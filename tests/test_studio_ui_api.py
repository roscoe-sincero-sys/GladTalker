"""Engine endpoints added for the Studio UI (issue #11, phase 2)."""

import io
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from src.studio import pipeline, server
from src.studio.jobs import JobManager
from src.studio.previews import PreviewStore
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

    def test_token_mode_serves_the_ui_shell_but_guards_everything_else(self):
        """The page must load before it can read its #token= fragment (browsers can't attach a
        header to a navigation); only the bundle's own files are open, and only for GET/HEAD."""
        dist = self.tmp / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>GladTalker Studio</title>", encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        self.config.token = "s3cret"
        auth = {"Authorization": "Bearer s3cret"}
        with mock.patch.object(server, "UI_DIST", dist), self.client() as c:
            for path in ("/", "/index.html", "/assets/app.js", "/favicon.svg"):
                self.assertEqual(c.get(path).status_code, 200, path)
            self.assertEqual(c.head("/assets/app.js").status_code, 200)
            self.assertEqual(c.post("/").status_code, 401)  # only reads are open
            for path in ("/hardware", "/models", "/jobs", "/docs", "/openapi.json", "/assets/missing.js",
                         "/uploads/" + "0" * 32 + ".png", "/jobs/x/files/video"):
                self.assertEqual(c.get(path).status_code, 401, path)
            self.assertEqual(c.get("/hardware", headers=auth).status_code, 200)
            # The Origin check still applies to the shell.
            self.assertEqual(c.get("/", headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_voice_names_are_stripped_and_blank_names_rejected(self):
        req = AudioPreviewRequest.model_validate({"cast": [{"name": "  Brain "}],
                                                  "script": [{"speaker": "brain", "text": "Hi"}]})
        self.assertEqual((req.cast[0].name, req.script[0].speaker), ("Brain", "Brain"))
        with self.client() as c:
            r = c.post("/audio/preview", json={"cast": [{"name": "   "}], "script": [{"speaker": "A", "text": "Hi"}]})
            self.assertEqual(r.status_code, 422)
            self.assertIn("must not be blank", r.text)


class PreviewRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gladtalker_previews_"))
        self.root = self.tmp / "previews"
        self.jobs = JobManager(lanes={"audio": 1}, max_jobs=3)
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.jobs.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, **kw):
        store = PreviewStore(self.root, **kw)
        self.stores.append(store)
        return store

    def _finished_job(self, jobs=None):
        job = (jobs or self.jobs).submit("audio.preview", "Preview", lambda ctx: {}, lane="audio")
        deadline = time.time() + 10
        while not job.done and time.time() < deadline:
            time.sleep(0.01)
        return job

    @staticmethod
    def _write(folder):
        folder.mkdir(parents=True)
        (folder / "preview.wav").write_bytes(b"x")
        return folder

    def test_own_unreachable_and_old_previews_are_deleted_running_ones_kept(self):
        store = self._store(keep=1)
        orphan = self._write(store.new_folder("deadbeef0000", self.jobs))  # its job never existed here
        finished = [self._write(store.new_folder(self._finished_job().id, self.jobs)) for _ in range(2)]
        release = threading.Event()
        running = self.jobs.submit("audio.preview", "Preview", lambda ctx: release.wait(10), lane="audio")
        running_dir = self._write(store.dir / f"20000101_000000_{running.id}")  # oldest name, still running
        try:
            store.new_folder("current00000", self.jobs)
            remaining = sorted(p.name for p in store.dir.iterdir() if p.is_dir())
            self.assertEqual(remaining, sorted([running_dir.name, finished[-1].name]))
            self.assertFalse(orphan.exists())
        finally:
            release.set()

    def test_previews_evicted_from_the_job_list_lose_their_files(self):
        store = self._store()
        first = self._finished_job()
        folder = self._write(store.new_folder(first.id, self.jobs))
        for _ in range(3):  # max_jobs=3 evicts the first job
            self._finished_job()
        self.assertIsNone(self.jobs.get(first.id))
        store.new_folder("x", self.jobs)
        self.assertFalse(folder.exists())

    def test_another_running_engine_is_never_cleaned_up(self):
        other_jobs = JobManager(lanes={"audio": 1})
        self.addCleanup(other_jobs.shutdown)
        other = self._store(stale_s=0)
        mine = self._store(stale_s=0)
        theirs = self._write(other.new_folder(self._finished_job(other_jobs).id, other_jobs))
        mine.new_folder("a", self.jobs)  # its jobs are unknown here, but its engine holds its lock
        self.assertTrue(theirs.exists())

    def test_a_dead_engines_folder_is_reclaimed_once_stale(self):
        dead = PreviewStore(self.root, stale_s=0)
        leftover = self._write(dead.new_folder("abc", self.jobs))
        dead._lock.release()  # what the OS does when that engine's process ends
        dead._lock = None
        young = self._store(stale_s=3600)
        young.new_folder("b", self.jobs)
        self.assertTrue(leftover.exists(), "a recent folder might belong to an engine still starting up")
        self._store(stale_s=0).new_folder("c", self.jobs)
        self.assertFalse(dead.dir.exists())

    def test_two_engines_racing_to_reclaim_the_same_folder_both_carry_on(self):
        from src.studio import previews

        dead = PreviewStore(self.root, stale_s=0)
        self._write(dead.new_folder("abc", self.jobs))
        dead._lock.release()
        dead._lock = None
        original = previews._FileLock.acquire

        def other_engine_wins(lock, *args, **kwargs):
            # The other engine deletes the folder between our "marker exists" check and our open.
            if lock.path.parent == dead.dir:
                shutil.rmtree(dead.dir)
            return original(lock, *args, **kwargs)

        store = self._store(stale_s=0)
        with mock.patch.object(previews._FileLock, "acquire", other_engine_wins):
            folder = store.new_folder("j2", self.jobs)  # must not raise FileNotFoundError
        self.assertEqual(folder.parent, store.dir)
        self.assertFalse(dead.dir.exists())

    def test_clean_shutdown_removes_its_own_folder(self):
        store = PreviewStore(self.root)
        self._write(store.new_folder("j", self.jobs))
        store.close()
        self.assertFalse(store.dir.exists())


class TwoEnginesOneWorkspaceTests(unittest.TestCase):
    """Two engines on one workspace (two ports, or the e2e suite): a preview in one must not
    break a preview that is still running in the other."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gladtalker_two_"))
        self.config = StudioConfig(workspace=self.tmp / "ws", allowed_hosts=DEFAULT_HOSTS + ["testserver"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _app(self):
        jobs = JobManager(lanes={"gpu": 1, "io": 1, "audio": 1})
        return create_app(self.config, StudioServices(devices=FakeDevices(), tts=FakeTTS(),
                                                      detector=FakeDetector(), jobs=jobs))

    def test_a_running_preview_survives_the_other_engines_cleanup(self):
        started, release = threading.Event(), threading.Event()

        def fake_preview(ctx, req, out_dir, tts):
            out_dir.mkdir(parents=True)
            if req.script[0].text == "Slow.":
                started.set()
                release.wait(10)
            (out_dir / "preview.wav").write_bytes(b"RIFF....WAVE")
            return {"audio_path": str(out_dir / "preview.wav"), "duration_s": 1.0, "tempo": 1.0}

        body = lambda text: {"cast": [{"name": "A"}], "script": [{"speaker": "A", "text": text}]}
        with mock.patch.object(pipeline, "require_ffmpeg_user_facing", return_value="ffmpeg"),                 mock.patch.object(pipeline, "preview_audio", fake_preview),                 mock.patch.object(PreviewStore, "__init__", _no_grace(PreviewStore.__init__)),                 TestClient(self._app()) as one, TestClient(self._app()) as two:
            slow = one.post("/audio/preview", json=body("Slow.")).json()["id"]
            self.assertTrue(started.wait(10))
            quick = two.post("/audio/preview", json=body("Quick.")).json()["id"]
            self.assertEqual(_wait(two, quick)["state"], "succeeded")
            release.set()
            job = _wait(one, slow)
            self.assertEqual(job["state"], "succeeded", job.get("error"))
            self.assertEqual(one.get(f"/jobs/{slow}/files/audio").status_code, 200)
            self.assertEqual(two.get(f"/jobs/{quick}/files/audio").status_code, 200)


def _no_grace(init):
    """PreviewStore without the start-up grace period, so the test exercises the lock alone."""

    def wrapped(self, root, keep=5, stale_s=0):
        init(self, root, keep=keep, stale_s=0)

    return wrapped


class _ToneTTS:
    """Writes a short tone instead of speaking, so preview_audio can run without coqui-tts."""

    def synthesize(self, text, out_wav):
        import soundfile

        sr = 22050
        t = np.arange(int(sr * (0.3 + 0.02 * len(text)))) / sr
        soundfile.write(out_wav, 0.2 * np.sin(2 * np.pi * 220 * t), sr)
        return out_wav


class _MissingTTS:
    def synthesize(self, text, out_wav):
        raise ModuleNotFoundError("No module named 'TTS'")


@unittest.skipUnless(REAL_FFMPEG, "FFmpeg not installed")
class PreviewAudioIntegrationTests(unittest.TestCase):
    def test_missing_tts_is_an_actionable_error(self):
        from src.studio.jobs import UserFacingError

        req = AudioPreviewRequest.model_validate({"cast": [{"name": "A"}], "script": [{"speaker": "A", "text": "Hi"}]})
        out = Path(tempfile.mkdtemp(prefix="gladtalker_preview_"))
        try:
            ctx = SimpleNamespace(set_stages=lambda s: None, progress=lambda *a, **k: None)
            with self.assertRaisesRegex(UserFacingError, "Text-to-speech isn't installed.*coqui-tts"):
                pipeline.preview_audio(ctx, req, out, _MissingTTS())
        finally:
            shutil.rmtree(out, ignore_errors=True)

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
