import unittest

import numpy as np

from src.studio.faces import FaceBox, find_tile, head_region, merge_detections, reading_order, suggest_cast
from src.studio.jobs import UserFacingError
from src.studio.pipeline import (
    compute_layout,
    hex_to_bgr,
    paste_clipped,
    upscale_factor,
    validate_request,
)
from src.studio.schemas import OutputSettings, RenderRequest


def _request(**overrides):
    base = {
        "image_path": "x.png",
        "cast": [{"name": "Brain", "region": {"x": 757, "y": 37, "w": 216, "h": 116}, "semitones": -1.5},
                 {"name": "Pinky", "region": {"x": 755, "y": 160, "w": 220, "h": 119}, "semitones": 2.5}],
        "script": [{"speaker": "brain", "text": "Click it."}, {"speaker": "Pinky", "text": "I'm clicking."}],
    }
    base.update(overrides)
    return RenderRequest.model_validate(base)


class SchemaTests(unittest.TestCase):
    def test_speakers_canonicalized(self):
        self.assertEqual(_request().script[0].speaker, "Brain")

    def test_unknown_speaker_rejected(self):
        with self.assertRaisesRegex(ValueError, "not in the cast"):
            _request(script=[{"speaker": "Narrator", "text": "Hi"}])

    def test_duplicate_cast_rejected(self):
        cast = [{"name": "A", "region": {"x": 0, "y": 0, "w": 50, "h": 50}},
                {"name": "a", "region": {"x": 0, "y": 0, "w": 50, "h": 50}}]
        with self.assertRaisesRegex(ValueError, "more than once"):
            _request(cast=cast, script=[{"speaker": "A", "text": "Hi"}])

    def test_semitone_range(self):
        cast = [{"name": "A", "region": {"x": 0, "y": 0, "w": 50, "h": 50}, "semitones": 7}]
        with self.assertRaises(ValueError):
            _request(cast=cast, script=[{"speaker": "A", "text": "Hi"}])

    def test_filename_sanitized(self):
        self.assertEqual(OutputSettings(filename="../../evil:name.mp4").filename, "evil_name")
        self.assertIsNone(OutputSettings(filename="...").filename)


class LayoutTests(unittest.TestCase):
    def test_fit_matches_recreate_doc(self):
        lay = compute_layout(977, 580, OutputSettings(preset="hd720", fit="fit"))
        self.assertEqual((lay.width, lay.height), (1280, 720))
        self.assertEqual(round(977 * lay.scale), 1213)
        self.assertEqual((lay.offset_x, lay.offset_y), (33, 0))
        self.assertEqual(lay.map_box((757, 37, 973, 153)),
                         (33 + round(757 * lay.scale), round(37 * lay.scale), 33 + round(973 * lay.scale), round(153 * lay.scale)))

    def test_fill_crops(self):
        lay = compute_layout(977, 580, OutputSettings(preset="vertical1080", fit="fill"))
        self.assertEqual((lay.width, lay.height), (1080, 1920))
        self.assertLess(lay.offset_x, 0)
        self.assertEqual(lay.offset_y, 0)

    def test_source_rounds_to_even(self):
        lay = compute_layout(977, 580, OutputSettings(preset="source"))
        self.assertEqual((lay.width, lay.height, lay.scale), (978, 580, 1.0))

    def test_paste_clipped(self):
        canvas = np.zeros((10, 10, 3), np.uint8)
        patch = np.full((4, 4, 3), 255, np.uint8)
        paste_clipped(canvas, patch, -2, 8)  # only a 2x2 corner lands on the canvas
        self.assertEqual(int(canvas.sum() // 255 // 3), 4)
        self.assertTrue((canvas[8:10, 0:2] == 255).all())
        paste_clipped(canvas, patch, 20, 20)  # fully outside: no-op

    def test_helpers(self):
        self.assertEqual(hex_to_bgr("#ff8000"), (0, 128, 255))
        self.assertEqual(upscale_factor(216, 116), 3)  # matches the recreate doc's 3x tiles
        self.assertEqual(upscale_factor(1000, 800), 1)
        self.assertEqual(upscale_factor(40, 40), 4)

    def test_validate_request(self):
        image = np.zeros((580, 977, 3), np.uint8)
        validate_request(_request(), image)
        with self.assertRaisesRegex(UserFacingError, "outside the 977×200 image"):
            validate_request(_request(), np.zeros((200, 977, 3), np.uint8))
        tiny = [{"name": "A", "region": {"x": 0, "y": 0, "w": 10, "h": 10}}]
        with self.assertRaisesRegex(UserFacingError, "at least 32"):
            validate_request(_request(cast=tiny, script=[{"speaker": "A", "text": "Hi"}]), image)


class FaceTests(unittest.TestCase):
    def test_merge_detections_keeps_best(self):
        boxes = [FaceBox(0, 0, 10, 10, 0.5), FaceBox(1, 1, 11, 11, 0.9), FaceBox(50, 50, 60, 60, 0.3)]
        merged = merge_detections(boxes)
        self.assertEqual([b.score for b in merged], [0.9, 0.3])

    def test_reading_order(self):
        faces = [FaceBox(100, 100, 120, 120, 1), FaceBox(0, 102, 20, 122, 1), FaceBox(50, 0, 70, 20, 1)]
        self.assertEqual([f.x1 for f in reading_order(faces)], [50, 0, 100])

    def test_head_region_clamped(self):
        r = head_region(FaceBox(0, 0, 40, 40, 1), 100, 100)
        self.assertEqual((r.x, r.y), (0, 0))
        self.assertLessEqual(r.x + r.w, 100)
        self.assertLessEqual(r.y + r.h, 100)

    def _grid_image(self):
        import cv2

        img = np.full((400, 600, 3), 235, np.uint8)
        tiles = [(350, 20, 580, 180), (350, 200, 580, 380)]
        for x1, y1, x2, y2 in tiles:
            cv2.rectangle(img, (x1, y1), (x2, y2), (40, 120, 40), 2)
            img[y1 + 3:y2 - 2, x1 + 3:x2 - 2] = (200, 180, 150)
        return img, tiles

    def test_snap_to_tile(self):
        img, tiles = self._grid_image()
        faces = [FaceBox(440, 60, 490, 120, 0.9), FaceBox(440, 240, 490, 300, 0.8)]
        cast = suggest_cast(img, faces)
        self.assertEqual([c.region_source for c in cast], ["tile", "tile"])
        for c, (x1, y1, x2, y2) in zip(cast, tiles):
            self.assertLessEqual(abs(c.region.x - x1), 3)
            self.assertLessEqual(abs(c.region.y - y1), 3)
            self.assertLessEqual(abs(c.region.x + c.region.w - x2), 3)
            self.assertLessEqual(abs(c.region.y + c.region.h - y2), 3)

    def test_shared_panel_falls_back_to_head_region(self):
        img, _ = self._grid_image()
        faces = [FaceBox(370, 60, 420, 120, 0.9), FaceBox(500, 60, 550, 120, 0.9)]
        self.assertEqual([c.region_source for c in suggest_cast(img, faces)], ["head", "head"])

    def test_find_tile_requires_all_sides(self):
        self.assertIsNone(find_tile(FaceBox(10, 10, 20, 20, 1), [], [], 100, 100))


if __name__ == "__main__":
    unittest.main()
