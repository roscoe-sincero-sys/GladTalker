import hashlib
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_models as dm
from src.studio.hardware import models_status
from tests._file_locks import exclusive_open, range_lock, windows_only


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
            if name.endswith(".part"):  # ignore the digest cache's own atomic-write temp files
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


class ChecksumReadinessTests(unittest.TestCase):
    """Same-size files with damaged contents must never count as ready, skipped, or repaired."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gladtalker_verify_"))
        self.dest = self.dir / "model.pth"
        self.data = b"good-model" * 100
        self.expected = (len(self.data), hashlib.sha256(self.data).hexdigest())
        self.damaged = b"X" + self.data[1:]  # same size, different bytes

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_size_damage_is_not_verified(self):
        self.dest.write_bytes(self.damaged)
        self.assertTrue(dm.is_complete(self.dest, self.expected), "size alone looks fine")
        self.assertFalse(dm.is_verified(self.dest, self.expected))
        self.dest.write_bytes(self.data)
        self.assertTrue(dm.is_verified(self.dest, self.expected))

    def _age(self, seconds):
        past = time.time_ns() - int(seconds * 1e9)
        os.utime(self.dest, ns=(past, past))

    def test_digest_cache_hashes_once_and_invalidates_on_change(self):
        self.dest.write_bytes(self.data)
        self._age(60)  # an installed model, written long before it is checked
        with mock.patch.object(dm, "_hash_open_file", wraps=dm._hash_open_file) as hasher:
            self.assertTrue(dm.is_verified(self.dest, self.expected))
            self.assertTrue(dm.is_verified(self.dest, self.expected))
            self.assertEqual(hasher.call_count, 1, "second check must come from the cache")
            self.dest.write_bytes(self.damaged)
            self._age(30)  # rewritten: different mtime invalidates the cache entry
            self.assertFalse(dm.is_verified(self.dest, self.expected))
            self.assertEqual(hasher.call_count, 2)

    def _backup(self, data, age_s):
        backup = self.dir / "backup.pth"
        backup.write_bytes(data)
        past = time.time_ns() - int(age_s * 1e9)
        os.utime(backup, ns=(past, past))  # e.g. restored from an old backup: mtime preserved
        return backup

    def test_replacement_during_hash_never_caches_the_wrong_file(self):
        """A valid backup replaces the damaged file while it is being hashed: the damaged file's
        digest must not be recorded against the restored file."""
        self.dest.write_bytes(self.damaged)
        self._age(60)
        backup = self._backup(self.data, 120)
        backup_ino = os.stat(backup).st_ino
        real_hash, real_stat = dm._hash_open_file, os.stat
        calls, swapped = [], []

        def counting_hash(f):
            calls.append(real_hash(f))
            return calls[-1]

        def stat_with_swap(path, *a, **kw):
            # The hashed file's handle is closed by the time the path is re-checked: that gap
            # (after hashing, before the digest is cached) is where the restore lands.
            if calls and not swapped and os.fspath(path) == os.fspath(self.dest):
                os.replace(backup, self.dest)
                swapped.append(True)
            return real_stat(path, *a, **kw)

        with mock.patch.object(dm, "_hash_open_file", counting_hash), \
                mock.patch.object(dm.os, "stat", stat_with_swap):
            self.assertTrue(dm.is_verified(self.dest, self.expected), "retries on the restored file")
        self.assertEqual(swapped, [True])
        self.assertEqual(calls, [hashlib.sha256(self.damaged).hexdigest(), self.expected[1]])
        entry = dm._load_cache(self.dir)[self.dest.name]
        self.assertEqual((entry["ino"], entry["sha256"]), (backup_ino, self.expected[1]))
        with mock.patch.object(dm, "_hash_open_file", side_effect=AssertionError("re-hashed")):
            self.assertTrue(dm.is_verified(self.dest, self.expected), "and the cached answer is right")

    def test_cache_entry_for_another_file_id_is_ignored(self):
        self.dest.write_bytes(self.damaged)
        self._age(60)
        self.assertFalse(dm.is_verified(self.dest, self.expected))  # cached: damaged
        st = os.stat(self.dest)
        backup = self._backup(self.data, 0)
        os.utime(backup, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size and mtime, other file
        os.replace(backup, self.dest)
        self.assertTrue(dm.is_verified(self.dest, self.expected))

    def test_file_that_keeps_changing_is_not_verified(self):
        self.dest.write_bytes(self.data)
        real_hash = dm._hash_open_file
        touches = []

        def hash_then_touch(f):
            digest = real_hash(f)
            touches.append(1)
            # Strictly increasing mtimes (the Windows clock can return the same time_ns twice).
            stamp = 1_700_000_000_000_000_000 + len(touches) * 1_000_000_000
            os.utime(self.dest, ns=(stamp, stamp))
            return digest

        with mock.patch.object(dm, "_hash_open_file", hash_then_touch):
            with self.assertRaises(dm.FileChangedError):
                dm.is_verified(self.dest, self.expected, use_cache=False)
        self.assertNotIn(self.dest.name, dm._load_cache(self.dir))

    def test_racy_same_mtime_rewrite_is_still_detected(self):
        self.dest.write_bytes(self.data)
        stamp = time.time_ns()
        os.utime(self.dest, ns=(stamp, stamp))
        self.assertTrue(dm.is_verified(self.dest, self.expected))
        # Same-size corruption that lands on the *same* mtime (one timestamp tick) must not be
        # served from the cache.
        self.dest.write_bytes(self.damaged)
        os.utime(self.dest, ns=(stamp, stamp))
        self.assertFalse(dm.is_verified(self.dest, self.expected))

    def test_same_size_damaged_file_is_repaired_not_skipped(self):
        self.dest.write_bytes(self.damaged)
        with mock.patch.object(dm.urllib.request, "urlopen",
                               return_value=FakeResponse(self.data, len(self.data))) as urlopen:
            dm.download_file("https://example.invalid/m", self.dest, progress=lambda d, t: None,
                             expected=self.expected)
        urlopen.assert_called_once()
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertTrue(dm.is_verified(self.dest, self.expected))

    def test_locked_damaged_destination_is_not_reported_repaired(self):
        self.dest.write_bytes(self.damaged)
        with mock.patch.object(dm.urllib.request, "urlopen",
                               return_value=FakeResponse(self.data, len(self.data))), \
                mock.patch.object(dm.os, "replace", side_effect=PermissionError(13, "Access is denied")), \
                mock.patch.object(dm.time, "sleep"):
            with self.assertRaises(PermissionError):
                dm.download_file("https://example.invalid/m", self.dest, progress=lambda d, t: None,
                                 expected=self.expected)
        self.assertEqual(self.dest.read_bytes(), self.damaged, "damaged file is still there")
        self.assertEqual([p.name for p in self.dir.iterdir() if p.suffix == ".part"], [])

    def test_locked_destination_accepted_only_when_another_writer_verified_it(self):
        self.dest.write_bytes(self.data)  # a concurrent downloader already published good bytes
        os.utime(self.dest, ns=(time.time_ns(), time.time_ns()))
        with mock.patch.object(dm.urllib.request, "urlopen",
                               return_value=FakeResponse(self.data, len(self.data))), \
                mock.patch.object(dm.os, "replace", side_effect=PermissionError(13, "Access is denied")), \
                mock.patch.object(dm.time, "sleep"):
            dm.download_file("https://example.invalid/m", self.dest, force=True, progress=lambda d, t: None,
                             expected=self.expected)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_status_and_render_precheck_reject_same_size_damage(self):
        from src.studio.jobs import UserFacingError
        from src.studio.pipeline import check_models

        rel = "checkpoints/SadTalker_V0.0.2_256.safetensors"
        pins = {}
        for model_rel, _ in dm.MODELS:
            path = self.dir / model_rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.data)
            pins[model_rel] = self.expected
        (self.dir / rel).write_bytes(self.damaged)
        with mock.patch.dict(dm.MODEL_CHECKSUMS, pins):
            status = models_status(self.dir)
            entry = next(f for f in status["files"] if f["path"] == rel)
            self.assertEqual((entry["ready"], entry["damaged"], entry["incomplete"]), (False, True, False))
            self.assertEqual(status["ready"], 7)
            with self.assertRaisesRegex(UserFacingError, "damaged: SadTalker_V0.0.2_256.safetensors"):
                check_models(self.dir)


class VerificationStateTests(unittest.TestCase):
    """verification_state(): busy (temporary) vs damaged (needs repair), from one snapshot."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gladtalker_state_"))
        self.dest = self.dir / "model.pth"
        self.data = b"model-bytes" * 100
        self.expected = (len(self.data), hashlib.sha256(self.data).hexdigest())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _age(self):
        past = time.time_ns() - 60 * 1_000_000_000
        os.utime(self.dest, ns=(past, past))

    def _on_first_path_stat(self, action):
        """Run ``action`` just before verification re-reads the path's metadata (after it has read
        the open file), as if another program swapped the file mid-check."""
        real_stat, done = os.stat, []

        def stat(path, *args, **kwargs):
            if not done and Path(path) == self.dest:
                done.append(1)
                action()
            return real_stat(path, *args, **kwargs)

        return mock.patch.object(dm.os, "stat", stat)

    def test_states(self):
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.MISSING)
        self.dest.write_bytes(self.data[:-1])
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.INCOMPLETE)
        self.dest.write_bytes(b"X" + self.data[1:])
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.DAMAGED)
        self.dest.write_bytes(self.data)
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        self.assertEqual(dm.verification_state(self.dir, self.expected), dm.MISSING)  # a folder

    def test_replacement_mid_check_is_rechecked_not_reported_incomplete(self):
        self.dest.write_bytes(self.data[:10])  # a truncated file, about to be replaced by a good one
        good = self.dir / "good.tmp"
        good.write_bytes(self.data)
        with self._on_first_path_stat(lambda: os.replace(good, self.dest)):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)

    def test_replacement_mid_hash_is_rechecked_not_reported_damaged(self):
        self.dest.write_bytes(b"X" + self.data[1:])  # same size, bad bytes, about to be replaced
        good = self.dir / "good.tmp"
        good.write_bytes(self.data)
        with self._on_first_path_stat(lambda: os.replace(good, self.dest)):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        self.assertEqual(dm._load_cache(self.dir)[self.dest.name]["sha256"], self.expected[1])

    def test_file_that_disappears_after_it_was_seen_is_busy(self):
        self.dest.write_bytes(self.data)
        with self._on_first_path_stat(self.dest.unlink):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.MISSING)

    def test_file_that_keeps_changing_is_busy_and_not_cached(self):
        self.dest.write_bytes(self.data)
        real_hash, touches = dm._hash_open_file, []

        def hash_then_touch(f):
            digest = real_hash(f)
            touches.append(1)
            stamp = 1_700_000_000_000_000_000 + len(touches) * 1_000_000_000
            os.utime(self.dest, ns=(stamp, stamp))
            return digest

        with mock.patch.object(dm, "_hash_open_file", hash_then_touch):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)
        self.assertEqual(len(touches), 3)
        self.assertNotIn(self.dest.name, dm._load_cache(self.dir))

    def test_cache_hit_still_needs_a_readable_file(self):
        self.dest.write_bytes(self.data)
        self._age()
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)  # warms the cache
        with mock.patch.object(dm, "_hash_open_file") as hasher:
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        hasher.assert_not_called()
        with mock.patch.object(dm, "open", create=True, side_effect=PermissionError(13, "in use")):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)

    @windows_only
    def test_sharing_violation_after_warming_the_cache_is_busy(self):
        self.dest.write_bytes(self.data)
        self._age()
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        with exclusive_open(self.dest):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)

    @windows_only
    def test_locked_contents_after_warming_the_cache_are_busy(self):
        import msvcrt

        self.dest.write_bytes(self.data)
        self._age()
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        with open(self.dest, "rb+") as holder:
            msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, len(self.data))
            try:
                self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)
            finally:
                holder.seek(0)
                msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, len(self.data))

    @windows_only
    def test_lock_beyond_the_first_read_after_warming_the_cache_is_busy(self):
        """A cached digest can't vouch for a file that another program has locked anywhere in it,
        not just where a first read would land."""
        self.data = os.urandom(3 * 1024 * 1024)  # bigger than one buffered read
        self.expected = (len(self.data), hashlib.sha256(self.data).hexdigest())
        self.dest.write_bytes(self.data)
        self._age()
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        with range_lock(self.dest, offset=len(self.data) - 4096, length=4096):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.BUSY)
            self.assertEqual(dm.verification_state(self.dest, self.expected, use_cache=False), dm.BUSY)
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)

    @windows_only
    def test_held_file_and_read_only_file_are_told_apart(self):
        import stat

        self.dest.write_bytes(self.data)
        self.assertFalse(dm._held_by_another_program(self.dest))
        with exclusive_open(self.dest):
            self.assertTrue(dm._held_by_another_program(self.dest))
        os.chmod(self.dest, stat.S_IREAD)
        self.addCleanup(os.chmod, self.dest, stat.S_IWRITE | stat.S_IREAD)
        self.assertTrue(dm._is_read_only(self.dest))
        self.assertFalse(dm._held_by_another_program(self.dest))

    @windows_only
    def test_another_readers_shared_lock_does_not_make_a_file_busy(self):
        self.dest.write_bytes(self.data)
        self._age()
        self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)
        with range_lock(self.dest, offset=0, length=len(self.data), exclusive=False):
            self.assertEqual(dm.verification_state(self.dest, self.expected), dm.READY)


class DownloadJobTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gladtalker_job_"))
        self.data = b"model-bytes" * 100
        pin = (len(self.data), hashlib.sha256(self.data).hexdigest())
        self.manifest = [("w/busy.pth", "https://example.invalid/busy"),
                         ("w/missing.pth", "https://example.invalid/missing")]
        self.busy = self.root / "w" / "busy.pth"
        for patcher in (mock.patch("src.studio.hardware.model_manifest", return_value=self.manifest),
                        mock.patch.dict(dm.MODEL_CHECKSUMS, {rel: pin for rel, _ in self.manifest})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _busy_when(self, condition):
        """verification_state() reports busy.pth as busy whenever ``condition()`` is true."""
        real_state = dm.verification_state

        def state(dest, expected, **kw):
            if Path(dest) == self.busy and condition():
                return dm.BUSY
            return real_state(dest, expected, **kw)

        return mock.patch.object(dm, "verification_state", state)

    def _serve(self):
        return mock.patch.object(dm.urllib.request, "urlopen",
                                 side_effect=lambda *a, **k: FakeResponse(self.data, len(self.data)))

    def test_busy_file_is_deferred_while_the_missing_one_downloads(self):
        from src.studio.hardware import download_models

        self.busy.parent.mkdir(parents=True)
        self.busy.write_bytes(b"X" + self.data[1:])
        with self._busy_when(lambda: True), self._serve() as urlopen:
            result = download_models(mock.MagicMock(), root=self.root)
        self.assertEqual([c.args[0].full_url for c in urlopen.call_args_list], ["https://example.invalid/missing"])
        self.assertEqual(result["downloaded"], ["w/missing.pth"])
        self.assertEqual(result["deferred"], ["w/busy.pth"])
        self.assertIn("busy.pth", result["message"])
        self.assertNotIn("All models", result["message"])
        self.assertEqual(self.busy.read_bytes(), b"X" + self.data[1:], "a busy file is left alone")

    def test_destination_held_at_publish_is_deferred(self):
        from src.studio.hardware import download_models

        real_replace = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(dst) == self.busy:  # another program grabbed it while we downloaded
                raise PermissionError(13, "in use")
            return real_replace(src, dst, *args, **kwargs)

        with self._serve(), mock.patch.object(dm.os, "replace", replace), mock.patch.object(dm.time, "sleep"),                 mock.patch.object(dm, "_held_by_another_program", lambda path: Path(path) == self.busy):
            result = download_models(mock.MagicMock(), root=self.root)
        self.assertEqual(result["deferred"], ["w/busy.pth"])
        self.assertEqual(result["downloaded"], ["w/missing.pth"])
        self.assertEqual([p.name for p in self.busy.parent.iterdir() if p.suffix == ".part"], [])

    @windows_only
    def test_destination_locked_by_another_program_during_download_is_deferred(self):
        """The lock starts after the pre-download check, so only the publish step can see it."""
        import contextlib

        from src.studio.hardware import download_models

        self.busy.parent.mkdir(parents=True)
        damaged = b"X" + self.data[1:]
        self.busy.write_bytes(damaged)
        with contextlib.ExitStack() as held:
            def urlopen(req, *args, **kwargs):
                if req.full_url.endswith("/busy"):
                    held.enter_context(exclusive_open(self.busy))
                return FakeResponse(self.data, len(self.data))

            with mock.patch.object(dm.urllib.request, "urlopen", urlopen), mock.patch.object(dm.time, "sleep"):
                result = download_models(mock.MagicMock(), root=self.root)
        self.assertEqual(result["deferred"], ["w/busy.pth"])
        self.assertEqual(result["downloaded"], ["w/missing.pth"])
        self.assertEqual(self.busy.read_bytes(), damaged)

    @windows_only
    def test_read_only_destination_fails_the_job_instead_of_waiting(self):
        import stat

        from src.studio.hardware import download_models
        from src.studio.jobs import UserFacingError

        self.busy.parent.mkdir(parents=True)
        damaged = b"X" + self.data[1:]
        self.busy.write_bytes(damaged)
        os.chmod(self.busy, stat.S_IREAD)
        self.addCleanup(os.chmod, self.busy, stat.S_IWRITE | stat.S_IREAD)
        with self._serve(), mock.patch.object(dm.time, "sleep") as sleep:
            with self.assertRaisesRegex(UserFacingError, "isn't allowed to change it.*isn't read-only"):
                download_models(mock.MagicMock(), root=self.root)
        sleep.assert_not_called()  # no point retrying a permission problem
        self.assertEqual(self.busy.read_bytes(), damaged)
        self.assertEqual(dm.verification_state(self.busy, dm.expected_for("w/busy.pth")), dm.DAMAGED)
        self.assertEqual([p.name for p in self.busy.parent.iterdir() if p.suffix == ".part"], [])

    def test_unconfirmed_permission_error_at_publish_stays_a_permission_error(self):
        tmp = self.root / "new.part"
        tmp.write_bytes(self.data)
        with mock.patch.object(dm.os, "replace", side_effect=PermissionError(13, "Access is denied")),                 mock.patch.object(dm.time, "sleep"), mock.patch.object(dm, "_held_by_another_program", return_value=False):
            with self.assertRaises(PermissionError) as caught:
                dm._publish(tmp, self.busy, None)
        self.assertNotIsInstance(caught.exception, dm.DestinationBusyError)

    def test_network_failure_is_reported_even_if_the_file_turns_busy_meanwhile(self):
        """A timeout is a download failure, whatever state the destination is in by then."""
        from src.studio.hardware import download_models
        from src.studio.jobs import UserFacingError

        timed_out = []

        def urlopen(*args, **kwargs):
            timed_out.append(1)  # ...and another program locks the destination at the same moment
            raise OSError(110, "timed out")

        with self._busy_when(lambda: bool(timed_out)), mock.patch.object(dm.urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(UserFacingError, "Couldn't download busy.pth: timed out"):
                download_models(mock.MagicMock(), root=self.root)

    def test_download_file_reports_a_busy_destination_as_such(self):
        self.busy.parent.mkdir(parents=True)
        self.busy.write_bytes(self.data)
        with self._busy_when(lambda: True), self._serve() as urlopen:
            with self.assertRaises(dm.DestinationBusyError):
                dm.download_file("https://example.invalid/busy", self.busy, progress=lambda d, t: None,
                                 expected=dm.expected_for("w/busy.pth"))
        urlopen.assert_not_called()

    def test_a_real_download_failure_is_still_an_error(self):
        from src.studio.hardware import download_models
        from src.studio.jobs import UserFacingError

        with mock.patch.object(dm.urllib.request, "urlopen", side_effect=OSError(110, "timed out")):
            with self.assertRaisesRegex(UserFacingError, "Couldn't download busy.pth"):
                download_models(mock.MagicMock(), root=self.root)


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
