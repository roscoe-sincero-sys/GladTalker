"""Magic Auto-Cast: find faces in a scene and suggest a render region for each.

Detection uses facexlib's RetinaFace (the same detector SadTalker uses) with a permissive
confidence floor plus a 2x upscaled pass for small scenes, so stylized/illustrated faces and small
video-call tiles are found. Each face gets a suggested region: the enclosing panel/tile when one
is bounded by long straight edges (video-call grids, comic panels), else a head-and-shoulders box.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from src.studio import REPO_ROOT
from src.studio.schemas import Region

UPSCALE_BELOW_PX = 1600  # also scan at 2x when the longest side is smaller than this
TILE_LINE_MIN_FACE_MULT = 1.5  # a tile border must be at least this many face-sizes long
TILE_MAX_FACE_MULT = 7.0  # a "tile" larger than this many face-sizes is probably the whole scene
DETECTOR_WEIGHTS = "detection_Resnet50_Final.pth"  # what facexlib loads for retinaface_resnet50


@dataclass
class FaceBox:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)


def iou(a: FaceBox, b: FaceBox) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def merge_detections(boxes: Sequence[FaceBox], iou_threshold: float = 0.3) -> list[FaceBox]:
    """Greedy non-maximum suppression across detection passes (highest score wins)."""
    kept: list[FaceBox] = []
    for box in sorted(boxes, key=lambda b: b.score, reverse=True):
        if all(iou(box, k) < iou_threshold for k in kept):
            kept.append(box)
    return kept


def reading_order(faces: Sequence[FaceBox]) -> list[FaceBox]:
    """Sort faces top-to-bottom in rows, then left-to-right (so badges #1, #2... read naturally)."""
    if not faces:
        return []
    row_h = float(np.median([f.h for f in faces])) or 1.0
    return sorted(faces, key=lambda f: (int(f.center[1] // row_h), f.center[0]))


def head_region(face: FaceBox, width: int, height: int) -> Region:
    """Head-and-shoulders box around a face, clamped to the image."""
    cx = face.center[0]
    w, h = face.w * 2.4, face.h * 2.6
    x1 = max(0.0, cx - w / 2.0)
    y1 = max(0.0, face.y1 - face.h * 0.8)
    x2 = min(float(width), cx + w / 2.0)
    y2 = min(float(height), y1 + h)
    return Region.from_box(x1, y1, x2, y2)


def _row_runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int, int, int]]:
    """Runs of True along each row of ``mask``: (start_col, end_col, row, length), length >= min_len."""
    padded = np.pad(mask.astype(np.int8), ((0, 0), (1, 1)))
    d = np.diff(padded, axis=1)
    starts, ends = np.argwhere(d == 1), np.argwhere(d == -1)  # both row-major, so they pair up
    lengths = ends[:, 1] - starts[:, 1]
    keep = lengths >= min_len
    return [(int(s[1]), int(e[1]) - 1, int(s[0]), int(n))
            for s, e, n in zip(starts[keep], ends[keep], lengths[keep])]


def _line_segments(gray: np.ndarray, min_len: int, max_gap: int = 3) -> tuple[list, list]:
    """Long axis-aligned edge segments: horizontal (x_lo, x_hi, y, length) and vertical
    (y_lo, y_hi, x, length). Deterministic run-length scan of a Canny edge map; small gaps (e.g.
    anti-aliasing or a label overlapping a border) are bridged with a 1-D morphological close."""
    import cv2

    edges = cv2.Canny(gray, 30, 90)
    k = max_gap + 2
    h_mask = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((1, k), np.uint8)) > 0
    v_mask = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((k, 1), np.uint8)) > 0
    horizontal = [(x1, x2, float(y), n) for x1, x2, y, n in _row_runs(h_mask, min_len)]
    vertical = [(y1, y2, float(x), n) for y1, y2, x, n in _row_runs(v_mask.T, min_len)]
    return horizontal, vertical


def find_tile(face: FaceBox, horizontal: list, vertical: list, width: int, height: int,
              others: Sequence[FaceBox] = ()) -> Optional[Region]:
    """Nearest long straight edges on all four sides of ``face`` that form a plausible panel."""
    cx, cy = face.center
    min_len = TILE_LINE_MIN_FACE_MULT * min(face.w, face.h)
    lefts = [x for lo, hi, x, n in vertical if n >= min_len and lo <= cy <= hi and x < face.x1 - 1]
    rights = [x for lo, hi, x, n in vertical if n >= min_len and lo <= cy <= hi and x > face.x2 + 1]
    tops = [y for lo, hi, y, n in horizontal if n >= min_len and lo <= cx <= hi and y < face.y1 - 1]
    bottoms = [y for lo, hi, y, n in horizontal if n >= min_len and lo <= cx <= hi and y > face.y2 + 1]
    if not (lefts and rights and tops and bottoms):
        return None
    x1, x2, y1, y2 = max(lefts), min(rights), max(tops), min(bottoms)
    if (x2 - x1) > TILE_MAX_FACE_MULT * face.w or (y2 - y1) > TILE_MAX_FACE_MULT * face.h:
        return None
    for other in others:
        ox, oy = other.center
        if x1 <= ox <= x2 and y1 <= oy <= y2:
            return None  # shared panel: a head-and-shoulders box separates the characters better
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(width), x2), min(float(height), y2)
    return Region.from_box(x1, y1, x2, y2)


@dataclass
class CastSuggestion:
    index: int
    face: Region
    score: float
    region: Region
    region_source: str  # "tile" | "head"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "badge": f"#{self.index + 1}",
            "face": self.face.model_dump(),
            "score": round(self.score, 4),
            "region": self.region.model_dump(),
            "region_source": self.region_source,
        }


def suggest_cast(image_bgr: np.ndarray, faces: Sequence[FaceBox]) -> list[CastSuggestion]:
    import cv2

    height, width = image_bgr.shape[:2]
    ordered = reading_order(faces)
    horizontal: list = []
    vertical: list = []
    if ordered:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        smallest = min(min(f.w, f.h) for f in ordered)
        horizontal, vertical = _line_segments(gray, max(20, int(TILE_LINE_MIN_FACE_MULT * smallest)))

    out = []
    for i, face in enumerate(ordered):
        others = [f for f in ordered if f is not face]
        tile = find_tile(face, horizontal, vertical, width, height, others)
        out.append(
            CastSuggestion(
                index=i,
                face=Region.from_box(face.x1, face.y1, face.x2, face.y2),
                score=face.score,
                region=tile or head_region(face, width, height),
                region_source="tile" if tile else "head",
            )
        )
    return out


class FaceDetectorService:
    """Lazily loaded, thread-safe RetinaFace detector (CPU by default to leave the GPU to renders)."""

    def __init__(self, device: str = "cpu", model_root: Optional[str] = None):
        self.device = device
        self.model_root = model_root or str(REPO_ROOT / "gfpgan" / "weights")
        self._net = None
        self._lock = threading.Lock()

    def check_model(self) -> None:
        """Verify the RetinaFace weights against their pinned size and SHA-256 before facexlib
        touches them: facexlib would silently use a damaged file, or download a missing one with
        no checksum. Only this one file is required, so casting works before render models are
        installed."""
        from scripts.download_models import expected_for, is_verified

        from src.studio.jobs import UserFacingError

        path = Path(self.model_root) / DETECTOR_WEIGHTS
        expected = expected_for(f"gfpgan/weights/{DETECTOR_WEIGHTS}")
        if not path.is_file():
            raise UserFacingError(
                f"The face-detection model ({DETECTOR_WEIGHTS}) isn't downloaded yet. "
                "Choose Download Models, then try Auto-Cast again."
            )
        if not is_verified(path, expected):
            raise UserFacingError(
                f"The face-detection model ({DETECTOR_WEIGHTS}) is incomplete or damaged. "
                "Choose Download Models to repair it, then try Auto-Cast again."
            )

    def _get(self):
        if self._net is None:
            from facexlib.detection import init_detection_model

            self.check_model()
            self._net = init_detection_model(
                "retinaface_resnet50", half=False, device=self.device, model_rootpath=self.model_root
            )
        return self._net

    def detect(self, image_bgr: np.ndarray, min_confidence: float = 0.2, max_faces: int = 8) -> list[FaceBox]:
        import cv2
        import torch

        height, width = image_bgr.shape[:2]
        scales = [1.0] + ([2.0] if max(height, width) < UPSCALE_BELOW_PX else [])
        found: list[FaceBox] = []
        with self._lock, torch.no_grad():
            net = self._get()
            for scale in scales:
                img = image_bgr if scale == 1.0 else cv2.resize(
                    image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                )
                for det in net.detect_faces(img, min_confidence):
                    x1, y1, x2, y2, score = (float(v) for v in det[:5])
                    if score < min_confidence:
                        continue  # enforce the caller's floor regardless of any detector patching
                    found.append(FaceBox(
                        max(0.0, x1 / scale), max(0.0, y1 / scale),
                        min(float(width), x2 / scale), min(float(height), y2 / scale), score,
                    ))
        faces = [f for f in merge_detections(found) if f.w >= 4 and f.h >= 4]
        return faces[:max_faces]


_fallback_lock = threading.Lock()
_fallback_local = threading.local()
FALLBACK_THRESHOLDS = (0.5, 0.2, 0.05)


def _install_retinaface_fallback() -> None:
    """Patch RetinaFace.detect_faces once; the patch is inert unless enabled on the calling thread."""
    from facexlib.detection.retinaface import RetinaFace

    with _fallback_lock:
        if getattr(RetinaFace.detect_faces, "_gladtalker_fallback", False):
            return
        original = RetinaFace.detect_faces

        def detect_faces_with_fallback(self, image, conf_threshold=0.8, *args, **kwargs):
            dets = original(self, image, conf_threshold, *args, **kwargs)
            if getattr(_fallback_local, "enabled", False):
                for thr in FALLBACK_THRESHOLDS:
                    if len(dets) == 0 and conf_threshold > thr:
                        dets = original(self, image, thr, *args, **kwargs)
            return dets

        detect_faces_with_fallback._gladtalker_fallback = True
        RetinaFace.detect_faces = detect_faces_with_fallback


@contextmanager
def retinaface_fallback():
    """Within this block (on this thread only), retry RetinaFace at lower confidence when the
    strict pass finds no face. SadTalker's preprocessing asks for 0.97 confidence, which drawn and
    stylized characters rarely reach. Auto-cast and other threads are unaffected."""
    _install_retinaface_fallback()
    previous = getattr(_fallback_local, "enabled", False)
    _fallback_local.enabled = True
    try:
        yield
    finally:
        _fallback_local.enabled = previous
