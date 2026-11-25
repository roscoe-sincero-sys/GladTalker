# Changelog

All notable changes to **GladTalker** (forked from OpenTalker/SadTalker `v0.0.2`) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For pre-fork SadTalker history (`v0.0.2` and earlier), see [docs/changlelog.md](docs/changlelog.md).

---

## [Unreleased]

### Added
- **GladTalker Studio desktop app (phase 3 of [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11))**: `studio-ui/src-tauri/`, a Tauri v2 shell around the Studio UI. On Windows it builds a 1.3 MB NSIS installer. See [studio-ui/README.md](studio-ui/README.md#desktop-app).
  - **Engine:** the app finds the GladTalker folder and its venv (a folder just chosen with a native dialog, `GLADTALKER_HOME`, the folder chosen in an earlier session, or the folders around the app). It starts `studio_server.py` on a free port with a fresh 256-bit token, passed in the environment rather than on the command line. It stops the engine's whole process tree on exit. A relative `GLADTALKER_STUDIO_WORKSPACE` is resolved once, against the app's working directory, for both the engine and the app's own checks. The engine is always bound to 127.0.0.1, and its ready line must confirm that. Start, restart, and folder changes are serialized. Debug builds also allow the dev server's origin, so `npm run tauri:dev` works. If something goes wrong, a setup screen says what's missing, shows the engine's last messages, and offers **Choose GladTalker folder…** and **Try again**.
  - **Native features:**
    - **Save project…** opens a Save dialog, and saves atomically, so a failed save never truncates the old file. Saving over a project keeps its access restrictions: on Windows the new contents get the file's DACL before they are written and `ReplaceFileW` swaps them in; elsewhere they get its permission bits and group.
    - **Show the video in its folder** works only for files inside the engine's workspace.
    - A notification fires when a render ends while the app is in the background. A render that had already ended before the app started following it (its history is replayed) doesn't notify, and checking that never holds up the video or audio.
    - Render progress appears on the taskbar icon.
  - **Security:** the page has a strict CSP and only the app's own commands, with no plugin permissions granted to it.
  - **Tests:** Rust unit tests cover engine discovery, the ready line, tokens, the workspace check, and atomic saving (including a Windows ACL check). Desktop end-to-end tests (`npm run e2e:desktop`) launch the real app, each run in its own temporary profile, and attach to its WebView2:
    - start → axe scan → upload, detect, and a real audio preview;
    - closing the app stops the engine;
    - killing the app still stops the engine.
- `studio_server.py --exit-with-parent PID`: the engine shuts down when the program that started it ends, even after a crash.
- `studio_server.py --open` (web mode): opens the Studio UI in your browser once the engine is ready. A token-protected engine instead prints how to open it with the token, which never goes into a URL handed to another program.
- **GladTalker Studio UI (phase 2 of [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11))**: `studio-ui/`, an accessible *Cast → Script → Roll* frontend (Svelte 5 + Vite) served by the engine at `/`. The built `dist/` is committed, so no Node is needed to use it. See [studio-ui/README.md](studio-ui/README.md).
  - **Cast:** scene upload, Auto-Detect Cast, and color + shape + number speaker badges. Regions are edited by drag, by Arrow/Alt+Arrow keys, through X/Y/W/H steppers, or with Snap to face / Expand to tile. Names, voice presets, pitch, and voice preview.
  - **Script:** paste-a-script import (adding unknown speakers to the cast), line editing with Play/Up/Down/Delete and Alt+↑/↓, live pronunciation lint with Auto-Fix, natural or target pacing, a full audio-mix preview with a pace meter and timeline, and a captions toggle.
  - **Roll:**
    - a checklist that jumps to the field to fix;
    - framing presets with fit/fill and a described preview, quality, and file name;
    - render progress by stage with ETA and Cancel;
    - a captioned player with playback speed, transcript, and downloads.
  - **Throughout:** autosave plus `.gladtalker.json` save/open, first-run setup (FFmpeg check, Download Models with progress), a Help dialog that stays in the same place, and live-region announcements.
  - **WCAG 2.2 A/AA design:**
    - native labelled controls, landmarks, and a skip link;
    - drag alternatives everywhere, ≥ 36 px targets, and a focus ring never hidden by sticky bars;
    - verified contrast in light and dark, forced-colors, reduced motion, and 320 px reflow.
- Engine endpoints for the UI: `GET /uploads/{name}`, `POST /script/lint`, and `POST /audio/preview` (a job that builds the render's exact soundtrack; WAV via `/jobs/{id}/files/audio`). UI responses carry a strict Content-Security-Policy and `nosniff`.
- UI robustness:
  - Async results (import, upload, detection, render start) are dropped if New/Open replaced the project meanwhile.
  - Renders and audio previews are started at project scope. A job the engine accepts after you've left the step is still saved, and the step follows it when shown again. Its button stays disabled while a request is on its way, and while a saved job's state is still being checked.
  - Saved job references are trusted only if they look like engine job ids (12 hex characters), both when a project loads and in the API client, so an edited project file can't steer requests. The audio-preview staleness check saves only a digest, never an old copy of the dialogue.
  - Lines over the 1000-character limit get no Auto-Fix (it would replace the whole line with a shortened one); they already ask to be split.
  - A preview's timing and audio are shown together, and only for the newest preview.
  - Auto-Fix suggestions belong to the exact text they were made for, and disappear as soon as the line changes.
  - A finished video is shown even if its captions fail to load, and the captions have their own retry.
  - Cancel's answer is only an acknowledgement; the job watcher alone sets the render's state, so a late answer can't bring back a render that has ended.
  - Job watchers stop on 401/403/404, back off on network errors, and close when their step closes.
  - `blob:` media URLs are released, and voice clips play one at a time.
  - The engine's limits (8 cast members, 500 lines, 1000 characters a line) are checked in the UI, and the checklist and audio preview say where to fix them.
  - Reordering keeps keyboard focus at the ends of the list.
  - Downloads keep their file names in token mode.
- Audio previews no longer pile up on disk. Each engine keeps its previews in its own locked folder: its previews whose job is gone are deleted, and its 5 newest finished ones are kept. Another engine's folder is reclaimed only with proof that Studio made it: an `engine-<id>` name, plus an existing ownership lock that is free because that engine has exited. Anything else under `previews/` is never deleted. So two engines can safely share one workspace, and two engines racing to reclaim the same folder both carry on.
- UI tests:
  - 66 Vitest unit/component tests, including axe-core checks and WCAG contrast checks of every theme token and badge.
  - Playwright end-to-end tests against a real engine:
    - axe WCAG 2.2 A/AA scans of every step in light and dark;
    - keyboard region editing, and line reordering with focus kept at the ends of the list;
    - a real audio-mix preview and autosave across reload;
    - a slow import racing an Open project;
    - stale Auto-Fix suggestions, a render accepted after leaving Roll, a slow re-attach to a saved render, a late Cancel acknowledgement, and a duplicate preview submission;
    - forged job ids in an opened project (no job request is made), and no removed dialogue in a saved project file;
    - the `#token=` launch against a token-protected engine, and a caption download failure in token mode;
    - Help dialog focus and 320 px reflow;
    - an opt-in real render.
- **GladTalker Studio engine (phase 1 of [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11))**: `studio_server.py` + `src/studio/`, a local FastAPI + WebSocket backend for the upcoming *Cast → Script → Roll* UI ([docs/studio_api.md](docs/studio_api.md)):
  - `/hardware` (device, every CUDA/ROCm/DirectML/MPS GPU, FFmpeg, model readiness) and `/models` + `/models/download` (download job with per-file byte progress).
  - `/cast/detect`: RetinaFace auto-cast tuned for stylized faces (permissive threshold + 2× pass), reading-order badges, and region suggestions that snap to video-call tiles / comic panels.
  - `/script/parse`: paste-a-script importer (`Name: line`, markdown/bullets, parentheticals, screenplay style, continuation lines) with pronunciation lint and one-click fixes; `/tts/preview` voice auditioning with semitone presets.
  - `/render/start`: generalizes the two-character recreate script to any cast. It supports natural pace or a target duration (≤2× tempo, with an actionable error if the script can't fit) and frame-locked per-speaker turns. Each character is rendered only while speaking. Output presets are 720p, 1080p, vertical, square and source, in fit or fill mode. Encoding is streamed H.264/AAC with `.srt`/`.vtt` captions.
  - Background job queue (one GPU lane) with live WebSocket progress, measured ETA, rate-limited events, and Cancel that stops SadTalker mid-render and cleans up intermediate files.
  - Security guard that runs before request bodies are parsed:
    - Rejects foreign browser origins (403) before any side effect, and unexpected Host headers.
    - Applies the optional bearer token to every route except `/health`.
    - Enforces streamed request-size limits (413).
    - The launcher refuses non-loopback binds without a token.
  - WebSockets authenticate with single-use, job-scoped tickets (`POST /jobs/{id}/ws-ticket`), and `token=`/`ticket=` values are redacted from server logs.
  - A memory-budget check refuses renders whose longest speaking part would not fit in RAM, with guidance on what to change.
  - The sidecar prints its `listening` line only once uvicorn is accepting connections.
- `scripts/download_models.py`: pinned size + SHA-256 for every model, confirmed against a fresh download of all eight upstream assets.
  - Downloads use unique temp files and are verified (length and checksum) before replacing anything.
  - Readiness, render admission, and skip/repair decisions all require a checksum match, so truncated and same-size damaged files are reported and repaired. Digests are cached per file, bound to the hashed file's own size, mtime, and file ID (read from the open handle and re-checked against the path), with a racy-timestamp guard.
  - A damaged file that can't be replaced is never reported as repaired.
- `src/utils/safe_io.py`: Unicode-safe `imread`/`imwrite` and FFmpeg resolution (`GLADTALKER_FFMPEG`).
- `tests/`: `unittest` suite (154 tests) for the Studio engine and the shared helpers it relies on. Run `python -m unittest` from the repository root, inside your GladTalker venv.

### Changed
- `inference.main()` now returns the generated video path and accepts `args.root_path` so it can be called from other entry points.
- `scripts/download_models.py`: `download_file()` accepts a `progress` callback and an `expected=(size, sha256)` check, and uses a 60 s network timeout.
- `src/utils/videoio.save_video_with_watermark()` runs FFmpeg with an argument list, honours `GLADTALKER_FFMPEG`, raises on failure instead of continuing silently, and writes its temp file beside the output instead of the working directory.
- `src/utils/paste_pic.py` writes its temp video beside the output and always releases and removes it, even when interrupted.
- GFPGAN enhancement (`--enhancer gfpgan`) now streams frames from disk through the restorer into the writer, and SadTalker frees its render buffers once they're written. Previously `imageio.mimsave` rejected the generator, so the fallback held every 2×-upscaled frame in RAM. Its bare `except` also re-ran enhancement after any error.
- When `GLADTALKER_FFMPEG` is set, Studio points ImageIO at the same FFmpeg (`IMAGEIO_FFMPEG_EXE`).
- The enhancement loop now closes its input decoder on every exit (finished, error, cancellation), and `animate.py` closes the enhancer alongside the writer. Cancelled renders no longer leave an intermediate video locked on Windows.
- Auto-cast verifies the RetinaFace weights against their pin before facexlib loads them. Previously facexlib used a damaged file as-is, or fetched a missing one without a checksum. Missing or damaged weights now get a Download Models message.
- TTS contraction normalization moved to `src/utils/tts_text.py` (re-exported from `text2speech.py`) so it can be used without loading coqui-tts.
- `requirements.txt`: added `fastapi`, `uvicorn`, `python-multipart`, and `websockets` for the Studio engine.

### Fixed
- DirectML device names no longer include a trailing NUL character in `describe_device()`.
- The audio pipeline closes every WAV file it opens (pydub left them open, which on Windows could keep temporary work folders from being deleted).
- Renders and audio previews without coqui-tts installed now fail with an install hint instead of "Something went wrong: No module named 'TTS'".
- `POST /audio/preview` trims cast names and rejects blank ones, the same as `POST /render/start`.
- Renders no longer fail when the image or workspace path contains non-ASCII characters on Windows (`preprocess.py` and `paste_pic.py` now use Unicode-safe image I/O).
- Log redaction keeps uvicorn's access-log arguments intact. Previously, redacting a request line broke the access formatter.
- The script importer recognises cast names longer than four words, and `**Name** (note):` lines. Previously such lines were silently merged into the previous speaker's line.
- Speaker names are matched ignoring letter case and spacing in both script import and render requests. Imported lines keep the cast's exact spelling (e.g. `Officer  Mia`).

---

## [0.1.0] - 2026-11-24

First modernized GladTalker release upgrading SadTalker from Python 3.8 / 2023 dependencies to Python 3.10–3.14, PyTorch 2.x, NumPy 2.x, Gradio 4+/6+, `spandrel` face enhancement, Idiap `coqui-tts`, and Windows AMD GPU support.

### Added
- **Unified GPU / AMD Windows support** (`src/utils/device.py`, `--device` CLI flag in `inference.py`) with automatic priority **CUDA / ROCm → DirectML (`torch-directml`) → Apple MPS → CPU**, discrete GPU auto-selection on dual-GPU Windows laptops (e.g., AMD Radeon RX 6550M), and `DML_DEVICE_INDEX` / `GLADTALKER_DEVICE` environment overrides ([#2](https://github.com/roscoe-sincero-sys/GladTalker/issues/2), [#7](https://github.com/roscoe-sincero-sys/GladTalker/pull/7)).
- **`SpandrelFaceRestorer`** (`src/utils/face_enhancer.py`) backed by `spandrel` + `facexlib`, enabling `--enhancer gfpgan` (`GFPGANv1.4.pth`) on Python 3.12–3.14 and PyTorch 2.x without requiring `basicsr` ([#2](https://github.com/roscoe-sincero-sys/GladTalker/issues/2), [#4](https://github.com/roscoe-sincero-sys/GladTalker/pull/4), [#6](https://github.com/roscoe-sincero-sys/GladTalker/pull/6)).
- **Cross-platform model downloader** (`scripts/download_models.py`) using only the Python standard library (skip-if-exists, atomic `.part` downloads, no `wget` required on Windows) ([#2](https://github.com/roscoe-sincero-sys/GladTalker/issues/2), [#4](https://github.com/roscoe-sincero-sys/GladTalker/pull/4), [#6](https://github.com/roscoe-sincero-sys/GladTalker/pull/6)).
- **Idiap `coqui-tts` integration** (`src/utils/text2speech.py`) replacing abandoned Coqui `TTS` with Idiap's maintained `coqui-tts` fork (`tts_models/en/ljspeech/tacotron2-DDC`, lazy-loaded, enabled on Windows as well as Linux/macOS) ([#2](https://github.com/roscoe-sincero-sys/GladTalker/issues/2), [#4](https://github.com/roscoe-sincero-sys/GladTalker/pull/4), [#6](https://github.com/roscoe-sincero-sys/GladTalker/pull/6)).
- **Versioning & changelog** (`VERSION`, `src/__version__.py`, `CHANGELOG.md`).

### Changed
- **Python 3.10–3.14 & modern dependency upgrade:** Replaced 2023 pins across `requirements.txt`, `req.txt`, `requirements3d.txt`, and `cog.yaml` with Python 3.14 / PyTorch 2.x / NumPy 2.x compatible requirements (adding `audioop-lts` for Python 3.13+, `soundfile`, `opencv-python`, `spandrel`, and `coqui-tts[codec]`) ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#6](https://github.com/roscoe-sincero-sys/GladTalker/pull/6)).
- **Launchers & documentation:** Updated `launcher.py`, `webui.bat`, `webui.sh`, `README.md`, and `docs/install.md` for Python 3.10+ (tested on 3.14), modern PyTorch, Windows AMD DirectML/ROCm setup, and `scripts/download_models.py` ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#7](https://github.com/roscoe-sincero-sys/GladTalker/pull/7)).
- **Gradio 4+/6+ WebUI migration:** Updated `app_sadtalker.py` (replaced removed `.style(...)` and `source="upload"` APIs) and `src/gradio_demo.py` (`shutil.copy2` to preserve UI previews, unified device helper) ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#4](https://github.com/roscoe-sincero-sys/GladTalker/pull/4), [#7](https://github.com/roscoe-sincero-sys/GladTalker/pull/7)).

### Fixed
- **NumPy 2.x compatibility:** Fixed `np.exceptions.VisibleDeprecationWarning`, ragged `trans_params` array creation, scalar `.item()` unpacking, `np.float` → `np.float64` in `my_awing_arch.py`, and deprecated `np.int`/`np.float` in `src/face3d/models/arcface_torch/` ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#2](https://github.com/roscoe-sincero-sys/GladTalker/issues/2), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#5](https://github.com/roscoe-sincero-sys/GladTalker/pull/5)).
- **PyTorch 2.6+ & DirectML checkpoint loading:** Added `weights_only=False` and `map_location='cpu'` before `.to(device)` across checkpoint loaders; replaced hardcoded `.cuda()` in `src/audio2pose_models/audio2pose.py` ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#7](https://github.com/roscoe-sincero-sys/GladTalker/pull/7)).
- **DirectML kernel & rank compatibility:** Added automatic `F.conv3d` CPU-fallback shim (DirectML only), flattened 6D spatial broadcasts/concatenations to ≤5D in `keypoint_detector.py`, `util.py`, and `dense_motion.py`, and kept GFPGANv1Clean StyleGAN2 weights on CPU under DirectML to avoid modulated-conv green-tint artifacts ([#7](https://github.com/roscoe-sincero-sys/GladTalker/pull/7)).
- **4 GB GPU VRAM OOM on long videos:** Moved each rendered batch prediction to CPU inside `src/facerender/modules/make_animation.py` (`out['prediction'].cpu()`) so GPU VRAM stays flat regardless of video length ([#8](https://github.com/roscoe-sincero-sys/GladTalker/pull/8)).
- **Pillow 10+, librosa 1.x, & `face_alignment` APIs:** Replaced removed `Image.ANTIALIAS` with `Image.Resampling.LANCZOS`, `librosa.core.load` → `librosa.load`, `librosa.output.write_wav` → `soundfile.write`, `LandmarksType._2D` → `TWO_D`, lazy-imported `face_enhancer`, and fixed invalid string `raise` statements ([#1](https://github.com/roscoe-sincero-sys/GladTalker/issues/1), [#3](https://github.com/roscoe-sincero-sys/GladTalker/pull/3), [#4](https://github.com/roscoe-sincero-sys/GladTalker/pull/4)).
- **TTS `'re` contraction pronunciation:** Normalized `you're` → `your`, `they're` → `their`, and `we're` → `where` in `src/utils/text2speech.py` so character-based `tacotron2-DDC` does not split them into two words ([#9](https://github.com/roscoe-sincero-sys/GladTalker/pull/9)).

---

## [0.0.2] - Upstream SadTalker Baseline

Forked from [OpenTalker/SadTalker](https://github.com/OpenTalker/SadTalker) (`v0.0.2`, Apache License 2.0) with fork attribution added in commit `bfd8081`.

[Unreleased]: https://github.com/roscoe-sincero-sys/GladTalker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/roscoe-sincero-sys/GladTalker/compare/v0.0.2...v0.1.0
[0.0.2]: https://github.com/roscoe-sincero-sys/GladTalker/releases/tag/v0.0.2
