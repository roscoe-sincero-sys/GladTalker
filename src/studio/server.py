"""FastAPI application for GladTalker Studio (issue #11, phase 1).

Endpoints (all JSON unless noted; interactive docs at ``/docs``):

    GET  /health                    liveness
    GET  /hardware                  compute device, GPUs, FFmpeg, model readiness
    GET  /models                    pretrained model files status
    POST /models/download           start (or join) a model download job
    GET  /voices                    voice presets (semitone pitch shifts)
    POST /uploads                   multipart image upload (for --web mode) -> server-side path
    POST /cast/detect               Magic Auto-Cast: faces + suggested regions for an image
    POST /tts/preview               audition a line/voice -> audio/wav
    POST /script/parse              paste-a-script importer + pronunciation lint
    POST /render/start              start a render job
    GET  /jobs, /jobs/{id}          job snapshots
    POST /jobs/{id}/cancel          cancel a queued/running job
    POST /jobs/{id}/ws-ticket       single-use WebSocket ticket (when a token is set)
    GET  /jobs/{id}/files/{kind}    finished outputs: video | srt | vtt
    WS   /ws/jobs/{id}              live job events (history replayed on connect)

Security (see src/studio/security.py): binds to 127.0.0.1 by default; rejects unexpected Host
headers (DNS rebinding) and foreign browser Origins before any work happens; enforces an
optional bearer token and request-size limits before bodies are parsed; WebSockets use
short-lived job tickets so the token never appears in URLs or logs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.studio import REPO_ROOT
from src.studio.audio import VOICE_PRESETS, TTSService
from src.studio.faces import FaceDetectorService, suggest_cast
from src.studio.ffmpeg import FFmpegError, find_ffmpeg
from src.studio.hardware import DeviceManager, download_models, hardware_report, models_status
from src.studio.jobs import TERMINAL_STATES, Job, JobManager, RetryLaterError, UserFacingError
from src.studio.previews import PreviewStore
from src.studio.schemas import (
    AudioPreviewRequest,
    CastDetectRequest,
    ModelsDownloadRequest,
    RenderRequest,
    ScriptLintRequest,
    ScriptParseRequest,
    TTSPreviewRequest,
)
from src.studio.script import lint_line, parse_script
from src.studio.security import (
    DEFAULT_BODY_LIMIT,
    StudioGuard,
    TicketStore,
    install_log_redaction,
    origin_allowed,
)

log = logging.getLogger("gladtalker.studio")

DEFAULT_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]
DEFAULT_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
FILE_KINDS = {"video": ("video_path", "video/mp4"), "srt": ("srt_path", "application/x-subrip"),
              "vtt": ("vtt_path", "text/vtt"), "audio": ("audio_path", "audio/wav")}
UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|jpeg|bmp|webp)$")
UI_DIST = REPO_ROOT / "studio-ui" / "dist"
PREVIEW_KEEP = 5  # finished audio previews kept on disk (older ones are deleted)
# The Studio UI only loads its own files; it talks to this engine (fetch + WebSocket) and shows
# uploaded images and rendered media through blob: URLs.
UI_HEADERS = [
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
     b"img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; "
     b"object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


def _version() -> str:
    try:
        from src.__version__ import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "0.0.0"


@dataclass
class StudioConfig:
    device: str = "auto"
    workspace: Path = REPO_ROOT / "results" / "studio"
    token: Optional[str] = None
    allowed_origins: list[str] = field(default_factory=lambda: list(DEFAULT_ORIGINS))
    allowed_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_HOSTS))
    detector_device: str = "cpu"
    max_upload_mb: int = 50


@dataclass
class StudioServices:
    devices: DeviceManager
    tts: TTSService
    detector: FaceDetectorService
    jobs: JobManager

    @classmethod
    def default(cls, config: StudioConfig) -> "StudioServices":
        return cls(
            devices=DeviceManager(config.device),
            tts=TTSService(),
            detector=FaceDetectorService(device=config.detector_device),
            jobs=JobManager(lanes={"gpu": 1, "io": 2, "audio": 1}),
        )


def create_app(config: Optional[StudioConfig] = None, services: Optional[StudioServices] = None) -> FastAPI:
    config = config or StudioConfig()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    # SadTalker and facexlib resolve weights relative to the working directory.
    os.chdir(REPO_ROOT)
    from src.utils.safe_io import sync_imageio_ffmpeg

    sync_imageio_ffmpeg()  # one FFmpeg for Studio, SadTalker's muxing, and ImageIO's writers
    services = services or StudioServices.default(config)
    workspace = Path(config.workspace)

    # Audio previews: this engine's own folder under the workspace (see src/studio/previews.py).
    previews = PreviewStore(workspace / "previews", keep=PREVIEW_KEEP)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        services.jobs.shutdown()
        previews.close()

    app = FastAPI(
        title="GladTalker Studio Engine",
        version=_version(),
        description="Local engine for GladTalker Studio: Cast → Script → Roll.",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.services = services
    app.state.previews = previews
    install_log_redaction()
    tickets = TicketStore()
    upload_limit = config.max_upload_mb * 1024 * 1024 + 64 * 1024  # file + multipart overhead

    serve_ui = (UI_DIST / "index.html").is_file()

    # Middleware runs outermost-last-added: Host check -> CORS (answers preflights) -> guard
    # (Origin, token, body size), all before FastAPI parses a request body.
    app.add_middleware(
        StudioGuard,
        allowed_origins=config.allowed_origins,
        token=config.token,
        body_limit_for=lambda path: upload_limit if path == "/uploads" else DEFAULT_BODY_LIMIT,
        public_paths=ui_public_paths(UI_DIST) if serve_ui else (),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-GladTalker-Token"],
        expose_headers=["Retry-After"],  # so browser clients can honour a retryable 503
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)

    @app.exception_handler(UserFacingError)
    async def _user_error(request: Request, exc: UserFacingError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RetryLaterError)
    async def _retry_later(request: Request, exc: RetryLaterError):
        return JSONResponse(status_code=503, content={"detail": str(exc), "retryable": True},
                            headers={"Retry-After": str(exc.retry_after_s)})

    @app.exception_handler(FFmpegError)
    async def _ffmpeg_error(request: Request, exc: FFmpegError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def job_or_404(job_id: str) -> Job:
        job = services.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job with id '{job_id}'.")
        return job

    # -- status ------------------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "name": "GladTalker Studio Engine", "version": _version()}

    @app.get("/hardware")
    def hardware() -> dict:
        report = hardware_report(services.devices)
        report["active_jobs"] = [j.snapshot() for j in services.jobs.active()]
        return report

    @app.get("/models")
    def models() -> dict:
        return models_status()

    @app.post("/models/download", status_code=202)
    def models_download(body: ModelsDownloadRequest = ModelsDownloadRequest()) -> dict:
        job, _ = services.jobs.submit_once(
            "models.download", "Download models",
            lambda ctx: download_models(ctx, REPO_ROOT, force=body.force), lane="io",
        )
        return job.snapshot()

    @app.get("/voices")
    def voices() -> dict:
        return {"presets": VOICE_PRESETS, "semitone_range": [-6.0, 6.0]}

    # -- Cast ----------------------------------------------------------------------------------
    @app.post("/uploads")
    async def upload(file: UploadFile = File(...)) -> dict:
        from src.studio.pipeline import read_image

        ext = Path(file.filename or "").suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            raise HTTPException(422, f"Unsupported file type “{ext or '?'}”. "
                                     f"Use one of: {', '.join(sorted(IMAGE_EXTENSIONS))}.")
        dest_dir = workspace / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{uuid.uuid4().hex}{ext}"
        limit = config.max_upload_mb * 1024 * 1024
        size = 0
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"Image is larger than {config.max_upload_mb} MB.")
                out.write(chunk)
        try:
            img = read_image(str(dest))
        except UserFacingError:
            dest.unlink(missing_ok=True)
            raise
        return {"image_path": str(dest), "width": img.shape[1], "height": img.shape[0],
                "original_name": file.filename}

    @app.post("/cast/detect")
    def cast_detect(body: CastDetectRequest) -> dict:
        from src.studio.pipeline import read_image, resolve_path

        t0 = time.perf_counter()
        path = resolve_path(body.image_path)
        image = read_image(str(path))
        faces = services.detector.detect(image, body.min_confidence, body.max_faces)
        cast = [c.to_dict() for c in suggest_cast(image, faces)]
        message = (f"Found {len(cast)} face{'s' if len(cast) != 1 else ''}." if cast else
                   "No faces found. Try a higher-resolution image, lower the detection sensitivity, "
                   "or add a region manually.")
        return {
            "image": {"path": str(path), "width": image.shape[1], "height": image.shape[0]},
            "cast": cast,
            "message": message,
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }

    @app.get("/uploads/{name}")
    def uploaded_image(name: str):
        """Serve an image previously stored by POST /uploads (only those files, by their
        generated names) so the UI can show the scene again after a reload."""
        if not UPLOAD_NAME_RE.match(name):
            raise HTTPException(404, "No such upload.")
        path = workspace / "uploads" / name
        if not path.is_file():
            raise HTTPException(404, "No such upload.")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})

    # -- Script --------------------------------------------------------------------------------
    @app.post("/script/parse")
    def script_parse(body: ScriptParseRequest) -> dict:
        return parse_script(body.text, body.cast).to_dict()

    @app.post("/script/lint")
    def script_lint(body: ScriptLintRequest) -> dict:
        """Pronunciation/quality warnings for individual lines (the editor re-checks as you type)."""
        from dataclasses import asdict

        return {"lines": [[asdict(w) for w in lint_line(text)] if text.strip() else [] for text in body.lines]}

    @app.post("/audio/preview", status_code=202)
    def audio_preview(body: AudioPreviewRequest) -> dict:
        """Start building the full soundtrack (voices + pacing) to listen to before rendering."""
        from src.studio import pipeline

        pipeline.require_ffmpeg_user_facing()

        def run(ctx):
            out_dir = previews.new_folder(ctx.job.id, services.jobs)
            return pipeline.preview_audio(ctx, body, out_dir, services.tts)

        return services.jobs.submit("audio.preview", "Preview audio mix", run, lane="audio").snapshot()

    @app.post("/tts/preview", response_class=Response,
              responses={200: {"content": {"audio/wav": {}}}})
    def tts_preview(body: TTSPreviewRequest) -> Response:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise FFmpegError("FFmpeg was not found, so voices can't be previewed. Install FFmpeg "
                              "and restart GladTalker Studio.")
        try:
            wav = services.tts.preview(body.text, body.semitones, body.tempo, ffmpeg=ffmpeg)
        except ImportError as exc:
            raise HTTPException(503, f"Text-to-speech isn't installed ({exc}). Install coqui-tts.") from exc
        return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # -- Roll ----------------------------------------------------------------------------------
    @app.post("/render/start", status_code=202)
    def render_start(body: RenderRequest) -> dict:
        from src.studio import pipeline

        # Fail fast with actionable messages before queueing a long job.
        pipeline.require_ffmpeg_user_facing()
        pipeline.check_models(quality_mode=body.quality.mode, size=body.quality.size)
        pipeline.validate_request(body, pipeline.read_image(str(pipeline.resolve_path(body.image_path))))

        def run(ctx):
            out_dir = workspace / "renders" / f"{time.strftime('%Y%m%d_%H%M%S')}_{ctx.job.id}"
            return pipeline.render(ctx, body, out_dir, services.tts, services.devices.resolve())

        speakers = ", ".join(m.name for m in body.cast)
        job = services.jobs.submit("render", f"Render ({speakers})", run, lane="gpu")
        return job.snapshot()

    # -- Jobs ----------------------------------------------------------------------------------
    @app.get("/jobs")
    def jobs_list() -> dict:
        return {"jobs": [j.snapshot() for j in services.jobs.list()]}

    @app.get("/jobs/{job_id}")
    def job_get(job_id: str) -> dict:
        return job_or_404(job_id).snapshot()

    @app.post("/jobs/{job_id}/cancel")
    def job_cancel(job_id: str) -> dict:
        job_or_404(job_id)
        return services.jobs.cancel(job_id).snapshot()

    @app.get("/jobs/{job_id}/files/{kind}")
    def job_file(job_id: str, kind: str):
        job = job_or_404(job_id)
        if kind not in FILE_KINDS:
            raise HTTPException(404, f"Unknown file kind '{kind}'. Use one of: {', '.join(FILE_KINDS)}.")
        key, media_type = FILE_KINDS[kind]
        path = (job.result or {}).get(key)
        if job.state != "succeeded" or not path or not Path(path).is_file():
            raise HTTPException(404, f"No {kind} output is available for job '{job_id}'.")
        resolved = Path(path).resolve()
        if workspace.resolve() not in resolved.parents:
            raise HTTPException(403, "Output is outside the Studio workspace.")
        return FileResponse(resolved, media_type=media_type, filename=resolved.name)

    @app.post("/jobs/{job_id}/ws-ticket")
    def job_ws_ticket(job_id: str) -> dict:
        """Single-use, 60-second ticket for ``/ws/jobs/{id}`` (needed when a token is set).
        Browsers can't send headers on WebSockets, and URLs end up in logs, so the long-lived
        token is never put in a WebSocket URL."""
        job_or_404(job_id)
        ticket = tickets.issue(job_id)
        return {"ticket": ticket, "expires_in": tickets.ttl_s,
                "url": f"/ws/jobs/{job_id}?ticket={ticket}"}

    @app.websocket("/ws/jobs/{job_id}")
    async def job_events(websocket: WebSocket, job_id: str):
        host = websocket.headers.get("host")
        if not origin_allowed(websocket.headers.get("origin"), host, config.allowed_origins):
            await websocket.close(code=1008)
            return
        if config.token and not tickets.redeem(websocket.query_params.get("ticket"), job_id):
            await websocket.close(code=1008)
            return
        job = services.jobs.get(job_id)
        if job is None:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        queue, history = services.jobs.subscribe(job_id, asyncio.get_running_loop())

        def is_terminal(event: dict) -> bool:
            return event["type"] == "state" and event["job"]["state"] in TERMINAL_STATES

        async def wait_for_disconnect() -> None:
            # Clients don't send anything; reading is how we notice they've gone away even
            # while the job is idle, so the subscription is released promptly.
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        disconnect = asyncio.ensure_future(wait_for_disconnect())
        next_event: Optional[asyncio.Future] = None
        try:
            # Replay history, then stream live events. The terminal event is in exactly one of
            # the two (subscribe() snapshots history atomically with registering the queue).
            for event in history:
                await websocket.send_json(event)
                if is_terminal(event):
                    return
            while True:
                next_event = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({next_event, disconnect}, return_when=asyncio.FIRST_COMPLETED)
                if disconnect in done:
                    return
                event = next_event.result()
                next_event = None
                await websocket.send_json(event)
                if is_terminal(event):
                    return
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            client_gone = disconnect.done()
            for task in (next_event, disconnect):
                if task is not None and not task.done():
                    task.cancel()
            if client_gone and not disconnect.cancelled():
                disconnect.exception()  # mark any receive error as retrieved
            services.jobs.unsubscribe(job_id, queue)
            if not client_gone:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass

    if serve_ui:
        # Mounted last so every API route above takes precedence.
        from fastapi.staticfiles import StaticFiles

        app.mount("/", _with_headers(StaticFiles(directory=UI_DIST, html=True), UI_HEADERS), name="ui")

    return app


def ui_public_paths(dist: Path) -> set[str]:
    """URL paths of the bundled UI's files (and ``/``), which load without the engine token."""
    paths = {"/"}
    for f in dist.rglob("*"):
        if f.is_file():
            paths.add("/" + f.relative_to(dist).as_posix())
    return paths


def _with_headers(asgi_app, headers):
    """Wrap an ASGI app so every response it sends carries ``headers``."""

    async def wrapped(scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *headers]}
            await send(message)

        await asgi_app(scope, receive, send_with_headers)

    return wrapped
