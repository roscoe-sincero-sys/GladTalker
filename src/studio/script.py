"""Paste-a-script importer and pronunciation lint for the Script step.

Turns a plain transcript such as::

    Brain: I'll show you how to change the time.
    Pinky (nervously): I'm clicking.
    **Brain:** Click harder.

into speaker-linked dialogue blocks, with non-blocking warnings the UI can show inline.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from src.utils.tts_text import TTS_CONTRACTIONS, normalize_tts_text

MAX_RECOMMENDED_LINE_CHARS = 250

# Optional bullet/quote marker, optional markdown emphasis, a speaker name that starts with a
# letter (up to 40 chars, at most 4 words), an optional parenthetical, then a colon.
_SPEAKER_LINE_RE = re.compile(
    r"""^\s*
        (?:[-*•>]\s+)?                 # bullet / quote marker
        (?:\*\*|__|\*|_)?\s*                # opening emphasis
        (?P<name>[^\W\d_][\w.'’-]*(?:\s+[\w.'’-]+){0,3}?)
        \s*(?:\((?P<note>[^)]{0,60})\))?    # (parenthetical)
        \s*(?:\*\*|__|\*|_)?                # closing emphasis before the colon
        \s*:                                # the colon
        (?:\*\*|__|\*|_)?                   # closing emphasis after the colon
        \s*(?P<text>.*)$""",
    re.VERBOSE,
)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]")


@dataclass
class LintWarning:
    code: str
    message: str
    fix: Optional[str] = None  # replacement text for a one-click "Auto-Fix"


@dataclass
class ParsedLine:
    index: int
    speaker: Optional[str]
    text: str
    note: Optional[str] = None
    source_line: int = 0
    warnings: list[LintWarning] = field(default_factory=list)


@dataclass
class ParseResult:
    lines: list[ParsedLine]
    speakers: list[str]
    unknown_speakers: list[str]

    def to_dict(self) -> dict:
        return {
            "lines": [asdict(line) for line in self.lines],
            "speakers": self.speakers,
            "unknown_speakers": self.unknown_speakers,
            "line_count": len(self.lines),
            "warning_count": sum(len(line.warnings) for line in self.lines),
        }


def lint_line(text: str) -> list[LintWarning]:
    """Return non-blocking pronunciation/quality warnings for one dialogue line."""
    warnings: list[LintWarning] = []

    if not _HAS_LETTER_RE.search(text):
        warnings.append(LintWarning("no_speech", "This line has no words to speak."))
        return warnings

    found = []
    for pattern, repl in TTS_CONTRACTIONS:
        for m in pattern.finditer(text):
            found.append(f"“{repl}” for “{m.group(0)}”")
    if found:
        warnings.append(
            LintWarning(
                "tts_contraction",
                "The tacotron2-DDC voice can split ’re contractions into two words, so "
                f"GladTalker will say {', '.join(found)} when rendering. Use Auto-Fix to show "
                "this in the script.",
                fix=normalize_tts_text(text),
            )
        )

    if len(text) > MAX_RECOMMENDED_LINE_CHARS:
        warnings.append(
            LintWarning(
                "long_line",
                f"This line is {len(text)} characters long. Lines over "
                f"{MAX_RECOMMENDED_LINE_CHARS} characters may be mispronounced or cut off; "
                "consider splitting it into shorter lines.",
            )
        )
    return warnings


def _match_speaker_line(raw: str) -> Optional[re.Match]:
    m = _SPEAKER_LINE_RE.match(raw)
    if not m:
        return None
    # Avoid treating URLs (http://...) or clock times as speakers.
    if m.group("text").startswith("//"):
        return None
    return m


def parse_script(text: str, cast: Iterable[str] = ()) -> ParseResult:
    """Split a pasted transcript into speaker-linked dialogue lines.

    * ``Name: text`` (with optional bullets, markdown bold, or ``Name (note): text``) starts a line.
    * A line consisting only of a known cast name (screenplay style, e.g. ``BRAIN``) sets the
      speaker for the following line(s).
    * Other non-blank lines continue the previous line's text.
    * Speaker names are matched case-insensitively to ``cast``; otherwise the first spelling seen
      is used for all case variants.
    """
    cast_list = [c.strip() for c in cast if c and c.strip()]
    canonical: dict[str, str] = {c.casefold(): c for c in cast_list}

    lines: list[ParsedLine] = []
    pending_speaker: Optional[str] = None
    pending_note: Optional[str] = None
    current: Optional[ParsedLine] = None

    def canon(name: str) -> str:
        name = re.sub(r"\s+", " ", name.strip().strip("*_").strip())
        return canonical.setdefault(name.casefold(), name)

    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            current = None  # a blank line ends continuation
            continue

        # Screenplay style: a line that is just a known cast name (optionally with a colon).
        bare = stripped.strip("*_ ").rstrip(":").strip("*_ ")
        if cast_list and bare.casefold() in {c.casefold() for c in cast_list}:
            pending_speaker, pending_note, current = canon(bare), None, None
            continue

        m = _match_speaker_line(raw)
        if m:
            speaker = canon(m.group("name"))
            body = m.group("text").strip()
            current = ParsedLine(
                index=len(lines), speaker=speaker, text=body, note=m.group("note"), source_line=lineno
            )
            lines.append(current)
            pending_speaker = None
            continue

        if current is not None and current.text:
            current.text = f"{current.text} {stripped}"
            continue
        if current is not None and not current.text:
            current.text = stripped
            continue

        current = ParsedLine(
            index=len(lines),
            speaker=pending_speaker,
            text=stripped,
            note=pending_note,
            source_line=lineno,
        )
        lines.append(current)

    lines = [ln for ln in lines if ln.text]
    known = {c.casefold() for c in cast_list}
    speakers: list[str] = []
    unknown: list[str] = []
    for i, ln in enumerate(lines):
        ln.index = i
        ln.warnings = lint_line(ln.text)
        if ln.speaker is None:
            ln.warnings.insert(
                0,
                LintWarning(
                    "no_speaker",
                    "No speaker found for this line. Start it with a cast name and a colon, "
                    "for example “Brain: Hello.”",
                ),
            )
            continue
        if ln.speaker not in speakers:
            speakers.append(ln.speaker)
        if cast_list and ln.speaker.casefold() not in known:
            if ln.speaker not in unknown:
                unknown.append(ln.speaker)
            ln.warnings.insert(
                0,
                LintWarning(
                    "unknown_speaker",
                    f"“{ln.speaker}” is not in the cast. Add them in the Cast step or "
                    f"choose one of: {', '.join(cast_list)}.",
                ),
            )

    return ParseResult(lines=lines, speakers=speakers, unknown_speakers=unknown)
