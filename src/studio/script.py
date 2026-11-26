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
from typing import Iterable, Iterator, Optional

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


_EMPHASIS = ("**", "__", "*", "_")
_BULLET_RE = re.compile(r"^\s*(?:[-*•>]\s+)?")
# The end of a label: "<name> (note)" with an optional closing emphasis marker after the note.
_NOTE_RE = re.compile(r"^(?P<label>.+?)\s*\((?P<note>[^()]{0,60})\)\s*(?P<close>\*\*|__|\*|_)?\s*$")
# Room around a cast name in a label: emphasis markers and a "(note)" of up to 60 characters.
# Like name matching, the limit ignores spacing: a label has at most twice as many non-space
# characters as the longest cast name, plus this, however much padding it has.
_LABEL_SLACK = 80
_SPACE_OR_WORD_RE = re.compile(r"\s+|\S+")


def _label_window(line: str, limit: int) -> tuple[str, list[int], bool]:
    """The start of ``line`` in one pass: each run of whitespace becomes one space (as name_key
    sees it), up to ``limit`` non-space characters. Returns the window, the index in ``line`` of
    each of its characters, and whether it reaches the end of the line."""
    out: list[str] = []
    where: list[int] = []
    left = limit
    for m in _SPACE_OR_WORD_RE.finditer(line):
        if m.group().isspace():
            out.append(" ")
            where.append(m.start())
            continue
        take = m.group()[:left]
        out.append(take)
        where.extend(range(m.start(), m.start() + len(take)))
        left -= len(take)
        if left <= 0:
            return "".join(out), where, m.end() == len(line) and len(take) == len(m.group())
    return "".join(out), where, True


def _cast_line_readings(line: str, max_label: int) -> Iterator[tuple[str, tuple[str, ...], Optional[str], Optional[int]]]:
    """Every way ``line`` could start with a speaker label of at most ``max_label`` non-space
    characters, as ``(label, closing markers, note, index of its colon in line, or None)``, longest
    label first, so a complete cast name that contains a colon or parentheses ("Team: Blue",
    "Dr. Moss (Guest)") wins over a shorter one:

    1. the whole line, as a screenplay header, first as a name, then as "name (note)";
    2. at each colon within reach, from the last to the first: the part before it as a name, then
       as "name (note)". The dialogue after the colon is left alone until a label wins.

    Labels are read from a window with whitespace collapsed, so padding never pushes a label out
    of reach, and the work doesn't depend on how long the dialogue is."""
    window, where, whole = _label_window(line, max_label)
    if whole:
        yield window, (), None, None
        if m := _NOTE_RE.match(window):
            yield m.group("label"), (m.group("close"),), m.group("note"), None
    for i in range(len(window) - 1, -1, -1):
        if window[i] != ":":
            continue
        head = window[:i]
        colon = where[i]
        after = next((marker for marker in _EMPHASIS if line.startswith(marker, colon + 1)), None)
        yield head, (after,), None, colon
        if m := _NOTE_RE.match(head):
            yield m.group("label"), (m.group("close"), after), m.group("note"), colon


def _dialogue_after(line: str, colon: Optional[int]) -> str:
    """The text after a winning label's colon, without a closing emphasis marker that follows it."""
    if colon is None:
        return ""
    rest = line[colon + 1:]
    for marker in _EMPHASIS:
        if rest.startswith(marker):
            rest = rest[len(marker):]
            break
    return rest.strip()


def _label_candidates(label: str, closers: Iterable[str] = ()) -> list[str]:
    """Ways to read a speaker label as a name, most literal first:

    1. the label as typed ("_Crew", "Crew_");
    2. without one matched pair of emphasis markers, keeping everything inside ("_Crew_" -> "Crew",
       "**_Crew**" -> "_Crew"), where the closing marker may also come after a parenthetical or
       the colon ("**Brain:**");
    3. without any emphasis markers at either end (the old, permissive reading).

    Inline speakers and screenplay headers both use this order, so a label means the same
    speaker wherever it appears."""
    label = label.strip()
    closers = {c for c in closers if c}
    found = [label]
    for marker in _EMPHASIS:
        if not label.startswith(marker):
            continue
        if label.endswith(marker) and len(label) > 2 * len(marker):
            found.append(label[len(marker):-len(marker)].strip())
        elif marker in closers:
            found.append(label[len(marker):].strip())
    found.append(label.strip("*_ ").strip())
    return [c for c in dict.fromkeys(found) if c]


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

    max_label = 2 * max((len("".join(c.split())) for c in cast_list), default=0) + _LABEL_SLACK

    def cast_member(label: str, closers: Iterable[str] = ()) -> Optional[str]:
        """The cast member a label names, compared with name_key (full Unicode case folding and
        any spacing, exactly as render matching does), or None."""
        key = name_key(label)
        if not any(k in key for k in canonical_cast):  # every reading of a label keeps the name inside
            return None
        for candidate in _label_candidates(label, closers):
            if (key := name_key(candidate)) in canonical_cast:
                return canonical[key]
        return None

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

        # A cast member's label: "Name: text", "- **Name** (note): text", or a screenplay header
        # (just the name, optionally with a note or a colon) that sets the speaker, and the note,
        # for the following lines.
        # The line as typed is tried first, so a cast name that starts like a list marker
        # ("- Operator") is that cast member; only then is a leading bullet removed.
        reading = None
        if cast_list:
            bulleted = raw[_BULLET_RE.match(raw).end():].strip()
            for body in dict.fromkeys((stripped, bulleted)):
                reading = next(((speaker, note, _dialogue_after(body, colon))
                                for label, closers, note, colon in _cast_line_readings(body, max_label)
                                if (speaker := cast_member(label, closers)) is not None), None)
                if reading is not None:
                    break
        if reading is not None:
            speaker, note, body_text = reading
            note = note.strip() if note and note.strip() else None
            if not body_text:
                pending_speaker, pending_note, current = speaker, note, None
                continue
            current = ParsedLine(index=len(lines), speaker=speaker, text=body_text, note=note,
                                 source_line=lineno)
            lines.append(current)
            pending_speaker = None
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
        pending_note = None  # a header's "(quietly)" describes the line right after it

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
