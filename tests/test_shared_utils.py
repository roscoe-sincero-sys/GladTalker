"""Shared SadTalker helpers touched for Studio: Unicode-safe I/O, FFmpeg override, cleanup."""

import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from src.utils import paste_pic as paste_pic_module
from src.utils import safe_io, videoio

UNICODE_PREFIX = "gladtalker_Zo\u00eb_\u65e5\u672c_"
REAL_FFMPEG = shutil.which("ffmpeg")


def _write_video(path: str, frames: int = 3, size=(32, 32)) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 25, size)
    for i in range(frames):
        w.write(np.full((size[1], size[0], 3), 40 * i, np.uint8))
    w.release()


def _write_wav(path: str, seconds: float = 0.2) -> None:
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * int(16000 * seconds))


class SafeIOTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=UNICODE_PREFIX)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip_in_non_ascii_directory(self):
        path = os.path.join(self.dir, "tile.png")
        img = np.random.default_rng(0).integers(0, 255, (12, 10, 3), dtype=np.uint8)
        self.assertTrue(safe_io.imwrite(path, img))
        out = safe_io.imread(path)
        self.assertIsNotNone(out)
        np.testing.assert_array_equal(out, img)

    def test_missing_or_corrupt_returns_none(self):
        self.assertIsNone(safe_io.imread(os.path.join(self.dir, "missing.png")))
        bad = os.path.join(self.dir, "bad.png")
        Path(bad).write_bytes(b"nope")
        self.assertIsNone(safe_io.imread(bad))

    def test_resolve_ffmpeg_prefers_override(self):
        with mock.patch.dict(os.environ, {"GLADTALKER_FFMPEG": "C:/tools/my-ffmpeg.exe"}):
            self.assertEqual(safe_io.resolve_ffmpeg(), "C:/tools/my-ffmpeg.exe")


class MuxHelperTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=UNICODE_PREFIX)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_uses_override_argument_list_and_temp_beside_output(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"mp4")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        out = os.path.join(self.dir, "out.mp4")
        with mock.patch.dict(os.environ, {"GLADTALKER_FFMPEG": "X:/ffmpeg-override"}), \
                mock.patch.object(videoio.subprocess, "run", fake_run):
            videoio.save_video_with_watermark("in.mp4", "in.wav", out)
        self.assertEqual(calls[0][0], "X:/ffmpeg-override")
        self.assertEqual(os.path.dirname(calls[0][-1]), self.dir, "temp file must sit beside the output")
        self.assertEqual(os.listdir(self.dir), ["out.mp4"])

    def test_failure_raises_and_cleans_up(self):
        def failing_run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"partial")
            return subprocess.CompletedProcess(cmd, 1, "", "Invalid data found")

        out = os.path.join(self.dir, "out.mp4")
        with mock.patch.object(videoio.subprocess, "run", failing_run):
            with self.assertRaisesRegex(RuntimeError, "Invalid data found"):
                videoio.save_video_with_watermark("in.mp4", "in.wav", out)
        self.assertEqual(os.listdir(self.dir), [])

    @unittest.skipUnless(REAL_FFMPEG, "FFmpeg not installed")
    def test_real_mux_with_override_and_no_path(self):
        video, audio, out = (os.path.join(self.dir, n) for n in ("v.mp4", "a.wav", "out.mp4"))
        _write_video(video)
        _write_wav(audio)
        with mock.patch.dict(os.environ, {"GLADTALKER_FFMPEG": REAL_FFMPEG, "PATH": ""}):
            videoio.save_video_with_watermark(video, audio, out)
        self.assertGreater(os.path.getsize(out), 0)


class PastePicCleanupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix=UNICODE_PREFIX)
        self.pic = os.path.join(self.dir, "scene.png")
        safe_io.imwrite(self.pic, np.full((64, 64, 3), 200, np.uint8))  # cv2.imread can't open this path
        self.video = os.path.join(self.dir, "crop.mp4")
        _write_video(self.video)
        self.audio = os.path.join(self.dir, "a.wav")
        _write_wav(self.audio)
        self.crop_info = ((32, 32), (16, 16, 48, 48), (0, 0, 32, 32))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_interrupted_blend_leaves_no_temp_files(self):
        def interrupting(iterable, *a, **kw):
            for i, item in enumerate(iterable):
                if i == 1:
                    raise KeyboardInterrupt("cancelled")
                yield item

        cwd_before = set(os.listdir(os.getcwd()))
        with mock.patch.object(paste_pic_module, "tqdm", interrupting):
            with self.assertRaises(KeyboardInterrupt):
                paste_pic_module.paste_pic(self.video, self.pic, self.crop_info, self.audio,
                                           os.path.join(self.dir, "full.mp4"))
        self.assertEqual(sorted(os.listdir(self.dir)), ["a.wav", "crop.mp4", "scene.png"])
        self.assertEqual(set(os.listdir(os.getcwd())), cwd_before, "nothing may be written to the CWD")

    @unittest.skipUnless(REAL_FFMPEG, "FFmpeg not installed")
    def test_blend_succeeds_in_non_ascii_directory(self):
        full = os.path.join(self.dir, "full.mp4")
        paste_pic_module.paste_pic(self.video, self.pic, self.crop_info, self.audio, full)
        self.assertGreater(os.path.getsize(full), 0)
        self.assertEqual(sorted(os.listdir(self.dir)), ["a.wav", "crop.mp4", "full.mp4", "scene.png"])


if __name__ == "__main__":
    unittest.main()
