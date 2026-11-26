import hashlib
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import torch

from scripts import download_models as dm
from src.studio.jobs import RetryLaterError, UserFacingError
from src.studio.model_reads import explain_checkpoint_reads
from tests._file_locks import exclusive_open, range_lock, windows_only


class ExplainCheckpointReadsTests(unittest.TestCase):
    """Only a failed read of a pinned model file is explained, and only by checking that file."""

    GFPGAN = "gfpgan/weights/GFPGANv1.4.pth"
    DETECTOR = "gfpgan/weights/detection_Resnet50_Final.pth"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gladtalker_reads_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.data = b"not a torch file" * 64  # torch.load always fails on it; the pin says it's intact
        pins = {}
        for rel in (self.GFPGAN, self.DETECTOR):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.data)
            pins[rel] = (len(self.data), hashlib.sha256(self.data).hexdigest())
        patcher = mock.patch.dict(dm.MODEL_CHECKSUMS, pins)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, rel):
        return self.root / rel

    def _states_checked(self):
        return mock.patch.object(dm, "verification_state", wraps=dm.verification_state)

    def test_busy_model_file_read_becomes_retry_advice(self):
        with mock.patch.object(dm, "verification_state", return_value=dm.BUSY) as state, \
                explain_checkpoint_reads(self.root):
            with self.assertRaisesRegex(RetryLaterError, "GFPGANv1.4.pth is being updated") as caught:
                torch.load(str(self.path(self.GFPGAN)), weights_only=True)
        self.assertEqual(Path(state.call_args.args[0]), self.path(self.GFPGAN).resolve())  # only that file
        self.assertEqual(state.call_count, 1)
        self.assertIsNotNone(caught.exception.__cause__)

    @windows_only
    def test_model_file_locked_by_another_program_becomes_retry_advice(self):
        path = self.path(self.DETECTOR)
        with range_lock(path, offset=0, length=len(self.data)), explain_checkpoint_reads(self.root):
            with self.assertRaises(RetryLaterError):
                torch.load(str(path), weights_only=True)
        with exclusive_open(path), explain_checkpoint_reads(self.root):
            with self.assertRaises(RetryLaterError):
                torch.load(str(path), weights_only=True)

    def test_damaged_model_file_read_says_to_repair(self):
        self.path(self.GFPGAN).write_bytes(b"X" + self.data[1:])
        with explain_checkpoint_reads(self.root):
            with self.assertRaisesRegex(UserFacingError, "GFPGANv1.4.pth is damaged. Choose Download Models"):
                torch.load(str(self.path(self.GFPGAN)), weights_only=True)

    def test_healthy_model_file_keeps_the_original_error(self):
        with explain_checkpoint_reads(self.root):
            with self.assertRaises(Exception) as caught:
                torch.load(str(self.path(self.GFPGAN)), weights_only=True)
        self.assertNotIsInstance(caught.exception, UserFacingError)

    def test_safetensors_reads_are_explained_too(self):
        import safetensors.torch

        self.path(self.GFPGAN).write_bytes(b"X" + self.data[1:])
        with explain_checkpoint_reads(self.root):
            with self.assertRaisesRegex(UserFacingError, "is damaged"):
                safetensors.torch.load_file(str(self.path(self.GFPGAN)))

    def test_loaders_keep_their_call_signatures_inside_and_after_the_block(self):
        """Every supported call form still works, by position or by keyword, both while a block is
        active and after it has ended (the wrappers stay installed for the whole process)."""
        import safetensors.torch

        tensors = {"w": torch.arange(4, dtype=torch.float32)}
        st_path, pt_path = self.root / "ok.safetensors", self.root / "ok.pt"
        safetensors.torch.save_file(tensors, str(st_path))
        torch.save(tensors, str(pt_path))

        def read_all():
            for loaded in (safetensors.torch.load_file(str(st_path)),
                           safetensors.torch.load_file(filename=str(st_path), device="cpu"),
                           torch.load(str(pt_path), weights_only=True),
                           torch.load(f=str(pt_path), map_location="cpu", weights_only=True)):
                self.assertTrue(torch.equal(loaded["w"], tensors["w"]))

        read_all()
        with explain_checkpoint_reads(self.root):
            read_all()
        read_all()

    def test_a_failed_keyword_read_is_explained_too(self):
        import safetensors.torch

        self.path(self.GFPGAN).write_bytes(b"X" + self.data[1:])
        with explain_checkpoint_reads(self.root):
            with self.assertRaisesRegex(UserFacingError, "is damaged"):
                safetensors.torch.load_file(filename=str(self.path(self.GFPGAN)), device="cpu")
            with self.assertRaisesRegex(UserFacingError, "is damaged"):
                torch.load(f=str(self.path(self.GFPGAN)), weights_only=True)

    @windows_only
    def test_a_differently_cased_path_to_a_pinned_file_is_still_that_file(self):
        """Windows paths ignore case: "GFPGAN/WEIGHTS" on disk and "gfpgan/weights" in the pin are
        the same file, so its failed read gets the same explanation."""
        shouty = self.root / "SHOUTY"
        (shouty / "GFPGAN" / "WEIGHTS").mkdir(parents=True)
        on_disk = shouty / "GFPGAN" / "WEIGHTS" / "GFPGANV1.4.PTH"
        on_disk.write_bytes(b"X" + self.data[1:])
        asked = shouty / "gfpgan" / "weights" / "GFPGANv1.4.pth"
        with explain_checkpoint_reads(self.root / "shouty"):
            with self.assertRaisesRegex(UserFacingError, "is damaged"):
                torch.load(str(asked), weights_only=True)
            with self.assertRaisesRegex(UserFacingError, "is damaged"):
                torch.load(str(on_disk), weights_only=True)

    def test_out_of_memory_with_an_unrelated_locked_checkpoint_keeps_its_message(self):
        """Moving a model to the device isn't a model read: its failure propagates as itself and
        nothing is rehashed, even while another checkpoint is busy."""
        def move_to_device():
            raise torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")

        with mock.patch.object(dm, "verification_state", return_value=dm.BUSY) as state, \
                explain_checkpoint_reads(self.root):
            with self.assertRaisesRegex(torch.OutOfMemoryError, "out of memory"):
                move_to_device()
        state.assert_not_called()

    def test_reads_outside_the_block_or_outside_the_models_are_untouched(self):
        self.path(self.GFPGAN).write_bytes(b"X" + self.data[1:])
        with self._states_checked() as state:
            with self.assertRaises(Exception) as outside:
                torch.load(str(self.path(self.GFPGAN)), weights_only=True)
            self.assertNotIsInstance(outside.exception, UserFacingError)
            other = self.root / "notes.pth"
            other.write_bytes(b"junk")
            with explain_checkpoint_reads(self.root), self.assertRaises(Exception) as unpinned:
                torch.load(str(other), weights_only=True)
            self.assertNotIsInstance(unpinned.exception, UserFacingError)
        state.assert_not_called()

    def test_another_threads_block_does_not_explain_this_threads_reads(self):
        self.path(self.GFPGAN).write_bytes(b"X" + self.data[1:])
        entered, release, errors = threading.Event(), threading.Event(), []

        def hold_block():
            with explain_checkpoint_reads(self.root):
                entered.set()
                release.wait(5)

        worker = threading.Thread(target=hold_block)
        worker.start()
        try:
            entered.wait(5)
            try:
                torch.load(str(self.path(self.GFPGAN)), weights_only=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(len(errors), 1)
        self.assertNotIsInstance(errors[0], UserFacingError)


if __name__ == "__main__":
    unittest.main()
