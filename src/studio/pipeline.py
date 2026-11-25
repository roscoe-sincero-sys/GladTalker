"""Roll: render a multi-character talking scene end to end.

Generalizes the hand-written render script described in issue #11 (two hard-coded characters at 1280x720)
to any cast, script, pacing, and output framing:

    audio  -> TTS + per-speaker pitch, frame-locked master and per-speaker turn tracks
    render -> SadTalker on each character's (upscaled) region, only for their speaking frames
    compose-> paste animated regions back onto the scene, encode H.264/AAC, write SRT/VTT captions
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np

from src.studio import REPO_ROOT
from src.studio.audio import FPS, DialogueTooLongError, TTSService, build_dialogue_audio
from src.studio.captions import to_srt, to_vtt
from src.studio.ffmpeg import NO_WINDOW, FFmpegError, require_ffmpeg
from src.studio.jobs import JobCancelled, JobContext, UserFacingError
from src.studio.schemas import OUTPUT_PRESETS, OutputSettings, RenderRequest

MIN_REGION_PX = 32
TILE_MIN_SIDE = 320  # upscale small regions so RetinaFace/SadTalker get enough face detail
TILE_MAX_UPSCALE = 4
REQUIRED_CHECKPOINTS = ("mapping_00109-model.pth.tar", "mapping_00229-model.pth.tar")

# Share of overall progress per phase.
P_AUDIO, P_RENDER, P_COMPOSE = 0.08, 0.84, 0.08

# Sub-progress within one character's SadTalker run, keyed by SadTalker's tqdm description.
_SUBSTEPS = {
    "standard": {"Face Renderer": (0.05, 0.90), "seamlessClone": (0.90, 1.0)},
    "enhanced": {"Face Renderer": (0.05, 0.38), "seamlessClone": (0.38, 0.42), "Face Enhancer": (0.42, 1.0)},
}


# --------------------------------------------------------------------------------------------
# Image & layout helpers (pure; unit-tested)
# --------------------------------------------------------------------------------------------

def read_image(path: str) -> np.ndarray:
    """cv2.imread that also handles non-ASCII Windows paths."""
    import cv2

    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError as exc:
        raise UserFacingError(f"Couldn't open the image “{path}”: {exc.strerror or exc}.") from exc
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise UserFacingError(
            f"“{Path(path).name}” isn't a readable image. Use a PNG, JPEG, BMP, or WebP file."
        )
    return img


def write_image(path: str, image: np.ndarray) -> None:
    """cv2.imwrite that also handles non-ASCII Windows paths."""
    from src.utils.safe_io import imwrite

    if not imwrite(path, image):
        raise RuntimeError(f"Could not encode image for {path}")


def resolve_path(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


# SadTalker keeps every rendered frame of a character in RAM as float32 RGB, then copies it while
# stacking and converting; ~3 copies are alive at the peak.
_RENDER_COPIES = 3
MEMORY_HEADROOM = 0.8  # use at most this share of currently available RAM


def estimate_render_bytes(frames: int, size: int) -> int:
    return int(frames) * size * size * 3 * 4 * _RENDER_COPIES


def check_memory_budget(speaker_frames: dict[str, int], size: int,
                        available_bytes: Optional[int] = None) -> None:
    """Refuse renders whose longest speaking part would not fit in memory (characters are
    rendered one at a time, so the peak is the largest single part)."""
    if not speaker_frames:
        return
    if available_bytes is None:
        try:
            import psutil

            available_bytes = psutil.virtual_memory().available
        except Exception:  # noqa: BLE001 - without psutil, skip the check rather than block renders
            return
    name, frames = max(speaker_frames.items(), key=lambda kv: kv[1])
    need = estimate_render_bytes(frames, size)
    budget = int(available_bytes * MEMORY_HEADROOM)
    if need > budget:
        gib = 1024 ** 3
        hint = " or switch to 256 px quality" if size > 256 else ""
        raise UserFacingError(
            f"{name} speaks for {frames / FPS:.0f}s, which needs about {need / gib:.1f} GB of memory to "
            f"render at {size} px, but only {available_bytes / gib:.1f} GB is free. Shorten {name}'s "
            f"lines, split the scene into shorter parts{hint}, or close other applications."
        )


@dataclass
class CanvasLayout:
    width: int
    height: int
    scale: float
    offset_x: int
    offset_y: int

    def map_box(self, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = box
        return (
            self.offset_x + int(round(x1 * self.scale)),
            self.offset_y + int(round(y1 * self.scale)),
            self.offset_x + int(round(x2 * self.scale)),
            self.offset_y + int(round(y2 * self.scale)),
        )


def compute_layout(src_w: int, src_h: int, output: OutputSettings) -> CanvasLayout:
    size = OUTPUT_PRESETS[output.preset]
    if size is None:  # match source (rounded up to even dimensions for yuv420p)
        return CanvasLayout(src_w + src_w % 2, src_h + src_h % 2, 1.0, 0, 0)
    out_w, out_h = size
    pick = min if output.fit == "fit" else max
    scale = pick(out_w / src_w, out_h / src_h)
    scaled_w, scaled_h = int(round(src_w * scale)), int(round(src_h * scale))
    return CanvasLayout(out_w, out_h, scale, (out_w - scaled_w) // 2, (out_h - scaled_h) // 2)


def paste_clipped(canvas: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    """Paste ``patch`` with its top-left at (x, y), clipping whatever falls outside ``canvas``."""
    ch, cw = canvas.shape[:2]
    ph, pw = patch.shape[:2]
    x1, y1, x2, y2 = max(0, x), max(0, y), min(cw, x + pw), min(ch, y + ph)
    if x1 >= x2 or y1 >= y2:
        return
    canvas[y1:y2, x1:x2] = patch[y1 - y : y2 - y, x1 - x : x2 - x]


def hex_to_bgr(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return (b, g, r)


def upscale_factor(w: int, h: int) -> int:
    return int(min(TILE_MAX_UPSCALE, max(1, math.ceil(TILE_MIN_SIDE / max(1, min(w, h))))))


class SequentialFrames:
    """Reads a video forward on demand; frame indices must be non-decreasing. Past the end it
    keeps returning the last frame (SadTalker can emit a frame or two fewer than requested)."""

    def __init__(self, path: str):
        import cv2

        self._cap = cv2.VideoCapture(path)
        self._index = -1
        self._frame: Optional[np.ndarray] = None
        self._eof = False

    def get(self, index: int) -> Optional[np.ndarray]:
        while self._index < index and not self._eof:
            ok, frame = self._cap.read()
            if not ok:
                self._eof = True
                break
            self._frame, self._index = frame, self._index + 1
        return self._frame

    def close(self) -> None:
        self._cap.release()


# --------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------

def validate_request(req: RenderRequest, image: np.ndarray) -> None:
    h, w = image.shape[:2]
    for member in req.cast:
        r = member.region
        if r.w < MIN_REGION_PX or r.h < MIN_REGION_PX:
            raise UserFacingError(
                f"{member.name}'s region is only {r.w}×{r.h} px. Make it at least "
                f"{MIN_REGION_PX}×{MIN_REGION_PX} px so the whole face fits (try Expand to Tile)."
            )
        if r.x + r.w > w or r.y + r.h > h:
            raise UserFacingError(
                f"{member.name}'s region (X {r.x}, Y {r.y}, W {r.w}, H {r.h}) extends outside the "
                f"{w}×{h} image. Reduce W/H or move it so X+W ≤ {w} and Y+H ≤ {h}."
            )


def check_models(root: Path = REPO_ROOT, quality_mode: str = "standard", size: int = 256) -> None:
    ckpt = root / "checkpoints"
    needed = [ckpt / f"SadTalker_V0.0.2_{size}.safetensors", *(ckpt / n for n in REQUIRED_CHECKPOINTS)]
    weights = root / "gfpgan" / "weights"
    needed += [weights / "alignment_WFLW_4HG.pth", weights / "detection_Resnet50_Final.pth"]
    if quality_mode == "enhanced":
        needed += [weights / "GFPGANv1.4.pth", weights / "parsing_parsenet.pth"]
    from scripts.download_models import expected_for, is_complete

    missing = [p.name for p in needed if not p.is_file()]
    broken = [p.name for p in needed
              if p.is_file() and not is_complete(p, expected_for(p.relative_to(root).as_posix()))]
    if missing or broken:
        parts = []
        if missing:
            parts.append(f"not downloaded yet: {', '.join(missing)}")
        if broken:
            parts.append(f"incomplete or damaged: {', '.join(broken)}")
        raise UserFacingError(f"Some models are {'; '.join(parts)}. Choose Download Models first.")


# --------------------------------------------------------------------------------------------
# Render job
# --------------------------------------------------------------------------------------------

class _Progress:
    """Maps phase-local progress onto overall progress, computes a live ETA, and rate-limits
    events (fast loops such as seamlessClone would otherwise flood the WebSocket and any
    screen reader announcing them). Cancellation is still checked on every call."""

    MIN_INTERVAL_S = 0.25

    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self._t0: Optional[float] = None
        self._p0 = 0.0
        self._last_emit = 0.0
        self._last_stage: Optional[str] = None

    def update(self, overall: float, message: str, stage: Optional[str] = None, force: bool = False,
               **extra) -> None:
        self.ctx.check_cancelled()
        now = time.monotonic()
        if not (force or stage != self._last_stage or now - self._last_emit >= self.MIN_INTERVAL_S):
            return
        self._last_emit, self._last_stage = now, stage
        eta = None
        if self._t0 is None and overall >= P_AUDIO:
            self._t0, self._p0 = now, overall
        if self._t0 is not None and overall - self._p0 > 0.02:
            rate = (overall - self._p0) / (now - self._t0)
            eta = (1.0 - overall) / rate if rate > 0 else None
        self.ctx.progress(overall, message, stage=stage, eta_s=eta, **extra)


def _sadtalker_args(tile_path: str, audio_path: str, out_dir: str, device, req: RenderRequest) -> SimpleNamespace:
    q = req.quality
    return SimpleNamespace(
        driven_audio=audio_path, source_image=tile_path, ref_eyeblink=None, ref_pose=None,
        checkpoint_dir=str(REPO_ROOT / "checkpoints"), result_dir=out_dir, root_path=str(REPO_ROOT),
        pose_style=0, batch_size=q.batch_size, size=q.size, expression_scale=q.expression_scale,
        input_yaw=None, input_pitch=None, input_roll=None,
        enhancer="gfpgan" if q.mode == "enhanced" else None, background_enhancer=None,
        cpu=(device.type == "cpu"), face3dvis=False, still=q.still, preprocess="full",
        verbose=False, old_version=False, net_recon="resnet50", init_path=None, use_last_fc=False,
        bfm_folder=str(REPO_ROOT / "checkpoints" / "BFM_Fitting"), bfm_model="BFM_model_front.mat",
        focal=1015.0, center=112.0, camera_d=10.0, z_near=5.0, z_far=15.0, device=device,
    )


def render(ctx: JobContext, req: RenderRequest, out_dir: Path, tts: TTSService, device) -> dict:
    """Job body for ``POST /render/start``."""
    import cv2

    t_start = time.perf_counter()
    ffmpeg = require_ffmpeg_user_facing()
    check_models(quality_mode=req.quality.mode, size=req.quality.size)
    image_path = resolve_path(req.image_path)
    image = read_image(str(image_path))
    validate_request(req, image)
    if device is None:
        raise UserFacingError("No compute device is available. Check the Hardware panel for details.")

    speakers_in_script = [m.name for m in req.cast if any(l.speaker == m.name for l in req.script)]
    stage_names = ["audio", *(f"render:{name}" for name in speakers_in_script), "compose"]
    ctx.set_stages(stage_names)
    prog = _Progress(ctx)

    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "work"
    work_dir.mkdir(exist_ok=True)
    (out_dir / "project.json").write_text(req.model_dump_json(indent=2), encoding="utf-8")
    timings: dict = {}

    try:
        # ---- 1. Audio ------------------------------------------------------------------------
        t0 = time.perf_counter()
        n_lines = len(req.script)
        prog.update(0.0, f"Synthesizing voices (line 1 of {n_lines})…", stage="audio")

        def on_line(done: int, total: int) -> None:
            prog.update(P_AUDIO * 0.8 * done / total,
                        f"Synthesizing voices (line {min(done + 1, total)} of {total})…", stage="audio")

        target_ms = int(round(req.pacing.target_duration_s * 1000)) if req.pacing.target_duration_s else None
        try:
            audio = build_dialogue_audio(
                [(l.speaker, l.text) for l in req.script],
                {m.name: m.semitones for m in req.cast},
                tts, str(work_dir / "audio"), target_ms=target_ms, gap_ms=req.pacing.gap_ms,
                ffmpeg=ffmpeg, on_line=on_line,
            )
        except DialogueTooLongError as exc:
            raise UserFacingError(str(exc)) from exc
        plan = audio.plan
        timings["audio_s"] = round(time.perf_counter() - t0, 2)
        frame_summary = " + ".join(f"{plan.speaker_frames.get(n, 0)} {n} frames" for n in speakers_in_script)
        prog.update(P_AUDIO, f"Audio ready: {plan.total_ms / 1000:.1f}s at {audio.tempo:.2f}× "
                             f"({frame_summary}).", stage="audio", force=True,
                    tempo=round(audio.tempo, 4), speaker_frames=plan.speaker_frames,
                    total_frames=plan.total_frames)

        # ---- 2. SadTalker per character ------------------------------------------------------
        check_memory_budget({n: plan.speaker_frames.get(n, 0) for n in speakers_in_script}, req.quality.size)

        import inference
        from src.studio.faces import retinaface_fallback
        from src.studio.progress import report_progress

        total_render_frames = sum(plan.speaker_frames.get(n, 0) for n in speakers_in_script) or 1
        substeps = _SUBSTEPS[req.quality.mode]
        renders: dict[str, str] = {}
        render_times: dict[str, float] = {}
        done_frames = 0

        for member in req.cast:
            name = member.name
            frames = plan.speaker_frames.get(name, 0)
            if name not in audio.speaker_wavs or frames <= 0:
                continue
            stage = f"render:{name}"
            base = P_AUDIO + P_RENDER * done_frames / total_render_frames
            share = P_RENDER * frames / total_render_frames

            x1, y1, x2, y2 = member.region.as_box()
            tile = image[y1:y2, x1:x2]
            k = upscale_factor(tile.shape[1], tile.shape[0])
            if k > 1:
                tile = cv2.resize(tile, (tile.shape[1] * k, tile.shape[0] * k), interpolation=cv2.INTER_CUBIC)
            safe = f"cast{req.cast.index(member):02d}"
            tile_path = str(work_dir / f"{safe}_tile.png")
            write_image(tile_path, tile)

            prog.update(base, f"Rendering {name}: preparing face ({frames} frames)…", stage=stage)

            def hook(desc: str, done: int, total: Optional[int], name=name, frames=frames,
                     base=base, share=share, stage=stage) -> None:
                ctx.check_cancelled()
                span = substeps.get(desc)
                if span is None or not total:
                    return
                frac = span[0] + (span[1] - span[0]) * done / total
                shown = min(frames, int(round(frames * done / total)))
                verb = {"Face Renderer": "animating", "Face Enhancer": "enhancing",
                        "seamlessClone": "blending"}.get(desc, desc)
                prog.update(base + share * frac, f"Rendering {name}: {verb} frame {shown} of {frames}",
                            stage=stage, force=(done == total), character=name, frame=shown, frames=frames)

            t_r = time.perf_counter()
            with report_progress(hook), retinaface_fallback():
                video = inference.main(_sadtalker_args(
                    tile_path, audio.speaker_wavs[name], str(work_dir / f"{safe}_sadtalker"), device, req
                ))
            if not video or not os.path.isfile(video):
                raise UserFacingError(
                    f"No face was detected in {name}'s region. Try Expand to Tile so the whole head "
                    "is inside the box, or choose a clearer, front-facing image."
                )
            renders[name] = video
            render_times[name] = round(time.perf_counter() - t_r, 2)
            done_frames += frames
            from src.utils.device import empty_cache

            empty_cache(device)
        timings["render_s"] = render_times

        # ---- 3. Composite + encode -----------------------------------------------------------
        t0 = time.perf_counter()
        prog.update(P_AUDIO + P_RENDER, "Compositing the final video…", stage="compose")
        layout = compute_layout(image.shape[1], image.shape[0], req.output)
        canvas = np.empty((layout.height, layout.width, 3), dtype=np.uint8)
        canvas[:] = hex_to_bgr(req.output.background)
        scaled = cv2.resize(
            image,
            (int(round(image.shape[1] * layout.scale)), int(round(image.shape[0] * layout.scale))),
            interpolation=cv2.INTER_LANCZOS4 if layout.scale >= 1 else cv2.INTER_AREA,
        ) if layout.scale != 1.0 else image
        paste_clipped(canvas, scaled, layout.offset_x, layout.offset_y)

        dst = {m.name: layout.map_box(m.region.as_box()) for m in req.cast}
        stem = req.output.filename or f"gladtalker_{time.strftime('%Y%m%d_%H%M%S')}"
        video_path = out_dir / f"{stem}.mp4"
        readers: dict[str, SequentialFrames] = {}
        try:
            for name, path in renders.items():
                readers[name] = SequentialFrames(path)
            encode_frames(ctx, prog, plan, canvas, dst, readers, str(audio.master_wav), str(video_path),
                          req.output.crf, ffmpeg)
        finally:
            # Release file handles before the work directory is deleted (Windows locks open files).
            for r in readers.values():
                r.close()
        timings["compose_s"] = round(time.perf_counter() - t0, 2)

        result = {
            "video_path": str(video_path),
            "width": layout.width,
            "height": layout.height,
            "fps": FPS,
            "frames": plan.total_frames,
            "duration_s": round(plan.total_ms / 1000.0, 3),
            "tempo": round(audio.tempo, 4),
            "speaker_frames": plan.speaker_frames,
            "output_dir": str(out_dir),
            "timings": timings,
        }
        if req.captions:
            srt, vtt = out_dir / f"{stem}.srt", out_dir / f"{stem}.vtt"
            srt.write_text(to_srt(plan.spans), encoding="utf-8")
            vtt.write_text(to_vtt(plan.spans), encoding="utf-8")
            result.update(srt_path=str(srt), vtt_path=str(vtt))
        result["captions"] = [
            {"speaker": s.speaker, "text": s.text, "start_ms": s.start_ms, "end_ms": s.end_ms} for s in plan.spans
        ]
        timings["total_s"] = round(time.perf_counter() - t_start, 2)
        (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        if not os.environ.get("GLADTALKER_KEEP_WORK"):
            shutil.rmtree(work_dir, ignore_errors=True)


def require_ffmpeg_user_facing() -> str:
    try:
        return require_ffmpeg()
    except FFmpegError as exc:
        raise UserFacingError(str(exc)) from exc


def encode_frames(ctx: JobContext, prog: _Progress, plan, canvas: np.ndarray, dst: dict,
                  readers: dict, master_wav: str, video_path: str, crf: int, ffmpeg: str) -> None:
    """Stream composited BGR frames into FFmpeg (H.264 + AAC), muxing the master dialogue track."""
    import cv2

    h, w = canvas.shape[:2]
    log_path = video_path + ".ffmpeg.log"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(plan.fps), "-i", "-",
        "-i", master_wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{plan.total_frames / plan.fps:.3f}",
        "-movflags", "+faststart", video_path,
    ]
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, creationflags=NO_WINDOW)
        try:
            last = 0.0
            for i, owner in enumerate(plan.frame_owner):
                frame = canvas
                if owner is not None and owner[0] in readers:
                    name, local = owner
                    src = readers[name].get(local)
                    if src is not None:
                        x1, y1, x2, y2 = dst[name]
                        frame = canvas.copy()
                        paste_clipped(frame, cv2.resize(src, (x2 - x1, y2 - y1), interpolation=cv2.INTER_AREA), x1, y1)
                proc.stdin.write(np.ascontiguousarray(frame).tobytes())
                now = time.monotonic()
                if now - last > 0.25 or i + 1 == plan.total_frames:
                    last = now
                    prog.update(P_AUDIO + P_RENDER + P_COMPOSE * 0.95 * (i + 1) / plan.total_frames,
                                f"Compositing frame {i + 1} of {plan.total_frames}…", stage="compose")
            proc.stdin.close()
            code = proc.wait()
        except (JobCancelled, BrokenPipeError, OSError):
            proc.kill()
            proc.wait()
            if ctx.cancelled:
                _remove_quietly(video_path, log_path)
                raise JobCancelled()
            code = proc.returncode
        except BaseException:
            proc.kill()
            proc.wait()
            _remove_quietly(video_path)
            raise
    tail = Path(log_path).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
    if code != 0:
        _remove_quietly(video_path)  # never leave a truncated MP4 that looks like a result
        raise FFmpegError(f"FFmpeg failed while encoding the video (exit {code}): {' '.join(tail)}")
    os.remove(log_path)


def _remove_quietly(*paths: str) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass
