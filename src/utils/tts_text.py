"""Lightweight TTS text normalization (no coqui-tts import required)."""

import re

# Character-based models like tacotron2-DDC split "'re" contractions into two syllables ("you re").
TTS_CONTRACTIONS = [
    (re.compile(r"\byou['’]re\b", re.IGNORECASE), "your"),
    (re.compile(r"\bthey['’]re\b", re.IGNORECASE), "their"),
    (re.compile(r"\bwe['’]re\b", re.IGNORECASE), "where"),
]


def normalize_tts_text(text: str) -> str:
    for pattern, repl in TTS_CONTRACTIONS:
        text = pattern.sub(repl, text)
    return text
