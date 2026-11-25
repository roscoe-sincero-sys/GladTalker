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
    GET  /jobs/{id}/files/{kind}    finished outputs: video | srt | vtt
    WS   /ws/jobs/{id}              live job events (history replayed on connect)

Security: the server binds to 127.0.0.1 by default, rejects unexpected Host headers (DNS
rebinding), only allows CORS/WebSocket origins from the Studio shell, and can require a bearer
token (``--token`` / ``GLADTALKER_STUDIO_TOKEN``) that the desktop shell passes to its sidecar.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.studio import REPO_ROOT
from src.studio.audio import VOICE_PRESETS, TTSService
from src.studio.faces import FaceDetectorService, suggest_cast
from src.studio.ffmpeg import FFmpegError, find_ffmpeg
from src.studio.hardware import DeviceManager, download_models, hardware_report, models_status
from src.studio.jobs import TERMINAL_STATES, Job, JobManager, UserFacingError
from src.studio.schemas import (
    CastDetectRequest,
    ModelsDownloadRequest,
    RenderRequest,
    ScriptParseRequest,
    TTSPreviewRequest,
)
from src.studio.script import parse_script

log = logging.getLogger("gladtalker.studio")

DEFAULT_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]
DEFAULT_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
FILE_KINDS = {"video": ("video_path", "video/mp4"), "srt": ("srt_path", "application/x-subrip"),
              "vtt": ("vtt_path", "text/vtt")}


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
            jobs=JobManager(lanes={"gpu": 1, "io": 2}),
        )


def _origin_allowed(origin: Optional[str], host: Optional[str], allowed: list[str]) -> bool:
    if not origin:
        return True  # non-browser clients (the desktop shell, curl, tests) send no Origin
    if "*" in allowed or origin in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc == host  # same-origin (--web mode)


def create_app(config: Optional[StudioConfig] = None, services: Optional[StudioServices] = None) -> FastAPI:
    config = config or StudioConfig()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    # SadTalker and facexlib resolve weights relative to the working directory.
    os.chdir(REPO_ROOT)
    services = services or StudioServices.default(config)
    workspace = Path(config.workspace)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        services.jobs.shutdown()

    app = FastAPI(
        title="GladTalker Studio Engine",
        version=_version(),
        description="Local engine for GladTalker Studio: Cast → Script → Roll.",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.services = services
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-GladTalker-Token"],
    )

    def require_token(request: Request) -> None:
        if not config.token:
            return
        supplied = request.headers.get("x-gladtalker-token") or ""
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not hmac.compare_digest(supplied.encode(), config.token.encode()):
            raise HTTPException(status_code=401, detail="Missing or invalid Studio token.")

    auth = [Depends(require_token)]

    @app.exception_handler(UserFacingError)
    async def _user_error(request: Request, exc: UserFacingError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

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

    @app.get("/hardware", dependencies=auth)
    def hardware() -> dict:
        report = hardware_report(services.devices)
        report["active_jobs"] = [j.snapshot() for j in services.jobs.active()]
        return report

    @app.get("/models", dependencies=auth)
    def models() -> dict:
        return models_status()

    @app.post("/models/download", status_code=202, dependencies=auth)
    def models_download(body: ModelsDownloadRequest = ModelsDownloadRequest()) -> dict:
        running = services.jobs.active(kind="models.download")
        if running:
            return running[0].snapshot()
        job = services.jobs.submit(
            "models.download", "Download models",
            lambda ctx: download_models(ctx, REPO_ROOT, force=body.force), lane="io",
        )
        return job.snapshot()

    @app.get("/voices")
    def voices() -> dict:
        return {"presets": VOICE_PRESETS, "semitone_range": [-6.0, 6.0]}

    # -- Cast ----------------------------------------------------------------------------------
    @app.post("/uploads", dependencies=auth)
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

    @app.post("/cast/detect", dependencies=auth)
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

    # -- Script --------------------------------------------------------------------------------
    @app.post("/script/parse", dependencies=auth)
    def script_parse(body: ScriptParseRequest) -> dict:
        return parse_script(body.text, body.cast).to_dict()

    @app.post("/tts/preview", dependencies=auth, response_class=Response,
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
    @app.post("/render/start", status_code=202, dependencies=auth)
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
    @app.get("/jobs", dependencies=auth)
    def jobs_list() -> dict:
        return {"jobs": [j.snapshot() for j in services.jobs.list()]}

    @app.get("/jobs/{job_id}", dependencies=auth)
    def job_get(job_id: str) -> dict:
        return job_or_404(job_id).snapshot()

    @app.post("/jobs/{job_id}/cancel", dependencies=auth)
    def job_cancel(job_id: str) -> dict:
        job_or_404(job_id)
        return services.jobs.cancel(job_id).snapshot()

    @app.get("/jobs/{job_id}/files/{kind}", dependencies=auth)
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

    @app.websocket("/ws/jobs/{job_id}")
    async def job_events(websocket: WebSocket, job_id: str):
        host = websocket.headers.get("host")
        if not _origin_allowed(websocket.headers.get("origin"), host, config.allowed_origins):
            await websocket.close(code=1008)
            return
        if config.token:
            supplied = websocket.query_params.get("token") or ""
            if not hmac.compare_digest(supplied.encode(), config.token.encode()):
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

        try:
            # Replay history, then stream live events. The terminal event is in exactly one of
            # the two (subscribe() snapshots history atomically with registering the queue).
            for event in history:
                await websocket.send_json(event)
                if is_terminal(event):
                    return
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if is_terminal(event):
                    return
        except WebSocketDisconnect:
            pass
        finally:
            services.jobs.unsubscribe(job_id, queue)
            try:
                await websocket.close()
            except RuntimeError:
                pass

    return app
