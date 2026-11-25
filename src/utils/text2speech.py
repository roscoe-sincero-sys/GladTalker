import os
import re
import tempfile

from TTS.api import TTS


DEFAULT_TTS_MODEL = "tts_models/en/ljspeech/tacotron2-DDC"

# Character-based models like tacotron2-DDC split "'re" contractions into two syllables ("you re").
_TTS_CONTRACTIONS = [
    (re.compile(r"\byou['’]re\b", re.IGNORECASE), "your"),
    (re.compile(r"\bthey['’]re\b", re.IGNORECASE), "their"),
    (re.compile(r"\bwe['’]re\b", re.IGNORECASE), "where"),
]


def normalize_tts_text(text: str) -> str:
    for pattern, repl in _TTS_CONTRACTIONS:
        text = pattern.sub(repl, text)
    return text


class TTSTalker:
    """Text-to-speech helper backed by Idiap's maintained Coqui TTS fork (coqui-tts)."""

    def __init__(self, model_name=DEFAULT_TTS_MODEL) -> None:
        self.model_name = model_name
        self.tts = None

    def _ensure_loaded(self):
        if self.tts is None:
            self.tts = TTS(self.model_name)

    def test(self, text, language="en"):
        if not text or not text.strip():
            return None

        self._ensure_loaded()

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

        kwargs = {}
        if getattr(self.tts, "is_multi_speaker", False) and self.tts.speakers:
            kwargs["speaker"] = self.tts.speakers[0]
        if getattr(self.tts, "is_multi_lingual", False) and language:
            kwargs["language"] = language

        text = normalize_tts_text(text)
        self.tts.tts_to_file(text=text, file_path=wav_path, **kwargs)
        return wav_path
