"""Request/response models for the GladTalker Studio API."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CAST = 8
MIN_SEMITONES, MAX_SEMITONES = -6.0, 6.0

_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def name_key(name: str) -> str:
    """How speaker names are compared everywhere: case-insensitive, whitespace-insensitive."""
    return " ".join(name.split()).casefold()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Region(_Model):
    """Pixel rectangle in source-image coordinates (matches the UI's X / Y / W / H steppers)."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)

    def as_box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @classmethod
    def from_box(cls, x1: float, y1: float, x2: float, y2: float) -> "Region":
        x1, y1 = int(round(x1)), int(round(y1))
        return cls(x=x1, y=y1, w=max(1, int(round(x2)) - x1), h=max(1, int(round(y2)) - y1))


class _Named(_Model):
    """A cast member's name: surrounding whitespace is dropped and blank names are rejected, the
    same for renders and audio previews."""

    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Cast member name must not be blank.")
        return v


class CastMember(_Named):
    region: Region
    semitones: float = Field(0.0, ge=MIN_SEMITONES, le=MAX_SEMITONES)


class DialogueLine(_Model):
    speaker: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=1000)

    @field_validator("speaker", "text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Must not be blank.")
        return v


class Pacing(_Model):
    """Natural pace (``target_duration_s`` unset) or fit the dialogue into a target duration."""

    target_duration_s: Optional[float] = Field(None, gt=0, le=600)
    gap_ms: int = Field(300, ge=0, le=5000, description="Pause between lines at natural pace.")


OutputPreset = Literal["hd720", "fhd1080", "vertical1080", "square1080", "source"]

OUTPUT_PRESETS: dict[str, Optional[tuple[int, int]]] = {
    "hd720": (1280, 720),
    "fhd1080": (1920, 1080),
    "vertical1080": (1080, 1920),
    "square1080": (1080, 1080),
    "source": None,
}


class OutputSettings(_Model):
    preset: OutputPreset = "hd720"
    fit: Literal["fit", "fill"] = Field(
        "fit", description="fit = letter/pillarbox the whole scene; fill = crop to cover the frame."
    )
    background: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    crf: int = Field(18, ge=0, le=51)
    filename: Optional[str] = Field(None, max_length=120)

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stem = _SAFE_STEM_RE.sub("_", v).strip(" ._")
        if stem.lower().endswith(".mp4"):
            stem = stem[:-4]
        return stem or None


class QualitySettings(_Model):
    mode: Literal["standard", "enhanced"] = Field(
        "standard", description="enhanced = GFPGAN face restoration (much slower)."
    )
    expression_scale: float = Field(1.2, ge=0.0, le=3.0)
    batch_size: int = Field(2, ge=1, le=16)
    size: Literal[256, 512] = 256
    still: bool = True


class RenderRequest(_Model):
    image_path: str = Field(min_length=1)
    cast: list[CastMember] = Field(min_length=1, max_length=MAX_CAST)
    script: list[DialogueLine] = Field(min_length=1, max_length=500)
    pacing: Pacing = Field(default_factory=Pacing)
    output: OutputSettings = Field(default_factory=OutputSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    captions: bool = True

    @model_validator(mode="after")
    def _check_cast_and_script(self) -> "RenderRequest":
        _link_script_to_cast([m.name for m in self.cast], self.script)
        return self


def _link_script_to_cast(cast_names: list[str], script: list["DialogueLine"]) -> None:
    """Reject duplicate cast names and unknown speakers; rewrite each line's speaker to the cast's
    exact spelling (names match ignoring case and spacing)."""
    seen: dict[str, str] = {}
    for name in cast_names:
        key = name_key(name)
        if key in seen:
            raise ValueError(f"Cast member name '{name}' is used more than once.")
        seen[key] = name
    for i, line in enumerate(script, start=1):
        canonical = seen.get(name_key(line.speaker))
        if canonical is None:
            raise ValueError(
                f"Line {i}: speaker '{line.speaker}' is not in the cast. "
                f"Known cast: {', '.join(seen.values())}."
            )
        line.speaker = canonical


class Voice(_Named):
    semitones: float = Field(0.0, ge=MIN_SEMITONES, le=MAX_SEMITONES)


class AudioPreviewRequest(_Model):
    """Everything that shapes the soundtrack (no image or regions): for "Play full audio mix"."""

    cast: list[Voice] = Field(min_length=1, max_length=MAX_CAST)
    script: list[DialogueLine] = Field(min_length=1, max_length=500)
    pacing: Pacing = Field(default_factory=Pacing)

    @model_validator(mode="after")
    def _check_cast_and_script(self) -> "AudioPreviewRequest":
        _link_script_to_cast([v.name for v in self.cast], self.script)
        return self


class ScriptParseRequest(_Model):
    text: str = Field(max_length=200_000)
    cast: list[str] = Field(default_factory=list, max_length=MAX_CAST * 4)


class ScriptLintRequest(_Model):
    lines: list[str] = Field(max_length=500)

    @field_validator("lines")
    @classmethod
    def _cap_length(cls, v: list[str]) -> list[str]:
        if any(len(line) > 1000 for line in v):
            raise ValueError("Each line must be at most 1000 characters.")
        return v


class TTSPreviewRequest(_Model):
    text: str = Field(min_length=1, max_length=500)
    semitones: float = Field(0.0, ge=MIN_SEMITONES, le=MAX_SEMITONES)
    tempo: float = Field(1.0, ge=0.5, le=2.0)


class CastDetectRequest(_Model):
    image_path: str = Field(min_length=1)
    min_confidence: float = Field(0.2, ge=0.01, le=0.99)
    max_faces: int = Field(MAX_CAST, ge=1, le=32)


class ModelsDownloadRequest(_Model):
    force: bool = False
