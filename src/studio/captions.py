"""SRT / WebVTT closed captions generated from the dialogue timeline (WCAG 1.2.2)."""

from __future__ import annotations

from typing import Iterable

from src.studio.audio import LineSpan


def _timestamp(ms: int, sep: str) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def to_srt(spans: Iterable[LineSpan]) -> str:
    """SubRip captions; speakers are identified in the text (``Brain: ...``)."""
    blocks = []
    for n, span in enumerate((s for s in spans if s.end_ms > s.start_ms), start=1):
        blocks.append(
            f"{n}\n{_timestamp(span.start_ms, ',')} --> {_timestamp(span.end_ms, ',')}\n"
            f"{span.speaker}: {_one_line(span.text)}\n"
        )
    return "\n".join(blocks)


def _vtt_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(spans: Iterable[LineSpan]) -> str:
    """WebVTT captions using voice spans (``<v Brain>``) so players can style/announce speakers."""
    out = ["WEBVTT", ""]
    for span in spans:
        if span.end_ms <= span.start_ms:
            continue
        speaker = _vtt_escape(span.speaker)
        out.append(f"{_timestamp(span.start_ms, '.')} --> {_timestamp(span.end_ms, '.')}")
        out.append(f"<v {speaker}>{speaker}: {_vtt_escape(_one_line(span.text))}")
        out.append("")
    return "\n".join(out)
