# Changelog

All notable changes to **GladTalker** (forked from OpenTalker/SadTalker `v0.0.2`) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For pre-fork SadTalker history (`v0.0.2` and earlier), see [docs/changlelog.md](docs/changlelog.md).

---

## [Unreleased]

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
