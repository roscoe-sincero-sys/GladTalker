import hashlib
import io
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_models as dm
from src.studio.hardware import models_status


class FakeResponse:
    def __init__(self, data: bytes, content_length=None, gate=None):
        self._data = io.BytesIO(data)
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}
        self._gate = gate

    def read(self, n):
        if self._gate is not None:
            self._gate.wait(5)  # hold both downloads in flight at the same time
        return self._data.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gladtalker_dl_"))
        self.dest = self.dir / "model.pth"
        self.data = b"model-bytes" * 100
        self.expected = (len(self.data), hashlib.sha256(self.data).hexdigest())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _download(self, response, **kw):
        with mock.patch.object(dm.urllib.request, "urlopen", return_value=response):
            dm.download_file("https://example.invalid/m", self.dest, progress=lambda d, t: None, **kw)

    def _leftovers(self):
        return [p.name for p in self.dir.iterdir() if p.name.endswith(".part")]

    def test_truncated_body_is_not_published(self):
        with self.assertRaisesRegex(dm.DownloadIntegrityError, "3 of 32 bytes"):
            self._download(FakeResponse(b"abc", content_length=32))
        self.assertFalse(self.dest.exists())
        self.assertEqual(self._leftovers(), [])

    def test_checksum_mismatch_is_not_published(self):
        bad = b"x" * len(self.data)
        with self.assertRaisesRegex(dm.DownloadIntegrityError, "checksum"):
            self._download(FakeResponse(bad, content_length=len(bad)), expected=self.expected)
        self.assertFalse(self.dest.exists())

    def test_size_mismatch_without_content_length(self):
        with self.assertRaisesRegex(dm.DownloadIntegrityError, "expected"):
            self._download(FakeResponse(b"short"), expected=self.expected)
        self.assertFalse(self.dest.exists())

    def test_good_download_verified_and_published(self):
        self._download(FakeResponse(self.data, content_length=len(self.data)), expected=self.expected)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(self._leftovers(), [])

    def test_existing_truncated_file_is_repaired(self):
        self.dest.write_bytes(b"trunc")
        self.assertFalse(dm.is_complete(self.dest, self.expected))
        self._download(FakeResponse(self.data, content_length=len(self.data)), expected=self.expected)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_complete_file_is_skipped(self):
        self.dest.write_bytes(self.data)
        with mock.patch.object(dm.urllib.request, "urlopen") as urlopen:
            dm.download_file("https://example.invalid/m", self.dest, progress=lambda d, t: None,
                             expected=self.expected)
        urlopen.assert_not_called()

    def test_progress_callback_can_abort_and_clean_up(self):
        def abort(done, total):
            raise KeyboardInterrupt

        with mock.patch.object(dm.urllib.request, "urlopen",
                               return_value=FakeResponse(self.data, content_length=len(self.data))):
            with self.assertRaises(KeyboardInterrupt):
                dm.download_file("https://example.invalid/m", self.dest, progress=abort)
        self.assertEqual(self._leftovers(), [])
        self.assertFalse(self.dest.exists())

    def test_concurrent_downloads_use_separate_temp_files(self):
        gate = threading.Event()
        temps = []
        real_mkstemp = dm.tempfile.mkstemp

        def recording_mkstemp(*a, **kw):
            fd, name = real_mkstemp(*a, **kw)
            temps.append(name)
            if len(temps) == 2:
                gate.set()
            return fd, name

        errors = []

        def run():
            try:
                dm.download_file("https://example.invalid/m", self.dest, force=True,
                                 progress=lambda d, t: None, expected=self.expected)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        new_response = lambda *a, **kw: FakeResponse(self.data, len(self.data), gate)  # noqa: E731
        with mock.patch.object(dm.tempfile, "mkstemp", recording_mkstemp), \
                mock.patch.object(dm.urllib.request, "urlopen", side_effect=new_response):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(set(temps)), 2)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(self._leftovers(), [])


class ManifestTests(unittest.TestCase):
    def test_every_model_is_pinned(self):
        for rel, _ in dm.MODELS:
            size, digest = dm.expected_for(rel)
            self.assertGreater(size, 1_000_000)
            self.assertEqual(len(digest), 64)

    def test_models_status_flags_truncated_files(self):
        root = Path(tempfile.mkdtemp(prefix="gladtalker_root_"))
        try:
            rel = dm.MODELS[0][0]
            (root / rel).parent.mkdir(parents=True)
            (root / rel).write_bytes(b"truncated")
            status = models_status(root)
            entry = next(f for f in status["files"] if f["path"] == rel)
            self.assertTrue(entry["exists"])
            self.assertFalse(entry["ready"])
            self.assertTrue(entry["incomplete"])
            self.assertEqual(status["ready"], 0)
            self.assertFalse(status["complete"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
