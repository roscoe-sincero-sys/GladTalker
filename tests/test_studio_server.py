import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.studio import pipeline
from src.studio.faces import FaceBox
from src.studio.jobs import JobManager
from src.studio.server import DEFAULT_HOSTS, DEFAULT_ORIGINS, StudioConfig, StudioServices, create_app
from tests._file_locks import exclusive_open, range_lock, windows_only

REAL_CHECK_MODELS = pipeline.check_models  # setUp stubs it out for most tests


class FakeTTS:
    def preview(self, text, semitones=0.0, tempo=1.0, ffmpeg=None):
        self.last = (text, semitones, tempo)
        return b"RIFF....WAVEfmt "


class FakeDetector:
    def detect(self, image, min_confidence=0.2, max_faces=8):
        return [FaceBox(440, 240, 490, 300, 0.8), FaceBox(440, 60, 490, 120, 0.9)][:max_faces]


class FakeDevices:
    spec = "cpu"
    error = None

    def resolve(self):
        import torch

        return torch.device("cpu")

    def info(self):
        return {"spec": "cpu", "type": "cpu", "label": "CPU", "is_gpu": False, "error": None}


def _write_png(path: Path, w=600, h=400):
    import cv2

    img = np.full((h, w, 3), 235, np.uint8)
    for x1, y1, x2, y2 in [(350, 20, 580, 180), (350, 200, 580, 380)]:
        cv2.rectangle(img, (x1, y1), (x2, y2), (40, 120, 40), 2)
    ok, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())
    return path


def _render_body(image_path, **overrides):
    body = {
        "image_path": str(image_path),
        "cast": [{"name": "Brain", "region": {"x": 350, "y": 20, "w": 230, "h": 160}, "semitones": -1.5},
                 {"name": "Pinky", "region": {"x": 350, "y": 200, "w": 230, "h": 180}, "semitones": 2.5}],
        "script": [{"speaker": "Brain", "text": "Click it."}, {"speaker": "Pinky", "text": "I'm clicking."}],
        "output": {"preset": "hd720", "filename": "scene"},
    }
    body.update(overrides)
    return body


class StudioServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gladtalker_test_"))
        self.image = _write_png(self.tmp / "scene.png")
        self.jobs = JobManager(lanes={"gpu": 1, "io": 1})
        self.services = StudioServices(devices=FakeDevices(), tts=FakeTTS(), detector=FakeDetector(), jobs=self.jobs)
        self.config = StudioConfig(workspace=self.tmp / "ws", allowed_hosts=DEFAULT_HOSTS + ["testserver"])
        self.client = TestClient(create_app(self.config, self.services))
        # Prechecks for /render/start that depend on the machine (FFmpeg + model files).
        patches = [mock.patch.object(pipeline, "require_ffmpeg_user_facing", return_value="ffmpeg"),
                   mock.patch.object(pipeline, "check_models", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.client.close()
        self.jobs.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_app_points_imageio_at_ffmpeg_override(self):
        import os

        with mock.patch.dict(os.environ, {"GLADTALKER_FFMPEG": "Z:/tools/ffmpeg.exe"}):
            os.environ.pop("IMAGEIO_FFMPEG_EXE", None)
            create_app(StudioConfig(workspace=self.tmp / "ws4"), self.services)
            self.assertEqual(os.environ.get("IMAGEIO_FFMPEG_EXE"), "Z:/tools/ffmpeg.exe")

    # -- basic endpoints ---------------------------------------------------------------------
    def test_health_and_voices(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        presets = self.client.get("/voices").json()["presets"]
        self.assertEqual({p["id"]: p["semitones"] for p in presets}["nervous"], 2.5)

    def test_models_status(self):
        data = self.client.get("/models").json()
        self.assertEqual(data["total"], 8)
        self.assertEqual(len(data["files"]), 8)

    def test_script_parse(self):
        r = self.client.post("/script/parse", json={"text": "Brain: You're late.\nNarrator: Hm.", "cast": ["Brain"]})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["unknown_speakers"], ["Narrator"])
        self.assertEqual(data["lines"][0]["warnings"][0]["fix"], "your late.")

    def test_tts_preview_returns_wav(self):
        with mock.patch("src.studio.server.find_ffmpeg", return_value="ffmpeg"):
            r = self.client.post("/tts/preview", json={"text": "Hello", "semitones": 2.5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "audio/wav")
        self.assertEqual(self.services.tts.last, ("Hello", 2.5, 1.0))

    def test_tts_preview_validates(self):
        self.assertEqual(self.client.post("/tts/preview", json={"text": "", "semitones": 0}).status_code, 422)
        self.assertEqual(self.client.post("/tts/preview", json={"text": "Hi", "semitones": 9}).status_code, 422)

    def test_tts_preview_rejects_whitespace_only_text(self):
        self.services.tts.last = None
        with mock.patch("src.studio.server.find_ffmpeg", return_value="ffmpeg"):
            r = self.client.post("/tts/preview", json={"text": "  \t\n "})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Enter some text to preview", r.text)
        self.assertIsNone(self.services.tts.last)

    def test_tts_preview_without_ffmpeg(self):
        with mock.patch("src.studio.server.find_ffmpeg", return_value=None):
            r = self.client.post("/tts/preview", json={"text": "Hello"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("FFmpeg", r.json()["detail"])

    def test_cast_detect(self):
        r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["image"]["width"], 600)
        self.assertEqual([c["badge"] for c in data["cast"]], ["#1", "#2"])
        self.assertEqual(data["cast"][0]["face"]["y"], 60)  # reading order: top tile first
        self.assertEqual(data["cast"][0]["region_source"], "tile")

    def test_cast_detect_reports_missing_detector_model(self):
        from src.studio.faces import FaceDetectorService

        self.services.detector = FaceDetectorService(model_root=str(self.tmp / "no_models"))
        with mock.patch("facexlib.detection.init_detection_model") as init:
            r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Download Models", r.json()["detail"])
        init.assert_not_called()

    def test_cast_detect_bad_image(self):
        bad = self.tmp / "not_an_image.png"
        bad.write_bytes(b"nope")
        r = self.client.post("/cast/detect", json={"image_path": str(bad)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("isn't a readable image", r.json()["detail"])
        r = self.client.post("/cast/detect", json={"image_path": str(self.tmp / "missing.png")})
        self.assertEqual(r.status_code, 422)

    def test_upload(self):
        data = self.image.read_bytes()
        r = self.client.post("/uploads", files={"file": ("scene.png", io.BytesIO(data), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(Path(r.json()["image_path"]).is_file())
        self.assertEqual(r.json()["width"], 600)
        r = self.client.post("/uploads", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/uploads", files={"file": ("x.png", io.BytesIO(b"garbage"), "image/png")})
        self.assertEqual(r.status_code, 422)

    # -- render jobs ---------------------------------------------------------------------------
    def test_render_rejects_bad_requests_up_front(self):
        r = self.client.post("/render/start", json=_render_body(self.image, script=[{"speaker": "Nobody", "text": "x"}]))
        self.assertEqual(r.status_code, 422)
        cast = [{"name": "Brain", "region": {"x": 500, "y": 300, "w": 230, "h": 160}}]
        r = self.client.post("/render/start", json=_render_body(self.image, cast=cast,
                                                                 script=[{"speaker": "Brain", "text": "Hi"}]))
        self.assertEqual(r.status_code, 422)
        self.assertIn("extends outside", r.json()["detail"])

    def _fake_render(self, gate=None):
        def fake(ctx, req, out_dir, tts, device):
            ctx.set_stages(["audio", "render:Brain", "compose"])
            ctx.progress(0.1, "Synthesizing voices", stage="audio")
            if gate is not None:
                while not gate.is_set():
                    ctx.progress(message="Rendering Brain", stage="render:Brain")
                    time.sleep(0.01)
            out_dir.mkdir(parents=True, exist_ok=True)
            video, srt = out_dir / "scene.mp4", out_dir / "scene.srt"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nBrain: Click it.\n", encoding="utf-8")
            return {"video_path": str(video), "srt_path": str(srt)}

        return fake

    def test_render_job_streams_events_and_serves_files(self):
        with mock.patch.object(pipeline, "render", self._fake_render()):
            r = self.client.post("/render/start", json=_render_body(self.image))
            self.assertEqual(r.status_code, 202, r.text)
            job_id = r.json()["id"]
            with self.client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
                events = []
                while True:
                    try:
                        events.append(ws.receive_json())
                    except WebSocketDisconnect:
                        break
        self.assertEqual(events[-1]["job"]["state"], "succeeded", events[-1])
        self.assertIn("progress", [e["type"] for e in events])
        self.assertEqual(events[-1]["job"]["stages"], ["audio", "render:Brain", "compose"])

        self.assertEqual(self.client.get(f"/jobs/{job_id}").json()["state"], "succeeded")
        self.assertIn(job_id, [j["id"] for j in self.client.get("/jobs").json()["jobs"]])
        video = self.client.get(f"/jobs/{job_id}/files/video")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.headers["content-type"], "video/mp4")
        self.assertEqual(self.client.get(f"/jobs/{job_id}/files/srt").status_code, 200)
        self.assertEqual(self.client.get(f"/jobs/{job_id}/files/vtt").status_code, 404)
        self.assertEqual(self.client.get(f"/jobs/{job_id}/files/exe").status_code, 404)

    def test_cancel_render(self):
        gate = threading.Event()
        with mock.patch.object(pipeline, "render", self._fake_render(gate)):
            job_id = self.client.post("/render/start", json=_render_body(self.image)).json()["id"]
            deadline = time.time() + 5
            while self.client.get(f"/jobs/{job_id}").json()["state"] != "running" and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.client.post(f"/jobs/{job_id}/cancel").json()["cancel_requested"])
            while self.client.get(f"/jobs/{job_id}").json()["state"] == "running" and time.time() < deadline:
                time.sleep(0.01)
            gate.set()
        self.assertEqual(self.client.get(f"/jobs/{job_id}").json()["state"], "cancelled")
        self.assertEqual(self.client.get(f"/jobs/{job_id}/files/video").status_code, 404)

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/jobs/nope").status_code, 404)
        self.assertEqual(self.client.post("/jobs/nope/cancel").status_code, 404)

    # -- security ------------------------------------------------------------------------------
    def test_rejects_unexpected_host(self):
        r = self.client.get("/health", headers={"host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_cors_only_for_allowed_origins(self):
        ok = self.client.options("/script/parse", headers={
            "origin": "tauri://localhost", "access-control-request-method": "POST",
            "access-control-request-headers": "content-type"})
        self.assertEqual(ok.headers.get("access-control-allow-origin"), "tauri://localhost")
        bad = self.client.options("/script/parse", headers={
            "origin": "https://evil.example", "access-control-request-method": "POST"})
        self.assertNotEqual(bad.headers.get("access-control-allow-origin"), "https://evil.example")

    def test_websocket_rejects_foreign_origin(self):
        with mock.patch.object(pipeline, "render", self._fake_render()):
            job_id = self.client.post("/render/start", json=_render_body(self.image)).json()["id"]
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/ws/jobs/{job_id}", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()

    def test_token_required_when_configured(self):
        config = StudioConfig(workspace=self.tmp / "ws2", token="s3cret",
                              allowed_hosts=DEFAULT_HOSTS + ["testserver"])
        with TestClient(create_app(config, self.services)) as client:
            self.assertEqual(client.get("/health").status_code, 200)  # liveness stays open
            self.assertEqual(client.get("/models").status_code, 401)
            self.assertEqual(client.get("/models", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(client.get("/models", headers={"Authorization": "Bearer s3cret"}).status_code, 200)
            self.assertEqual(client.get("/models", headers={"X-GladTalker-Token": "s3cret"}).status_code, 200)
            auth = {"Authorization": "Bearer s3cret"}
            with mock.patch.object(pipeline, "render", self._fake_render()):
                job_id = client.post("/render/start", json=_render_body(self.image), headers=auth).json()["id"]
            # No ticket, or the long-lived token in the URL: both refused.
            for query in ("", "?token=s3cret"):
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect(f"/ws/jobs/{job_id}{query}") as ws:
                        ws.receive_json()
            self.assertEqual(client.post(f"/jobs/{job_id}/ws-ticket").status_code, 401)
            ticket = client.post(f"/jobs/{job_id}/ws-ticket", headers=auth).json()
            self.assertEqual(ticket["url"], f"/ws/jobs/{job_id}?ticket={ticket['ticket']}")
            with client.websocket_connect(ticket["url"]) as ws:
                self.assertEqual(ws.receive_json()["type"], "state")
            # Tickets are single-use...
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(ticket["url"]) as ws:
                    ws.receive_json()
            # ...and bound to one job.
            other = client.post(f"/jobs/{job_id}/ws-ticket", headers=auth).json()["ticket"]
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws/jobs/someotherjob?ticket={other}") as ws:
                    ws.receive_json()

    def test_desktop_dev_origin_reaches_a_token_protected_engine(self):
        """`npm run tauri:dev` loads the UI from http://localhost:5173 and talks to the engine on
        its own port, so the shell starts the engine with --allow-origin for that dev origin
        (debug builds only). That origin must get through over HTTP (the authenticated preflight
        and the request) and over WebSocket; any other origin still doesn't."""
        dev = "http://localhost:5173"
        config = StudioConfig(workspace=self.tmp / "ws-dev", token="s3cret", allowed_hosts=DEFAULT_HOSTS + ["testserver"],
                              allowed_origins=list(DEFAULT_ORIGINS) + [dev])
        auth = {"Authorization": "Bearer s3cret"}
        with TestClient(create_app(config, self.services)) as client:
            preflight = client.options("/hardware", headers={"Origin": dev, "Access-Control-Request-Method": "GET",
                                                             "Access-Control-Request-Headers": "authorization"})
            self.assertEqual(preflight.status_code, 200, preflight.text)
            self.assertEqual(preflight.headers["access-control-allow-origin"], dev)
            r = client.get("/hardware", headers={"Origin": dev, **auth})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["access-control-allow-origin"], dev)
            self.assertEqual(client.get("/hardware", headers={"Origin": "http://localhost:5174", **auth}).status_code, 403)

            with mock.patch.object(pipeline, "render", self._fake_render()):
                job_id = client.post("/render/start", json=_render_body(self.image), headers={"Origin": dev, **auth}).json()["id"]
            ticket = client.post(f"/jobs/{job_id}/ws-ticket", headers={"Origin": dev, **auth}).json()
            with client.websocket_connect(ticket["url"], headers={"origin": dev}) as ws:
                self.assertEqual(ws.receive_json()["type"], "state")
            other = client.post(f"/jobs/{job_id}/ws-ticket", headers=auth).json()
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(other["url"], headers={"origin": "http://localhost:5174"}) as ws:
                    ws.receive_json()

    def test_cross_origin_requests_rejected_before_side_effects(self):
        evil = {"origin": "https://evil.example"}
        with mock.patch.object(pipeline, "render", self._fake_render()) as _:
            r = self.client.post("/render/start", json=_render_body(self.image), headers=evil)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/jobs").json()["jobs"], [], "no job may be created")
        data = self.image.read_bytes()
        r = self.client.post("/uploads", files={"file": ("scene.png", io.BytesIO(data), "image/png")}, headers=evil)
        self.assertEqual(r.status_code, 403)
        self.assertFalse((self.tmp / "ws" / "uploads").exists(), "nothing may be written")
        with mock.patch("src.studio.server.download_models") as dl:
            self.assertEqual(self.client.post("/models/download", headers=evil).status_code, 403)
            dl.assert_not_called()
        # The Studio shell and same-origin pages are still allowed.
        ok = self.client.post("/script/parse", json={"text": "A: hi"}, headers={"origin": "tauri://localhost"})
        self.assertEqual(ok.status_code, 200)
        same = self.client.post("/script/parse", json={"text": "A: hi"}, headers={"origin": "http://testserver"})
        self.assertEqual(same.status_code, 200)

    def test_upload_limits_enforced_before_parsing(self):
        config = StudioConfig(workspace=self.tmp / "ws3", max_upload_mb=1, token="s3cret",
                              allowed_hosts=DEFAULT_HOSTS + ["testserver"])
        big = b"\x89PNG" + b"0" * (2 * 1024 * 1024)
        with TestClient(create_app(config, self.services)) as client:
            auth = {"Authorization": "Bearer s3cret"}
            # Token is checked before the multipart body is read.
            r = client.post("/uploads", files={"file": ("x.png", io.BytesIO(b"tiny"), "image/png")})
            self.assertEqual(r.status_code, 401)
            # Declared length over the limit is refused up front.
            r = client.post("/uploads", files={"file": ("x.png", io.BytesIO(big), "image/png")}, headers=auth)
            self.assertEqual(r.status_code, 413)

            # A streamed (chunked, no Content-Length) body is cut off at the limit.
            def chunks():  # well-formed multipart whose file part keeps streaming past the limit
                yield (b'--xyz\r\nContent-Disposition: form-data; name="file"; filename="x.png"\r\n'
                       b"Content-Type: image/png\r\n\r\n")
                for _ in range(40):
                    yield b"0" * (64 * 1024)
                yield b"\r\n--xyz--\r\n"

            r = client.post("/uploads", content=chunks(),
                            headers={**auth, "content-type": "multipart/form-data; boundary=xyz"})
            self.assertEqual(r.status_code, 413)
            # JSON endpoints have a small limit too.
            r = client.post("/script/parse", content=b'{"text": "' + b"a" * (3 * 1024 * 1024) + b'"}',
                            headers={**auth, "content-type": "application/json"})
            self.assertEqual(r.status_code, 413)
        uploads = self.tmp / "ws3" / "uploads"
        self.assertTrue(not uploads.exists() or not any(uploads.iterdir()))

    # -- model verification: temporary (file changing / locked) vs damaged --------------------
    DETECTOR_REL = "gfpgan/weights/detection_Resnet50_Final.pth"

    def _install_models(self):
        """Every model file under a temp root, pinned to its (tiny) contents."""
        import hashlib

        from scripts import download_models as dm
        from src.studio.faces import FaceDetectorService
        from src.studio.hardware import models_status

        root = self.tmp / "models_root"
        data = b"weights" * 64
        pins = {}
        for rel, _ in dm.MODELS:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
            pins[rel] = (len(data), hashlib.sha256(data).hexdigest())
        self.services.detector = FaceDetectorService(model_root=str(root / "gfpgan" / "weights"))
        # /models and the render precheck read this root instead of the repository's.
        for patcher in (mock.patch.dict(dm.MODEL_CHECKSUMS, pins),
                        mock.patch("src.studio.server.models_status", lambda: models_status(root)),
                        mock.patch.object(pipeline, "check_models", lambda **kw: REAL_CHECK_MODELS(root, **kw))):
            patcher.start()
            self.addCleanup(patcher.stop)
        return root

    def _changing_while_hashed(self, rel_name):
        """Make verification of ``rel_name`` exhaust its retries: the file is modified each time
        it has just been hashed."""
        from scripts import download_models as dm

        real = dm._hash_open_file
        touches = []

        def hash_then_touch(f):
            digest = real(f)
            if Path(f.name).name == rel_name:
                touches.append(1)
                stamp = 1_700_000_000_000_000_000 + len(touches) * 1_000_000_000
                os.utime(f.name, ns=(stamp, stamp))
            return digest

        return mock.patch.object(dm, "_hash_open_file", hash_then_touch)

    def _age_models(self, root):
        """Make the files look long installed, so their verified digests are served from cache."""
        past = time.time_ns() - 60 * 1_000_000_000
        for path in root.rglob("*.pth"):
            os.utime(path, ns=(past, past))
        for path in root.rglob("*.safetensors"):
            os.utime(path, ns=(past, past))

    def test_changing_checkpoint_is_busy_not_damaged(self):
        self._install_models()
        with self._changing_while_hashed("detection_Resnet50_Final.pth"), \
                mock.patch("facexlib.detection.init_detection_model") as init:
            status = self.client.get("/models").json()
            entry = next(f for f in status["files"] if f["path"] == self.DETECTOR_REL)
            self.assertEqual((entry["state"], entry["busy"], entry["damaged"], entry["ready"]),
                             ("busy", True, False, False))
            self.assertEqual((status["busy"], status["ready"], status["complete"]), (1, 7, False))

            r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
            self.assertEqual(r.status_code, 503, r.text)
            self.assertEqual(r.headers["retry-after"], "5")
            self.assertTrue(r.json()["retryable"])
            self.assertIn("Try Auto-Cast again in a moment", r.json()["detail"])

            r = self.client.post("/render/start", json=_render_body(self.image))
            self.assertEqual(r.status_code, 503, r.text)
            self.assertEqual(r.headers["retry-after"], "5")
            self.assertIn("detection_Resnet50_Final.pth", r.json()["detail"])
            self.assertIn("Try again in a moment", r.json()["detail"])
        init.assert_not_called()
        self.assertEqual(self.client.get("/jobs").json()["jobs"], [])
        # Once the file settles it verifies normally.
        self.assertTrue(self.client.get("/models").json()["complete"])

    @windows_only
    def test_locked_checkpoint_with_a_warm_cache_is_busy_not_ready(self):
        """A digest cached while the file was readable must not make a locked file "ready"."""
        root = self._install_models()
        self._age_models(root)
        self.assertTrue(self.client.get("/models").json()["complete"])  # warms the digest cache
        with exclusive_open(root / self.DETECTOR_REL), \
                mock.patch("facexlib.detection.init_detection_model") as init:
            status = self.client.get("/models").json()
            entry = next(f for f in status["files"] if f["path"] == self.DETECTOR_REL)
            self.assertEqual((entry["state"], entry["ready"], entry["damaged"]), ("busy", False, False))
            r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
            self.assertEqual(r.status_code, 503, r.text)
            self.assertTrue(r.json()["retryable"])
            r = self.client.post("/render/start", json=_render_body(self.image))
            self.assertEqual(r.status_code, 503, r.text)
        init.assert_not_called()
        self.assertTrue(self.client.get("/models").json()["complete"])

    @windows_only
    def test_lock_late_in_a_cached_checkpoint_is_busy_not_ready(self):
        root = self._install_models()
        self._age_models(root)
        self.assertTrue(self.client.get("/models").json()["complete"])  # warms the digest cache
        path = root / self.DETECTOR_REL
        with range_lock(path, offset=path.stat().st_size - 16, length=16),                 mock.patch("facexlib.detection.init_detection_model") as init:
            entry = next(f for f in self.client.get("/models").json()["files"] if f["path"] == self.DETECTOR_REL)
            self.assertEqual(entry["state"], "busy")
            r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
            self.assertEqual(r.status_code, 503, r.text)
        init.assert_not_called()

    @windows_only
    def test_lock_taken_while_the_detector_loads_gets_retry_advice(self):
        """Verification passed, then another program locked the weights before the loader read
        them: the load error becomes the retryable 503, not a 500."""
        import contextlib

        root = self._install_models()
        path = root / self.DETECTOR_REL
        with contextlib.ExitStack() as held:
            def load(*args, **kwargs):  # facexlib's loader: reads the weights with torch.load
                import torch

                held.enter_context(range_lock(path, offset=0, length=path.stat().st_size))
                return torch.load(str(path), map_location="cpu", weights_only=True)

            with mock.patch("facexlib.detection.init_detection_model", side_effect=load):
                r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertTrue(r.json()["retryable"])
        self.assertIn("detection_Resnet50_Final.pth is being updated", r.json()["detail"])

    @windows_only
    def test_lock_during_load_gets_retry_advice_whatever_the_folder_casing(self):
        """The configured detector folder is "gfpgan/weights" but the folders on disk are spelled
        "GFPGAN/WEIGHTS": still the pinned file, so a late lock is still a retryable 503."""
        import contextlib

        from src.studio.faces import FaceDetectorService

        root = self._install_models()
        shutil.rmtree(root / "gfpgan")
        (root / "GFPGAN" / "WEIGHTS").mkdir(parents=True)
        (root / "GFPGAN" / "WEIGHTS" / "detection_Resnet50_Final.pth").write_bytes(b"weights" * 64)
        self.services.detector = FaceDetectorService(model_root=str(root / "gfpgan" / "weights"))
        path = root / "gfpgan" / "weights" / "detection_Resnet50_Final.pth"
        with contextlib.ExitStack() as held:
            def load(*args, **kwargs):
                import torch

                held.enter_context(range_lock(path, offset=0, length=path.stat().st_size))
                return torch.load(str(path), map_location="cpu", weights_only=True)

            with mock.patch("facexlib.detection.init_detection_model", side_effect=load):
                r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertTrue(r.json()["retryable"])

    @windows_only
    def test_lock_during_load_gets_retry_advice_from_a_configured_model_folder(self):
        """A detector configured with its own model folder (not ".../gfpgan/weights") is still
        guarded with the file and pin its precheck verified."""
        import contextlib

        from src.studio.faces import FaceDetectorService

        self._install_models()
        folder = self.tmp / "my detector models"
        folder.mkdir()
        path = folder / "detection_Resnet50_Final.pth"
        path.write_bytes(b"weights" * 64)  # matches the pin _install_models set
        self.services.detector = FaceDetectorService(model_root=str(folder))
        with contextlib.ExitStack() as held:
            def load(*args, **kwargs):
                import torch

                held.enter_context(range_lock(path, offset=0, length=path.stat().st_size))
                return torch.load(str(path), map_location="cpu", weights_only=True)

            with mock.patch("facexlib.detection.init_detection_model", side_effect=load):
                r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.headers["retry-after"], "5")

    def test_detector_out_of_memory_is_not_disguised_by_a_busy_checkpoint(self):
        """Running out of memory while the detector moves to its device isn't a model read, so it
        isn't turned into retry advice even if a model file happens to be busy then."""
        import torch

        from scripts import download_models as dm

        self._install_models()
        oom = torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 1.00 GiB")
        with mock.patch("facexlib.detection.init_detection_model", side_effect=oom):
            real_state = dm.verification_state
            calls = []

            def state(dest, expected, **kw):  # the pre-load check passes; any later check finds it busy
                calls.append(dest)
                return real_state(dest, expected, **kw) if len(calls) == 1 else dm.BUSY

            with mock.patch.object(dm, "verification_state", state):
                client = TestClient(create_app(self.config, self.services), raise_server_exceptions=False)
                r = client.post("/cast/detect", json={"image_path": str(self.image)})
                client.close()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(len(calls), 1)  # nothing was rehashed after the failure

    def test_load_failure_with_healthy_weights_is_not_disguised(self):
        self._install_models()
        with mock.patch("facexlib.detection.init_detection_model", side_effect=RuntimeError("boom")):
            client = TestClient(create_app(self.config, self.services), raise_server_exceptions=False)
            r = client.post("/cast/detect", json={"image_path": str(self.image)})
            client.close()
        self.assertEqual(r.status_code, 500)

    def test_unopenable_checkpoint_is_busy_not_damaged(self):
        """Portable version of the lock test: every model refuses to open (sharing violation)."""
        self._install_models()
        from scripts import download_models as dm

        with mock.patch.object(dm, "open", create=True, side_effect=PermissionError(13, "in use")):
            status = self.client.get("/models").json()
            self.assertEqual(status["busy"], 8)
            self.assertFalse(any(f["damaged"] or f["ready"] for f in status["files"]))
            self.assertEqual(self.client.post("/render/start", json=_render_body(self.image)).status_code, 503)

    def test_retry_after_is_readable_by_allowed_browser_origins(self):
        self._install_models()
        with self._changing_while_hashed("detection_Resnet50_Final.pth"):
            r = self.client.post("/cast/detect", json={"image_path": str(self.image)},
                                 headers={"Origin": "http://tauri.localhost"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers["access-control-allow-origin"], "http://tauri.localhost")
        self.assertIn("retry-after", r.headers["access-control-expose-headers"].lower())

    def test_model_busy_after_admission_fails_the_job_with_retry_advice_and_frees_the_lane(self):
        from src.studio.jobs import RetryLaterError

        busy = RetryLaterError("Some models are being updated or are in use by another program, so they "
                               "couldn't be checked: x.pth. Try again in a moment.")
        with mock.patch.object(pipeline, "check_models", side_effect=[None, busy]):
            job_id = self.client.post("/render/start", json=_render_body(self.image)).json()["id"]
            job = self._wait_for_job(job_id)
        self.assertEqual(job["state"], "failed")
        self.assertIn("Try again in a moment", job["error"])
        follow_up = self.jobs.submit("probe", "Probe", lambda ctx: {"ok": True}, lane="gpu")
        self.assertEqual(self._wait_for_job(follow_up.id)["state"], "succeeded")

    def _wait_for_job(self, job_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/jobs/{job_id}").json()
            if job["state"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        self.fail(f"job {job_id} did not finish")

    def test_checksum_mismatch_is_damaged(self):
        root = self._install_models()
        path = root / self.DETECTOR_REL
        path.write_bytes(b"X" + path.read_bytes()[1:])  # same size, different bytes
        status = self.client.get("/models").json()
        entry = next(f for f in status["files"] if f["path"] == self.DETECTOR_REL)
        self.assertEqual((entry["state"], entry["damaged"], entry["busy"]), ("damaged", True, False))
        self.assertEqual(status["busy"], 0)

        r = self.client.post("/cast/detect", json={"image_path": str(self.image)})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("incomplete or damaged", r.json()["detail"])
        self.assertNotIn("retry-after", r.headers)

        r = self.client.post("/render/start", json=_render_body(self.image))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("damaged: detection_Resnet50_Final.pth", r.json()["detail"])

    def test_model_download_admission_is_atomic(self):
        gate = threading.Event()

        def slow_download(ctx, root, force=False):
            gate.wait(5)
            return {"downloaded": []}

        ids, errors = [], []

        def hit():
            try:
                ids.append(self.client.post("/models/download").json()["id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with mock.patch("src.studio.server.download_models", slow_download):
            threads = [threading.Thread(target=hit) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
            gate.set()
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 1, "concurrent requests must share one download job")
        self.assertEqual(len([j for j in self.jobs.list() if j.kind == "models.download"]), 1)

    def test_idle_websocket_disconnect_releases_subscription(self):
        gate = threading.Event()
        job = self.jobs.submit("render", "Idle", lambda ctx: gate.wait(10))
        try:
            with self.client.websocket_connect(f"/ws/jobs/{job.id}") as ws:
                ws.receive_json()  # history replay; the job then stays idle
                self.assertEqual(len(self.jobs._subscribers.get(job.id, [])), 1)
            deadline = time.time() + 3
            while self.jobs._subscribers.get(job.id) and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.jobs._subscribers.get(job.id), [], "subscription must be released")
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main()
