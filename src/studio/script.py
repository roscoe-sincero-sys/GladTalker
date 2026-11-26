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

from src.studio.schemas import name_key
from src.utils.tts_text import TTS_CONTRACTIONS, normalize_tts_text

MAX_RECOMMENDED_LINE_CHARS = 250

# Optional bullet/quote marker, optional markdown emphasis, a speaker name that starts with a
# letter (up to 40 chars, at most 4 words), an optional parenthetical, then a colon.
_SPEAKER_LINE_RE = re.compile(
    r"""^\s*
        (?:[-*•>]\s+)?                 # bullet / quote marker
        (?:\*\*|__|\*|_)?\s*                # opening emphasis
        (?P<name>[^\W\d_][\w.'’-]*(?:\s+[\w.'’-]+){0,3}?)
        \s*(?:\*\*|__|\*|_)?                # closing emphasis before a parenthetical
        \s*(?:\((?P<note>[^)]{0,60})\))?    # (parenthetical)
        \s*(?:\*\*|__|\*|_)?                # closing emphasis before the colon
        \s*:                                # the colon
        (?:\*\*|__|\*|_)?                   # closing emphasis after the colon
        \s*(?P<text>.*)$""",
    re.VERBOSE,
)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]")


_EMPH = r"(?:\*\*|__|\*|_)?"
# Lazy: try the line *without* an opening emphasis marker first, so a cast name that starts with
# an underscore ("_Crew") wins over a shorter name wrapped in emphasis ("Crew").
_OPEN_EMPH = r"(?:\*\*|__|\*|_)??"


def _known_speaker_re(cast: list[str]) -> Optional[re.Pattern]:
    """Same shape as _SPEAKER_LINE_RE, but the name must be one of the cast (any length, any
    case, flexible spacing). Tried first so long names like "The Man in the Hat" are recognised
    instead of being folded into the previous speaker's line. Longest names win, and a name's own
    underscores ("Crew_", "_Crew") are kept rather than read as Markdown emphasis."""
    if not cast:
        return None
    names = sorted({c for c in cast if c.strip()}, key=len, reverse=True)
    alternatives = "|".join(r"\s+".join(re.escape(part) for part in n.split()) for n in names)
    return re.compile(
        rf"^\s*(?:[-*•>]\s+)?{_OPEN_EMPH}\s*"
        rf"(?P<name>{alternatives})"
        rf"\s*{_EMPH}\s*(?:\((?P<note>[^)]{{0,60}})\))?\s*{_EMPH}\s*:{_EMPH}"
        r"\s*(?P<text>.*)$",
        re.IGNORECASE,
    )


def _header_candidates(line: str) -> list[str]:
    """Ways to read a screenplay header line ("BRAIN", "**Crew_:**") as a bare name, most literal
    first: as typed, then without asterisks, then without any emphasis markers."""
    literal = line.strip().rstrip(":").strip()
    no_stars = literal.strip("* ").rstrip(":").strip("* ")
    bare = literal.strip("*_ ").rstrip(":").strip("*_ ")
    return list(dict.fromkeys(c for c in (literal, no_stars, bare) if c))


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
    # Normalized key -> the label exactly as the cast defines it (first one wins).
    canonical: dict[str, str] = {}
    for c in cast_list:
        canonical.setdefault(name_key(c), c)
    canonical_cast = set(canonical)  # keys of the cast itself (canonical also learns other names)
    known_re = _known_speaker_re(cast_list)

    lines: list[ParsedLine] = []
    pending_speaker: Optional[str] = None
    pending_note: Optional[str] = None
    current: Optional[ParsedLine] = None

    def canon(name: str) -> str:
        # A cast member keeps their stored spelling (even "Officer  Mia" with two spaces, or
        # "Crew_" with a literal underscore), so look the name up before tidying it. Other names
        # lose stray emphasis markers, and their first spelling is reused for later variants.
        if (stored := canonical.get(name_key(name))) is not None:
            return stored
        name = re.sub(r"\s+", " ", name.strip().strip("*_").strip())
        return canonical.setdefault(name_key(name), name)

    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            current = None  # a blank line ends continuation
            continue

        # Screenplay style: a line that is just a known cast name (optionally with a colon).
        # Compared with name_key (full Unicode case folding, so "STRASSE" is "Straße"), trying the
        # literal label before stripping emphasis, so "_Crew" stays "_Crew" when it is in the cast.
        header = next((c for c in _header_candidates(stripped) if name_key(c) in canonical_cast), None)
        if header is not None:
            pending_speaker, pending_note, current = canonical[name_key(header)], None, None
            continue

        m = (known_re.match(raw) if known_re else None) or _match_speaker_line(raw)
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
    known = {name_key(c) for c in cast_list}
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
        if cast_list and name_key(ln.speaker) not in known:
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
