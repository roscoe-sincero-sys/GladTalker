import json
import logging
import os
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

import numpy as np

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


class LauncherTests(unittest.TestCase):
    def _launch(self, *args):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
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
