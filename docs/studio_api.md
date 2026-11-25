# GladTalker Studio Engine API

`studio_server.py` is the local backend for **GladTalker Studio** (issue [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11), phase 1). The Studio frontend talks to it over HTTP and WebSocket, and the same API also works from scripts. It replaces the hand-written two-character render script described in #11 with one generalized engine:

- **Cast:** find every face in a scene and suggest a region per character, snapped to its video-call tile or comic panel when there is one.
- **Script:** turn a pasted `Name: line` transcript into dialogue blocks, with pronunciation lint and voice previews.
- **Roll:** render each character only while they speak. Voices use per-character pitch, and pacing is natural or fitted to a target duration. The scene is composited at any output size, encoded to H.264/AAC, and written with `.srt` / `.vtt` captions.

## Running

```bash
python studio_server.py                       # UI at http://127.0.0.1:7860, API docs at /docs
python studio_server.py --device dml:0        # auto | cpu | cuda[:N] | dml[:N] | mps
python studio_server.py --open                # web mode: also open the UI in your browser
python studio_server.py --port 0 --token XYZ --exit-with-parent PID
                                              # sidecar mode (the desktop app): free port,
                                              # bearer token, stops when process PID ends
```

`--exit-with-parent PID` makes the engine shut down when that process ends, even if it crashes or is killed. The engine first shuts down gracefully, and exits outright 15 seconds later. On Windows, a venv's `python.exe` is a launcher that starts the real interpreter as its own child, so stopping the launcher alone could leave the engine running. Watching the app's PID covers that case.

When `studio-ui/dist/` is present (it is committed), the engine also serves the Studio UI at `/`, with a strict Content-Security-Policy. API routes always take precedence. See [`studio-ui/README.md`](../studio-ui/README.md).

Once the server is accepting connections it prints one JSON line, for example `{"event": "listening", "host": "127.0.0.1", "port": 7860, ...}`. A parent process such as the Tauri shell can connect as soon as it reads that line. Renders and uploads go to `results/studio/` (change this with `--workspace`; paths with non-ASCII characters work).

Use the same environment as the CLI. For AMD GPUs on Windows that is `venv-dml` (Python 3.12 + `torch-directml`). The engine also needs `fastapi`, `uvicorn`, `python-multipart` and `websockets`, which are listed in `requirements.txt`.

### Security model

The engine reads image paths, writes files, and starts downloads on your machine. Every HTTP request is screened before its body is parsed:

- **Network:** the server binds to **127.0.0.1** by default. It refuses to start on any other address unless `--token` is set, and then you must name the address clients will use with `--allow-host`.
- **Host header:** unexpected `Host` headers are rejected (DNS-rebinding protection).
- **Origin:** browser requests from any origin other than the Tauri shell (`tauri://localhost`, `http(s)://tauri.localhost`) or the same origin are rejected with `403` before anything runs. CORS alone isn't enough, because it only hides the response while the side effect would still happen. Use `--allow-origin` to add origins.
- **Token:** with `--token` (or `GLADTALKER_STUDIO_TOKEN`), every route except `/health` needs `Authorization: Bearer <token>` or `X-GladTalker-Token: <token>`. The one exception is the bundled UI's own files (`/`, `/index.html`, and the files in `studio-ui/dist/`), which load with `GET`/`HEAD` and no token. A browser can't add a header to a page load, and the page has to load before it can read its `#token=` fragment. Those files are the same for every install and hold no user data. Uploads, job media, `/docs`, and every API route still need the token, and the Origin check still applies.
- **WebSockets:** browsers can't send headers on WebSockets, and URLs end up in logs, so the token is never put in a WebSocket URL. Instead, get a single-use, 60-second ticket for one job from `POST /jobs/{id}/ws-ticket` and connect to the `url` it returns (`/ws/jobs/{id}?ticket=…`). Without a token, no ticket is needed. `token=` and `ticket=` values are redacted from server logs.
- **Body size:** uploads are cut off at `max_upload_mb` (50 MB) as they stream in, and JSON bodies at 2 MB. Oversized requests get `413` without being spooled to disk.

## Endpoints

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness and version (no token needed). |
| `GET` | `/hardware` | Selected device (e.g. `DirectML:0 (AMD Radeon(TM) RX 6550M)`), every detected GPU, FFmpeg, model readiness, active jobs. |
| `GET` | `/models` | Status of the 8 pretrained files. A file is `ready` only if its size **and SHA-256** match the pinned values. Truncated files show `incomplete: true`; same-size files with bad contents show `damaged: true`. Each file is hashed once, so polling is cheap. The cached result is bound to the exact file that was hashed (size, modification time, and file ID, read from the open file), so a replaced file is always re-checked. |
| `POST` | `/models/download` | Starts a download job, or returns the one already running. Missing, incomplete, and damaged files are fetched and checked against pinned sizes and SHA-256 digests before they replace anything. The pins were confirmed by a fresh download of all eight files from their upstream release URLs. Body: `{"force": false}`. |
| `GET` | `/voices` | Voice presets: Neutral 0, Authoritative −1.5, Warm −0.5, Nervous +2.5 semitones (range −6…+6). |
| `POST` | `/uploads` | Multipart `file` upload for `--web` mode. Returns `{image_path, width, height}`. |
| `GET` | `/uploads/{name}` | Serves an uploaded image back (only files stored by `POST /uploads`, by their generated names), so the UI can show the scene after a reload. |
| `POST` | `/cast/detect` | `{image_path, min_confidence=0.2, max_faces=8}` → faces in reading order with a suggested `region` each. Needs only the face-detection model (`detection_Resnet50_Final.pth`), which is verified first. If it's missing or damaged, the response is `422` asking the user to choose Download Models. |
| `POST` | `/script/parse` | `{text, cast: [names]}` → dialogue lines, speakers, unknown speakers, lint warnings with an optional `fix`. Names from `cast` are recognised at any length, in any letter case, and with any spacing, and are returned exactly as the cast spells them. Without a cast, names of up to four words are detected. |
| `POST` | `/script/lint` | `{lines: [text]}` → per-line pronunciation/quality warnings (the editor re-checks as you type). |
| `POST` | `/tts/preview` | `{text, semitones=0, tempo=1}` → `audio/wav` (16 kHz mono). |
| `POST` | `/audio/preview` | `{cast: [{name, semitones}], script, pacing}` → `202` job that builds the exact soundtrack a render would use. Its result has `tempo`, `duration_s`, `frames`, and caption timings; the WAV is at `/jobs/{id}/files/audio`. |
| `POST` | `/render/start` | Render request (see below) → `202` job snapshot. |
| `GET` | `/jobs`, `/jobs/{id}` | Job snapshots. |
| `POST` | `/jobs/{id}/cancel` | Cancels a queued or running job. Running renders stop at the next frame batch, and intermediate files are removed. |
| `POST` | `/jobs/{id}/ws-ticket` | Single-use WebSocket ticket for that job (needed when a token is set). |
| `GET` | `/jobs/{id}/files/{video,srt,vtt,audio}` | Downloads the finished outputs (`audio` for audio previews). |
| `WS` | `/ws/jobs/{id}` | Live job events. Past events are replayed first, and the socket closes after the final state. |

Errors return `{"detail": "..."}` in plain language, for example *"Brain's region (X 900, …) extends outside the 977×580 image. Reduce W/H or move it so X+W ≤ 977 and Y+H ≤ 580."* Status codes: `422` for validation problems, `503` when FFmpeg or TTS is unavailable, `401` for a missing token, `403` for a foreign origin, `413` for an oversized body.

### Cast detection

The detector is RetinaFace (facexlib) running on CPU, so the GPU stays free for renders. It uses a permissive confidence floor (`min_confidence`, enforced exactly), plus an extra 2× upscaled pass for scenes under 1600 px, so stylized and illustrated faces are found. Each face gets a region:

- `region_source: "tile"`: the nearest long straight edges on all four sides form a panel around this face only.
- `region_source: "head"`: a head-and-shoulders box, used when there is no panel or two characters share one.

Rendering also retries face detection at lower confidence for stylized characters. That retry is limited to the render itself and never changes auto-cast results.

On a 977×580 four-person video-call screenshot with illustrated characters, detection returned all four participants with their tiles in about 4 s.

### Render request

```jsonc
{
  "image_path": "path/to/your_scene.png",   // absolute, or relative to the repository root
  "cast": [
    {"name": "Brain", "region": {"x": 758, "y": 38, "w": 205, "h": 113}, "semitones": -1.5},
    {"name": "Pinky", "region": {"x": 755, "y": 159, "w": 211, "h": 125}, "semitones": 2.5}
  ],
  "script": [
    {"speaker": "Brain", "text": "I'll show you how to change the time."},
    {"speaker": "Pinky", "text": "I'm clicking."}
  ],
  "pacing":  {"target_duration_s": 42.0, "gap_ms": 300},   // null target = natural pace
  "output":  {"preset": "hd720", "fit": "fit", "background": "#ffffff", "crf": 18, "filename": "phm"},
  "quality": {"mode": "standard", "expression_scale": 1.2, "batch_size": 2, "size": 256, "still": true},
  "captions": true
}
```

- `output.preset` is one of `hd720` (1280×720), `fhd1080`, `vertical1080` (1080×1920), `square1080` or `source`. With `fit` the whole scene is shown (letterbox or pillarbox). With `fill` the frame is covered and the scene is cropped.
- `quality.mode: "enhanced"` adds GFPGAN face restoration, which is much slower: about +2.6 s per frame on DirectML.
- Speakers are matched to cast names case-insensitively. Characters with no lines stay static.
- With a target duration, lines may play up to 2× faster to fit. If the script still doesn't fit, the job fails and the message says the shortest target that would work.
- **Memory:** SadTalker keeps a character's whole speaking part in RAM while rendering (about 2.3 MB per frame at 256 px and 9.4 MB at 512 px, at peak). Paste-back and GFPGAN enhancement stream one frame at a time, so the tile's full resolution only adds a fixed amount. Before rendering, the engine checks the most demanding part against free memory and fails early with suggestions (shorten or split the scene, use 256 px, use Standard) instead of exhausting RAM.

The job's `result` holds `video_path`, `srt_path`, `vtt_path`, `width`, `height`, `duration_s`, `frames`, `tempo`, `speaker_frames`, `timings`, and the caption cues. Each render folder also holds `project.json` (the request) and `result.json`.

### Job events

Every WebSocket message has this shape:

```jsonc
{
  "type": "state" | "progress" | "log",
  "seq": 12, "ts": 1764000000.0,
  "job": {
    "id": "349f6d5e6992", "kind": "render", "state": "queued|running|succeeded|failed|cancelled",
    "stage": "render:Brain", "stages": ["audio", "render:Brain", "render:Pinky", "compose"],
    "progress": 0.42, "message": "Rendering Brain: animating frame 120 of 591",
    "eta_s": 610.5, "elapsed_s": 300.2, "result": null, "error": null, "cancel_requested": false
  },
  // progress extras, when relevant: character, frame, frames, tempo, speaker_frames, bytes_done, bytes_total
}
```

- `progress` only increases.
- `eta_s` is measured from the actual render speed so far.
- Events are limited to about 4 per second, so a UI can forward `message` to an `aria-live="polite"` region without flooding screen readers.
- Renders run one at a time on a single GPU lane. Model downloads and audio previews each have their own lane, so neither waits behind a render. Only one download job can exist at a time.
- Audio previews are kept on disk only while they can still be fetched. Several engines can share one workspace, so each engine writes its previews into its own folder, `previews/engine-<id>/`, and holds an OS lock on it while it runs. Before each new preview, the engine:
  - deletes its own previews whose job it no longer knows (dropped from its history), and keeps its 5 newest finished ones;
  - deletes another engine's folder only with proof that Studio made it and its engine has exited. The name must be exactly `engine-<12 hex>`, its `.owner.lock` must exist and be over a minute old, and the lock must be free (the OS releases it even after a crash). Anything else under `previews/` is never touched.

  Queued and running previews are never touched, and an engine deletes its own folder when it shuts down cleanly. Renders are never deleted.

## Walkthrough (Python)

This needs your own scene image with at least two faces (a video-call screenshot or comic panel works well), because the repository's sample images are single portraits.

```python
import asyncio, json
import httpx, websockets

api = "http://127.0.0.1:7860"
img = "path/to/your_scene.png"
cast = httpx.post(f"{api}/cast/detect", json={"image_path": img}, timeout=60).json()["cast"]
script = httpx.post(f"{api}/script/parse", json={
    "text": "Brain: Click it.\nPinky: I'm clicking.", "cast": ["Brain", "Pinky"]}).json()

job = httpx.post(f"{api}/render/start", json={
    "image_path": img,
    "cast": [{"name": "Brain", "region": cast[0]["region"], "semitones": -1.5},
             {"name": "Pinky", "region": cast[1]["region"], "semitones": 2.5}],
    "script": [{"speaker": l["speaker"], "text": l["text"]} for l in script["lines"]],
}).json()

async def follow():
    # With --token, use the URL from POST /jobs/{id}/ws-ticket instead.
    async with websockets.connect(f"ws://127.0.0.1:7860/ws/jobs/{job['id']}") as ws:
        async for raw in ws:
            print(json.loads(raw)["job"]["message"])
asyncio.run(follow())
```

Measured on an AMD Radeon RX 6550M (DirectML), a two-character, three-line scene (5.8 s, 145 frames) rendered in 160 s: 1.7 s of audio, about 1.36 s per animated frame, and 0.6 s of compositing.

## Notes and limits (phase 1)

- Set `GLADTALKER_KEEP_WORK=1` to keep intermediate files (tiles, TTS lines, per-character SadTalker videos) in each render's `work/` folder.
- `GLADTALKER_FFMPEG` / `GLADTALKER_FFPROBE` override FFmpeg discovery. The override covers every FFmpeg call, including SadTalker's internal muxing and ImageIO's intermediate video writers (an explicit `IMAGEIO_FFMPEG_EXE` takes precedence for ImageIO).
- The first voice preview or render downloads the coqui-tts `tacotron2-DDC` voice (about 100 MB) into the coqui cache.
- Model download progress is reported per file. An interrupted download restarts that file; it does not resume.
- Verified on DirectML (AMD) and CPU. CUDA, ROCm, and MPS use the same code paths but haven't been exercised on real hardware yet.
- Burned-in subtitles and desktop packaging (Tauri, native dialogs and notifications) are later phases of #11. The Studio UI saves projects in the browser and as `.gladtalker.json` files.
