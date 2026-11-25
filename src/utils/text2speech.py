import os
import tempfile

from TTS.api import TTS

from src.utils.tts_text import TTS_CONTRACTIONS as _TTS_CONTRACTIONS, normalize_tts_text  # noqa: F401


DEFAULT_TTS_MODEL = "tts_models/en/ljspeech/tacotron2-DDC"


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
