import io
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
from src.studio.server import DEFAULT_HOSTS, StudioConfig, StudioServices, create_app


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
            with mock.patch.object(pipeline, "render", self._fake_render()):
                job_id = client.post("/render/start", json=_render_body(self.image),
                                     headers={"Authorization": "Bearer s3cret"}).json()["id"]
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
                    ws.receive_json()
            with client.websocket_connect(f"/ws/jobs/{job_id}?token=s3cret") as ws:
                self.assertEqual(ws.receive_json()["type"], "state")


if __name__ == "__main__":
    unittest.main()
