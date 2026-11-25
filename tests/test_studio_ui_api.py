"""Engine endpoints added for the Studio UI (issue #11, phase 2)."""

import io
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from src.studio import pipeline, server
from src.studio.jobs import JobManager
from src.studio.schemas import AudioPreviewRequest
from src.studio.server import DEFAULT_HOSTS, StudioConfig, StudioServices, create_app
from tests.test_studio_server import FakeDetector, FakeDevices, FakeTTS, _write_png

REAL_FFMPEG = shutil.which("ffmpeg")


def _wait(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


class UiEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gladtalker_ui_"))
        self.jobs = JobManager(lanes={"gpu": 1, "io": 1, "audio": 1})
        self.services = StudioServices(devices=FakeDevices(), tts=FakeTTS(), detector=FakeDetector(), jobs=self.jobs)
        self.config = StudioConfig(workspace=self.tmp / "ws", allowed_hosts=DEFAULT_HOSTS + ["testserver"])

    def tearDown(self):
        self.jobs.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, **patches):
        return TestClient(create_app(self.config, self.services))

    def test_uploaded_images_are_served_back_by_name_only(self):
        with self.client() as c:
            png = _write_png(self.tmp / "scene.png").read_bytes()
            path = c.post("/uploads", files={"file": ("scene.png", io.BytesIO(png), "image/png")}).json()["image_path"]
            name = Path(path).name
            r = c.get(f"/uploads/{name}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, png)
            for bad in ("..%2F..%2Fstudio_server.py", "nothex.png", f"{name[:-4]}.exe", "0" * 32 + ".png"):
                self.assertEqual(c.get(f"/uploads/{bad}").status_code, 404, bad)

    def test_script_lint_per_line(self):
        with self.client() as c:
            r = c.post("/script/lint", json={"lines": ["You're late.", "Fine.", "", "...!"]})
            self.assertEqual(r.status_code, 200)
            lines = r.json()["lines"]
            self.assertEqual([[w["code"] for w in ws] for ws in lines], [["tts_contraction"], [], [], ["no_speech"]])
            self.assertEqual(lines[0][0]["fix"], "your late.")
            self.assertEqual(c.post("/script/lint", json={"lines": ["x" * 1001]}).status_code, 422)

    def test_audio_preview_job_serves_its_wav(self):
        def fake_preview(ctx, req, out_dir, tts):
            out_dir.mkdir(parents=True)
            (out_dir / "preview.wav").write_bytes(b"RIFF....WAVE")
            self.seen = req
            return {"audio_path": str(out_dir / "preview.wav"), "duration_s": 1.2, "tempo": 1.0}

        body = {"cast": [{"name": "Officer  Mia", "semitones": 2.5}],
                "script": [{"speaker": "officer mia", "text": "Freeze."}]}
        with mock.patch.object(pipeline, "require_ffmpeg_user_facing", return_value="ffmpeg"), \
                mock.patch.object(pipeline, "preview_audio", fake_preview), self.client() as c:
            r = c.post("/audio/preview", json=body)
            self.assertEqual(r.status_code, 202, r.text)
            job = _wait(c, r.json()["id"])
            self.assertEqual(job["state"], "succeeded")
            self.assertEqual(job["kind"], "audio.preview")
            audio = c.get(f"/jobs/{job['id']}/files/audio")
            self.assertEqual((audio.status_code, audio.headers["content-type"]), (200, "audio/wav"))
        self.assertEqual(self.seen.script[0].speaker, "Officer  Mia")

    def test_audio_preview_rejects_unknown_speakers(self):
        with mock.patch.object(pipeline, "require_ffmpeg_user_facing", return_value="ffmpeg"), self.client() as c:
            r = c.post("/audio/preview", json={"cast": [{"name": "A"}], "script": [{"speaker": "B", "text": "Hi"}]})
            self.assertEqual(r.status_code, 422)
            self.assertIn("not in the cast", r.text)

    def test_built_ui_is_served_with_security_headers_without_shadowing_the_api(self):
        dist = self.tmp / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>GladTalker Studio</title>", encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        with mock.patch.object(server, "UI_DIST", dist), self.client() as c:
            page = c.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("GladTalker Studio", page.text)
            csp = page.headers["content-security-policy"]
            for directive in ("default-src 'self'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'"):
                self.assertIn(directive, csp)
            self.assertEqual(page.headers["x-content-type-options"], "nosniff")
            self.assertEqual(c.get("/assets/app.js").status_code, 200)
            self.assertEqual(c.get("/health").json()["status"], "ok")  # API still wins
            self.assertNotIn("content-security-policy", c.get("/health").headers)
            self.assertEqual(c.get("/docs").status_code, 200)

    def test_no_ui_mount_when_not_built(self):
        with mock.patch.object(server, "UI_DIST", self.tmp / "missing"), self.client() as c:
            self.assertEqual(c.get("/").status_code, 404)


class _ToneTTS:
    """Writes a short tone instead of speaking, so preview_audio can run without coqui-tts."""

    def synthesize(self, text, out_wav):
        import soundfile

        sr = 22050
        t = np.arange(int(sr * (0.3 + 0.02 * len(text)))) / sr
        soundfile.write(out_wav, 0.2 * np.sin(2 * np.pi * 220 * t), sr)
        return out_wav


@unittest.skipUnless(REAL_FFMPEG, "FFmpeg not installed")
class PreviewAudioIntegrationTests(unittest.TestCase):
    def test_preview_matches_render_timeline(self):
        import soundfile

        req = AudioPreviewRequest.model_validate({
            "cast": [{"name": "Brain", "semitones": -1.5}, {"name": "Pinky", "semitones": 2.5}],
            "script": [{"speaker": "Brain", "text": "Click it."}, {"speaker": "Pinky", "text": "I'm clicking."}],
            "pacing": {"target_duration_s": 3.0},
        })
        out = Path(tempfile.mkdtemp(prefix="gladtalker_preview_"))
        try:
            ctx = SimpleNamespace(set_stages=lambda s: None, progress=lambda *a, **k: None)
            res = pipeline.preview_audio(ctx, req, out, _ToneTTS())
            self.assertEqual(res["duration_s"], 3.0)
            self.assertEqual(res["frames"], 75)
            self.assertGreaterEqual(res["tempo"], 1.0)
            self.assertEqual([c["speaker"] for c in res["captions"]], ["Brain", "Pinky"])
            self.assertAlmostEqual(soundfile.info(res["audio_path"]).duration, 3.0, places=2)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["preview.wav"], "work files cleaned up")
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
