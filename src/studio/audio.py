"""Voices, pitch/tempo shifting, and frame-locked dialogue timelines.

Generalizes the two-voice pipeline from the hand-written render script described in issue #11 to any number
of cast members:

1. Synthesize each line with coqui-tts (tacotron2-DDC).
2. Optionally compute one global tempo so the whole conversation fits a target duration.
3. Pitch-shift each line per speaker (FFmpeg ``asetrate`` + compensating ``atempo``).
4. Lay lines out on a 25 fps timeline and build, per speaker, a track containing only their own
   speaking turns, cut to whole frames, so SadTalker renders each character only while they talk.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from src.studio.ffmpeg import run_ffmpeg

FPS = 25
SAMPLE_RATE = 16000
MIN_GAP_MS = 120  # spacing assumed when estimating how fast lines must play to fit a target
FIT_SAFETY_MS = 200  # head-room left when fitting a target duration
MAX_TEMPO = 2.0  # faster than this is unintelligible; ask for a longer target instead

VOICE_PRESETS: list[dict] = [
    {"id": "neutral", "label": "Neutral", "semitones": 0.0,
     "description": "The base voice with no pitch change."},
    {"id": "authoritative", "label": "Authoritative", "semitones": -1.5,
     "description": "Slightly lower and more commanding."},
    {"id": "warm", "label": "Warm", "semitones": -0.5,
     "description": "A touch lower and softer."},
    {"id": "nervous", "label": "Nervous", "semitones": 2.5,
     "description": "Higher and more anxious."},
]


class DialogueTooLongError(ValueError):
    """The script cannot fit the target duration without speeding speech past MAX_TEMPO."""

    def __init__(self, natural_ms: int, target_ms: int):
        self.natural_ms = natural_ms
        self.target_ms = target_ms
        self.minimum_s = (natural_ms / MAX_TEMPO + FIT_SAFETY_MS) / 1000.0
        super().__init__(
            f"The dialogue needs about {natural_ms / 1000:.1f}s at a natural pace, which cannot fit "
            f"in {target_ms / 1000:.1f}s without speaking faster than {MAX_TEMPO:.1f}×. Choose a "
            f"target of at least {self.minimum_s:.1f}s, shorten the script, or use Natural Pace."
        )


def build_pitch_tempo_filter(sample_rate: int, semitones: float, tempo: float) -> str:
    """FFmpeg audio filter that shifts pitch by ``semitones`` and plays ``tempo``× faster.

    ``asetrate`` raises pitch *and* speed by ``2**(st/12)``; ``atempo`` then corrects speed so the
    net speed-up is exactly ``tempo``. ``atempo`` only accepts 0.5..2.0 per instance, so larger
    factors are chained.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if tempo <= 0:
        raise ValueError("tempo must be positive")
    pitch_factor = 2.0 ** (semitones / 12.0)
    remaining = tempo / pitch_factor
    atempo = []
    while remaining > 2.0:
        atempo.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        atempo.append("atempo=0.5")
        remaining /= 0.5
    atempo.append(f"atempo={remaining:.5f}")
    return f"asetrate={int(round(sample_rate * pitch_factor))},aresample={sample_rate}," + ",".join(atempo)


def shift_audio(in_wav: str, out_wav: str, semitones: float, tempo: float, ffmpeg: Optional[str] = None) -> None:
    """Pitch/tempo-shift ``in_wav`` into a 16 kHz mono ``out_wav`` (what SadTalker expects)."""
    import soundfile

    sr = soundfile.info(in_wav).samplerate
    af = build_pitch_tempo_filter(sr, semitones, tempo)
    run_ffmpeg(["-i", in_wav, "-filter:a", af, "-ar", str(SAMPLE_RATE), "-ac", "1", out_wav], ffmpeg=ffmpeg)


def compute_fit_tempo(raw_durations_ms: Sequence[int], target_ms: int) -> float:
    """Global tempo (>= 1.0) needed for the raw lines to fit ``target_ms``."""
    if not raw_durations_ms:
        return 1.0
    natural = sum(raw_durations_ms) + MIN_GAP_MS * (len(raw_durations_ms) - 1)
    usable = target_ms - FIT_SAFETY_MS
    if usable <= 0:
        raise DialogueTooLongError(natural, target_ms)
    tempo = max(1.0, natural / usable)
    if tempo > MAX_TEMPO:
        raise DialogueTooLongError(natural, target_ms)
    return tempo


@dataclass
class LineSpan:
    index: int
    speaker: str
    text: str
    start_ms: int
    end_ms: int
    frame_start: int
    frame_end: int

    @property
    def frames(self) -> int:
        return self.frame_end - self.frame_start


@dataclass
class DialoguePlan:
    spans: list[LineSpan]
    total_ms: int
    total_frames: int
    gap_ms: int
    fps: int = FPS
    # For each output frame: None (nobody speaking) or (speaker, index into that speaker's frames).
    frame_owner: list[Optional[tuple[str, int]]] = field(default_factory=list)
    speaker_frames: dict[str, int] = field(default_factory=dict)


def plan_dialogue(
    segments: Sequence[tuple[str, str, int]],
    target_ms: Optional[int] = None,
    gap_ms: int = 300,
    fps: int = FPS,
) -> DialoguePlan:
    """Lay out processed ``(speaker, text, duration_ms)`` lines on a frame timeline.

    With ``target_ms`` the leftover time is spread evenly before, between, and after lines and the
    timeline is exactly ``target_ms`` long; otherwise ``gap_ms`` is used everywhere.
    """
    n = len(segments)
    spoken = sum(d for _, _, d in segments)
    if target_ms is not None:
        gap = (target_ms - spoken) // (n + 1) if spoken < target_ms else 0
        total_ms = int(target_ms)
    else:
        gap = int(gap_ms)
        total_ms = spoken + gap * (n + 1)

    total_frames = int(round(total_ms / 1000.0 * fps))
    owner: list[Optional[tuple[str, int]]] = [None] * total_frames
    speaker_frames: dict[str, int] = {}
    spans: list[LineSpan] = []

    cursor = gap
    for i, (speaker, text, dur) in enumerate(segments):
        start, end = cursor, cursor + dur
        cursor = end + gap
        f_start = max(0, min(total_frames, int(round(start * fps / 1000.0))))
        f_end = max(f_start, min(total_frames, int(round(end * fps / 1000.0))))
        local = speaker_frames.setdefault(speaker, 0)
        for k in range(f_end - f_start):
            owner[f_start + k] = (speaker, local + k)
        speaker_frames[speaker] = local + (f_end - f_start)
        spans.append(LineSpan(i, speaker, text, start, min(end, total_ms), f_start, f_end))

    return DialoguePlan(spans, total_ms, total_frames, gap, fps, owner, speaker_frames)


class TTSService:
    """Thread-safe, lazily loaded coqui-tts voice shared by previews and renders."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name
        self._talker = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._talker is not None

    def _get(self):
        if self._talker is None:
            from src.utils.text2speech import DEFAULT_TTS_MODEL, TTSTalker

            self._talker = TTSTalker(self.model_name or DEFAULT_TTS_MODEL)
        return self._talker

    def synthesize(self, text: str, out_wav: str) -> str:
        """Speak ``text`` (already contraction-normalized by TTSTalker) into ``out_wav``."""
        with self._lock:
            wav = self._get().test(text)
        if wav is None:
            raise ValueError("Nothing to speak: the line is empty.")
        shutil.move(wav, out_wav)
        return out_wav

    def preview(self, text: str, semitones: float = 0.0, tempo: float = 1.0, ffmpeg: Optional[str] = None) -> bytes:
        """Return WAV bytes of ``text`` spoken with the given voice settings."""
        with tempfile.TemporaryDirectory(prefix="gladtalker_preview_") as tmp:
            raw = self.synthesize(text, os.path.join(tmp, "raw.wav"))
            out = os.path.join(tmp, "voice.wav")
            shift_audio(raw, out, semitones, tempo, ffmpeg=ffmpeg)
            with open(out, "rb") as f:
                return f.read()


@dataclass
class DialogueAudio:
    plan: DialoguePlan
    tempo: float
    master_wav: str
    speaker_wavs: dict[str, str]


def _load_wav(path: str):
    """``AudioSegment.from_wav`` on a handle we close (pydub leaves paths open, which on Windows
    can stop the work folder from being deleted)."""
    from pydub import AudioSegment

    with open(path, "rb") as f:
        return AudioSegment.from_wav(f)


def _save_wav(segment, path: str) -> None:
    with open(path, "wb") as f:  # pydub's export(path) returns the file still open
        segment.export(f, format="wav")


def build_dialogue_audio(
    lines: Sequence[tuple[str, str]],
    semitones: dict[str, float],
    tts: TTSService,
    work_dir: str,
    target_ms: Optional[int] = None,
    gap_ms: int = 300,
    ffmpeg: Optional[str] = None,
    on_line: Optional[Callable[[int, int], None]] = None,
) -> DialogueAudio:
    """Synthesize, shift, and lay out all lines; write master + per-speaker WAV tracks.

    ``on_line(done, total)`` is called after each TTS line (it may raise to cancel).
    """
    from pydub import AudioSegment

    os.makedirs(work_dir, exist_ok=True)
    n = len(lines)
    raw: list[tuple[str, str, str, int]] = []
    for i, (speaker, text) in enumerate(lines):
        path = tts.synthesize(text, os.path.join(work_dir, f"raw_{i:03d}.wav"))
        raw.append((speaker, text, path, len(_load_wav(path))))
        if on_line:
            on_line(i + 1, n)

    tempo = compute_fit_tempo([d for *_, d in raw], target_ms) if target_ms is not None else 1.0

    processed: list[tuple[str, str, AudioSegment]] = []
    for i, (speaker, text, path, _) in enumerate(raw):
        out = os.path.join(work_dir, f"line_{i:03d}.wav")
        shift_audio(path, out, semitones.get(speaker, 0.0), tempo, ffmpeg=ffmpeg)
        seg = _load_wav(out).set_frame_rate(SAMPLE_RATE).set_channels(1)
        processed.append((speaker, text, seg))

    plan = plan_dialogue([(s, t, len(seg)) for s, t, seg in processed], target_ms, gap_ms)

    master = AudioSegment.silent(duration=plan.total_ms, frame_rate=SAMPLE_RATE)
    tracks: dict[str, AudioSegment] = {}
    for span, (_, _, seg) in zip(plan.spans, processed):
        master = master.overlay(seg, position=span.start_ms)
        if span.frames <= 0:
            continue
        # Cut each turn to whole frames so frame k of a speaker's render lines up with the timeline.
        exact_ms = int(round(span.frames * 1000.0 / plan.fps))
        turn = seg[:exact_ms]
        if len(turn) < exact_ms:
            turn += AudioSegment.silent(duration=exact_ms - len(turn), frame_rate=SAMPLE_RATE)
        tracks[span.speaker] = tracks.get(span.speaker, AudioSegment.empty()) + turn

    master_wav = os.path.join(work_dir, "master.wav")
    _save_wav(master[: plan.total_ms], master_wav)
    speaker_wavs = {}
    for idx, (speaker, track) in enumerate(tracks.items()):
        path = os.path.join(work_dir, f"speaker_{idx:02d}.wav")
        _save_wav(track, path)
        speaker_wavs[speaker] = path
    return DialogueAudio(plan=plan, tempo=tempo, master_wav=master_wav, speaker_wavs=speaker_wavs)
