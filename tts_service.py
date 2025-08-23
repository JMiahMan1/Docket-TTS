import re
import json
import inflect
from pathlib import Path
import pyttsx3

# Load normalization rules
NORMALIZATION = json.loads(Path("normalization.json").read_text(encoding="utf-8"))
ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
CONTRACTIONS = NORMALIZATION.get("contractions", {})
SYMBOLS = NORMALIZATION.get("symbols", {})
PUNCTUATION = NORMALIZATION.get("punctuation", {})

_inflect = inflect.engine()


def normalize_text(text: str) -> str:
    """Make text more TTS-friendly."""

    # Expand abbreviations
    for abbr, expanded in ABBREVIATIONS.items():
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)

    # Handle Bible references with ff/f
    def bible_ff_repl(match):
        book = match.group(1)
        verse = _inflect.number_to_words(match.group(2))
        suffix = match.group(3).lower()
        if suffix in BIBLE_REFS:
            return f"{book} verse {verse} {BIBLE_REFS[suffix]}"
        return match.group()
    text = re.sub(r"([A-Za-z]+\s?\d*):(\d+)(ff|f)\b", bible_ff_repl, text)

    # Expand contractions
    for contr, expanded in CONTRACTIONS.items():
        text = re.sub(rf"\b{re.escape(contr)}\b", expanded, text, flags=re.IGNORECASE)

    # Expand symbols
    for sym, expanded in SYMBOLS.items():
        text = text.replace(sym, expanded)

    # Smooth punctuation
    for p, repl in PUNCTUATION.items():
        text = text.replace(p, repl)

    # Expand numbers
    def expand_number(match):
        return _inflect.number_to_words(match.group(), andword="")
    text = re.sub(r"\b\d+\b", expand_number, text)

    return text.strip()


class TTSService:
    def __init__(self, voice: str = None, rate: int = 180, volume: float = 1.0):
        self.engine = pyttsx3.init()
        self.voice = voice
        self.rate = rate
        self.volume = volume

        # Set voice if provided
        if self.voice:
            voices = self.engine.getProperty('voices')
            for v in voices:
                if self.voice.lower() in v.name.lower():
                    self.engine.setProperty('voice', v.id)
                    break

        self.engine.setProperty('rate', self.rate)
        self.engine.setProperty('volume', self.volume)

    def synthesize(self, text: str, output_path: str):
        text = normalize_text(text)

        # Fix colon truncation
        text = text.replace(":", ",")

        self.engine.save_to_file(text, output_path)
        self.engine.runAndWait()
        return output_path
