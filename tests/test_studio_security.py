import json
import logging
import os
import shutil
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np

from scripts import download_models as dm
from src.studio.faces import FaceDetectorService, retinaface_fallback
from src.studio.jobs import UserFacingError
from src.studio.pipeline import check_memory_budget, estimate_render_bytes
from src.studio.security import TicketStore, install_log_redaction, origin_allowed, redact

REPO_ROOT = Path(__file__).resolve().parents[1]


class SecurityHelperTests(unittest.TestCase):
    def test_redact(self):
        line = '127.0.0.1:5 - "WebSocket /ws/jobs/abc?ticket=XyZ-123&x=1" [accepted] token=s3cret'
        self.assertEqual(redact(line),
                         '127.0.0.1:5 - "WebSocket /ws/jobs/abc?ticket=[redacted]&x=1" [accepted] token=[redacted]')

    def test_uvicorn_handshake_logs_are_redacted(self):
        install_log_redaction()
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            logging.getLogger("uvicorn.error").info('%s - "WebSocket %s" [accepted]', "127.0.0.1:5",
                                                    "/ws/jobs/abc?ticket=SECRET")
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertIn("ticket=[redacted]", "\n".join(logs.output))

    def test_access_log_redaction_keeps_uvicorn_formatter_working(self):
        import io

        from uvicorn.logging import AccessFormatter

        install_log_redaction()
        logger = logging.getLogger("uvicorn.access")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                             use_colors=False))
        logger.addHandler(handler)
        old_level, old_propagate = logger.level, logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            # Exactly how uvicorn's HTTP protocol logs an access line: five positional args.
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET",
                        "/jobs/abc/files/video?token=SECRET", "1.1", 200)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            logger.propagate = old_propagate
        line = stream.getvalue()
        self.assertNotIn("SECRET", line)
        self.assertIn('"GET /jobs/abc/files/video?token=[redacted] HTTP/1.1" 200', line)
        self.assertNotIn("Logging error", line)

    def test_origin_rules(self):
        allowed = ["tauri://localhost"]
        self.assertTrue(origin_allowed(None, "127.0.0.1:7860", allowed))
        self.assertTrue(origin_allowed("tauri://localhost", "127.0.0.1:7860", allowed))
        self.assertTrue(origin_allowed("http://127.0.0.1:7860", "127.0.0.1:7860", allowed))
        self.assertFalse(origin_allowed("https://evil.example", "127.0.0.1:7860", allowed))
        self.assertFalse(origin_allowed("null", "127.0.0.1:7860", allowed))

    def test_tickets_expire_and_are_single_use(self):
        now = [100.0]
        store = TicketStore(ttl_s=60, clock=lambda: now[0])
        t = store.issue("job1")
        self.assertFalse(store.redeem(t, "job2"), "bound to its job")
        t = store.issue("job1")
        self.assertTrue(store.redeem(t, "job1"))
        self.assertFalse(store.redeem(t, "job1"), "single use")
        t = store.issue("job1")
        now[0] += 61
        self.assertFalse(store.redeem(t, "job1"), "expired")
        self.assertFalse(store.redeem(None, "job1"))


class _FakeNet:
    def detect_faces(self, image, conf_threshold):
        # A permissively patched detector could return low-score faces whatever was asked for.
        return [np.array([10, 10, 30, 30, 0.95]), np.array([40, 10, 60, 30, 0.70])]


class DetectorModelGateTests(unittest.TestCase):
    """Auto-cast must verify its RetinaFace weights before facexlib loads (or fetches) them."""

    REL = "gfpgan/weights/detection_Resnet50_Final.pth"

    def setUp(self):
        import hashlib
        import tempfile

        self.root = Path(tempfile.mkdtemp(prefix="gladtalker_det_"))
        self.data = b"retinaface" * 50
        self.pin = mock.patch.dict(dm.MODEL_CHECKSUMS,
                                   {self.REL: (len(self.data), hashlib.sha256(self.data).hexdigest())})
        self.pin.start()
        self.init = mock.patch("facexlib.detection.init_detection_model", return_value=_FakeNet())
        self.init_mock = self.init.start()

    def tearDown(self):
        import shutil

        self.init.stop()
        self.pin.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _detect(self):
        det = FaceDetectorService(model_root=str(self.root))
        return det.detect(np.zeros((2000, 2000, 3), np.uint8), min_confidence=0.5)

    def test_missing_weights_ask_for_download_without_loading(self):
        with self.assertRaisesRegex(UserFacingError, "isn't downloaded yet.*Download Models"):
            self._detect()
        self.init_mock.assert_not_called()

    def test_damaged_weights_ask_for_repair_without_loading(self):
        (self.root / "detection_Resnet50_Final.pth").write_bytes(b"X" + self.data[1:])  # same size
        with self.assertRaisesRegex(UserFacingError, "incomplete or damaged.*repair"):
            self._detect()
        self.init_mock.assert_not_called()

    def test_verified_weights_load(self):
        (self.root / "detection_Resnet50_Final.pth").write_bytes(self.data)
        self.assertEqual(len(self._detect()), 2)
        self.init_mock.assert_called_once()


class AutoCastFloorTests(unittest.TestCase):
    def test_requested_floor_is_enforced(self):
        det = FaceDetectorService()
        det._net = _FakeNet()
        image = np.zeros((2000, 2000, 3), np.uint8)  # large: single pass, no 2x upscale
        self.assertEqual([round(f.score, 2) for f in det.detect(image, min_confidence=0.9)], [0.95])
        self.assertEqual(len(det.detect(image, min_confidence=0.5)), 2)

    def test_fallback_only_active_inside_render_scope(self):
        from facexlib.detection.retinaface import RetinaFace

        def strict_only(self, image, conf_threshold=0.8, *a, **kw):
            return [] if conf_threshold > 0.5 else [[0, 0, 1, 1, 0.3]]

        saved = RetinaFace.__dict__["detect_faces"]
        try:
            RetinaFace.detect_faces = strict_only  # a fresh, unpatched detector
            with retinaface_fallback():
                self.assertEqual(len(RetinaFace.detect_faces(None, None, 0.97)), 1, "fallback in render scope")
            self.assertEqual(RetinaFace.detect_faces(None, None, 0.97), [], "no fallback outside it")
            self.assertEqual(RetinaFace.detect_faces(None, None, 0.9), [], "auto-cast unaffected")
        finally:
            RetinaFace.detect_faces = saved


class MemoryBudgetTests(unittest.TestCase):
    GIB = 1024 ** 3

    def test_estimate(self):
        self.assertEqual(estimate_render_bytes(1, 256), 256 * 256 * 3 * 4 * 3)

    def test_long_part_refused_with_guidance(self):
        frames = 2 * 60 * 25  # two minutes of one character at 512 px ~ 26 GiB peak
        with self.assertRaisesRegex(UserFacingError, r"Brain speaks for 120s.*256 px"):
            check_memory_budget({"Brain": frames, "Pinky": 10}, 512, available_bytes=16 * self.GIB)

    def test_normal_parts_allowed(self):
        check_memory_budget({"Brain": 591, "Pinky": 388}, 256, available_bytes=8 * self.GIB)  # the 42s scene
        check_memory_budget({}, 512, available_bytes=1)

    def test_tile_buffers_counted_at_full_resolution(self):
        base = estimate_render_bytes(100, 256)
        self.assertEqual(estimate_render_bytes(100, 256, 1920, 1080), base + 1920 * 1080 * 3 * 4)
        # Enhancement adds GFPGAN's input and its 2x output (4x the pixels) per in-flight frame.
        self.assertEqual(estimate_render_bytes(100, 256, 1920, 1080, enhanced=True), base + 1920 * 1080 * 3 * 9)

    def test_full_hd_enhanced_tile_streams_so_length_does_not_multiply_tile_cost(self):
        # 60 s of a Full HD tile, enhanced, at 256 px: the frames are streamed through GFPGAN, so
        # only the 256 px render buffers scale with length (~3.3 GiB), not the 1080p frames.
        check_memory_budget({"Brain": (1500, 1920, 1080)}, 256, available_bytes=8 * self.GIB, enhanced=True)
        # The same part at 512 px does not fit, and the advice mentions both levers.
        with self.assertRaisesRegex(UserFacingError, r"Brain speaks for 60s.*256 px.*Standard instead of Enhanced"):
            check_memory_budget({"Brain": (1500, 1920, 1080)}, 512, available_bytes=8 * self.GIB, enhanced=True)

    def test_huge_tile_alone_can_exceed_budget(self):
        with self.assertRaisesRegex(UserFacingError, "Giant"):
            check_memory_budget({"Tiny": (10, 64, 64), "Giant": (10, 12000, 12000)}, 256,
                                available_bytes=2 * self.GIB, enhanced=True)


class LauncherTests(unittest.TestCase):
    def _launch(self, *args, env=None):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
        env.pop("GLADTALKER_STUDIO_TOKEN", None)
        return subprocess.Popen([sys.executable, str(REPO_ROOT / "studio_server.py"), *args],
                                cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env)

    def test_refuses_network_bind_without_token(self):
        proc = self._launch("--host", "0.0.0.0", "--port", "0")
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("without a token", err)
        self.assertEqual(out, "")

    def test_exits_when_the_parent_process_ends(self):
        """--exit-with-parent: the desktop app crashing (or being killed) must not leave the
        engine running in the background."""
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        proc = self._launch("--port", "0", "--log-level", "warning", "--exit-with-parent", str(parent.pid))
        try:
            self.assertEqual(json.loads(proc.stdout.readline())["event"], "listening")
            time.sleep(1)
            self.assertIsNone(proc.poll(), "the engine runs while its parent does")
            parent.kill()
            parent.wait(timeout=30)
            self.assertEqual(proc.wait(timeout=30), 0)
        finally:
            parent.kill()
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=30)

    def test_open_flag_opens_the_ui_and_never_fails_the_engine(self):
        import studio_server
        from unittest import mock

        self.assertTrue(studio_server.parse_args(["--open"]).open)
        self.assertFalse(studio_server.parse_args([]).open)
        with mock.patch("webbrowser.open") as opened:
            studio_server.open_in_browser("http://localhost:7860/")
        opened.assert_called_once_with("http://localhost:7860/")
        with mock.patch("webbrowser.open", side_effect=RuntimeError("no browser")),                 self.assertLogs(level="WARNING") as logs:
            studio_server.open_in_browser("http://localhost:7860/")  # logs, doesn't raise
        self.assertIn("Open http://localhost:7860/ yourself", logs.output[0])

    def test_wait_for_exit_returns_for_a_missing_process(self):
        from src.studio.parent_watch import wait_for_exit

        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        self.assertTrue(wait_for_exit(gone.pid, poll_s=0.05))

    def test_open_flag_opens_the_announced_address(self):
        """--open opens the same origin as the ready line (and the docs), once the engine is
        ready: not "localhost", which is another browser origin with its own saved project, and
        may resolve to ::1, which isn't bound. A stand-in browser (BROWSER) records the URL."""
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="gladtalker_open_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        record = tmp / "opened.txt"
        fake_browser = tmp / "fake_browser.py"
        # Written to a temporary name, then renamed: the test never reads a half-written file.
        fake_browser.write_text("import os, sys, pathlib\n"
                                f"tmp = pathlib.Path({str(record) + '.tmp'!r})\n"
                                "tmp.write_text(sys.argv[1])\n"
                                f"os.replace(tmp, {str(record)!r})\n", encoding="utf-8")
        env_browser = f'"{sys.executable}" "{fake_browser}" %s'
        proc = self._launch("--port", "0", "--open", "--log-level", "warning", env={"BROWSER": env_browser})
        try:
            event = json.loads(proc.stdout.readline())
            deadline = time.time() + 30
            opened = ""
            while not opened and time.time() < deadline:
                try:
                    opened = record.read_text()
                except (FileNotFoundError, PermissionError):  # not there yet / briefly locked (AV scan)
                    pass
                if not opened:
                    time.sleep(0.1)
            self.assertEqual(opened, f"{event['url']}/")
            self.assertTrue(event["url"].startswith("http://127.0.0.1:"))
        finally:
            proc.terminate()
            proc.communicate(timeout=30)

    def test_ready_line_means_reachable(self):
        proc = self._launch("--port", "0", "--log-level", "warning")
        try:
            line = proc.stdout.readline()
            event = json.loads(line)
            self.assertEqual(event["event"], "listening")
            # Connect immediately, with no retry: the ready line must mean "accepting connections".
            with urllib.request.urlopen(f"{event['url']}/health", timeout=10) as r:
                self.assertEqual(json.load(r)["status"], "ok")
        finally:
            proc.terminate()
            proc.communicate(timeout=30)


if __name__ == "__main__":
    unittest.main()
