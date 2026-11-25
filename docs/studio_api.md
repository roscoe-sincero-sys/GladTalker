# GladTalker Studio Engine API

`studio_server.py` is the local backend for **GladTalker Studio** (issue [#11](https://github.com/roscoe-sincero-sys/GladTalker/issues/11), phase 1). The Studio frontend talks to it over HTTP and WebSocket, and the same API also works from scripts. It replaces the hand-written pipeline in [`results/RECREATE_GoogleMeet_PHM_42s.md`](../results/RECREATE_GoogleMeet_PHM_42s.md) with one generalized engine:

- **Cast:** find every face in a scene and suggest a region per character, snapped to its video-call tile or comic panel when there is one.
- **Script:** turn a pasted `Name: line` transcript into dialogue blocks, with pronunciation lint and voice previews.
- **Roll:** render each character only while they speak. Voices use per-character pitch, and pacing is natural or fitted to a target duration. The scene is composited at any output size, encoded to H.264/AAC, and written with `.srt` / `.vtt` captions.

## Running

```bash
python studio_server.py                       # http://127.0.0.1:7860, docs at /docs
python studio_server.py --device dml:0        # auto | cpu | cuda[:N] | dml[:N] | mps
python studio_server.py --port 0 --token XYZ  # sidecar mode: free port + bearer token
```

On startup the server prints one JSON line, for example `{"event": "listening", "host": "127.0.0.1", "port": 7860, ...}`. A parent process such as the Tauri shell can read the port from it. Renders and uploads go to `results/studio/` (change this with `--workspace`).

Use the same environment as the CLI. For AMD GPUs on Windows that is `venv-dml` (Python 3.12 + `torch-directml`). The engine also needs `fastapi`, `uvicorn`, `python-multipart` and `websockets`, which are listed in `requirements.txt`.

### Security model

The engine reads image paths and writes files on your machine, so it is locked down by default:

- It binds to **127.0.0.1** only. If you pass `--host 0.0.0.0`, you must also set `--token`.
- Unexpected `Host` headers are rejected (DNS-rebinding protection). Use `--allow-host` to add one.
- CORS and WebSocket origins are limited to the Tauri shell (`tauri://localhost`, `http(s)://tauri.localhost`) and same-origin pages. Use `--allow-origin` to add more.
- With `--token` (or `GLADTALKER_STUDIO_TOKEN`), every endpoint except `/health` needs `Authorization: Bearer <token>` or `X-GladTalker-Token: <token>`. WebSockets take it as `?token=<token>`.

## Endpoints

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness and version (no token needed). |
| `GET` | `/hardware` | Selected device (e.g. `DirectML:0 (AMD Radeon(TM) RX 6550M)`), every detected GPU, FFmpeg, model readiness, active jobs. |
| `GET` | `/models` | Status of the 8 pretrained files (`checkpoints/`, `gfpgan/weights/`). |
| `POST` | `/models/download` | Starts a download job, or returns the one already running. Body: `{"force": false}`. |
| `GET` | `/voices` | Voice presets: Neutral 0, Authoritative −1.5, Warm −0.5, Nervous +2.5 semitones (range −6…+6). |
| `POST` | `/uploads` | Multipart `file` upload for `--web` mode. Returns `{image_path, width, height}`. |
| `POST` | `/cast/detect` | `{image_path, min_confidence=0.2, max_faces=8}` → faces in reading order with a suggested `region` each. |
| `POST` | `/script/parse` | `{text, cast: [names]}` → dialogue lines, speakers, unknown speakers, lint warnings with an optional `fix`. |
| `POST` | `/tts/preview` | `{text, semitones=0, tempo=1}` → `audio/wav` (16 kHz mono). |
| `POST` | `/render/start` | Render request (see below) → `202` job snapshot. |
| `GET` | `/jobs`, `/jobs/{id}` | Job snapshots. |
| `POST` | `/jobs/{id}/cancel` | Cancels a queued or running job. Running renders stop at the next frame batch. |
| `GET` | `/jobs/{id}/files/{video,srt,vtt}` | Downloads the finished outputs. |
| `WS` | `/ws/jobs/{id}` | Live job events. Past events are replayed first, and the socket closes after the final state. |

Errors return `{"detail": "..."}` in plain language, for example *"Brain's region (X 900, …) extends outside the 977×580 image. Reduce W/H or move it so X+W ≤ 977 and Y+H ≤ 580."* Status codes: `422` for validation problems, `503` when FFmpeg or TTS is unavailable, `401` for a missing token.

### Cast detection

The detector is RetinaFace (facexlib) running on CPU, so the GPU stays free for renders. It uses a permissive confidence floor, plus an extra 2× upscaled pass for scenes under 1600 px, so stylized and illustrated faces are found. Each face gets a region:

- `region_source: "tile"`: the nearest long straight edges on all four sides form a panel around this face only.
- `region_source: "head"`: a head-and-shoulders box, used when there is no panel or two characters share one.

On `examples/source_image/GoogleMeet_PHM.png`, detection returns all four participants (`#1` Brain … `#4` Victim 1) with their tiles in about 4 s.

### Render request

```jsonc
{
  "image_path": "examples/source_image/GoogleMeet_PHM.png",
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
- Renders run one at a time on a single GPU lane. Model downloads use a separate lane.

## Walkthrough (Python)

```python
import httpx, json, websockets, asyncio

api = "http://127.0.0.1:7860"
img = "examples/source_image/GoogleMeet_PHM.png"
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
    async with websockets.connect(f"ws://127.0.0.1:7860/ws/jobs/{job['id']}") as ws:
        async for raw in ws:
            print(json.loads(raw)["job"]["message"])
asyncio.run(follow())
```

Measured on an AMD Radeon RX 6550M (DirectML), this two-line, three-turn scene (5.8 s, 145 frames) rendered in 160 s: 1.7 s of audio, about 1.36 s per animated frame, and 0.6 s of compositing.

## Notes and limits (phase 1)

- Set `GLADTALKER_KEEP_WORK=1` to keep intermediate files (tiles, TTS lines, per-character SadTalker videos) in each render's `work/` folder.
- `GLADTALKER_FFMPEG` / `GLADTALKER_FFPROBE` override FFmpeg discovery.
- Model download progress is reported per file. An interrupted download restarts that file; it does not resume.
- Burned-in subtitles, project auto-save (`.gladtalker.json`), and the accessible UI itself are later phases of #11.
