import re
import json
import inflect
import subprocess
from pathlib import Path

# Load normalization rules from the JSON file
NORMALIZATION_PATH = Path("normalization.json")
if NORMALIZATION_PATH.exists():
    NORMALIZATION = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
else:
    # Provide empty fallbacks if the file is missing
    ABBREVIATIONS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION = {}, {}, {}, {}, {}

_inflect = inflect.engine()


def normalize_text(text: str) -> str:
    """
    Cleans and normalizes text to be more TTS-friendly by expanding
    abbreviations, numbers, symbols, and handling special punctuation.
    """
    # Expand abbreviations (e.g., "Dr." to "Doctor")
    for abbr, expanded in ABBREVIATIONS.items():
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)

    # Handle specific Bible references (e.g., "Gen 1:1f" to "Genesis verse one and the following verse")
    def bible_ff_repl(match):
        book, verse = match.group(1), _inflect.number_to_words(match.group(2))
        suffix = BIBLE_REFS.get(match.group(3).lower(), "")
        return f"{book} verse {verse} {suffix}"
    text = re.sub(r"([A-Za-z]+\s?\d*):(\d+)(ff|f)\b", bible_ff_repl, text)

    # Expand contractions (e.g., "can't" to "cannot")
    for contr, expanded in CONTRACTIONS.items():
        text = text.replace(contr, expanded)

    # Expand symbols (e.g., "%" to "percent")
    for sym, expanded in SYMBOLS.items():
        text = text.replace(sym, expanded)
        
    # Replace or remove punctuation
    for p, repl in PUNCTUATION.items():
        text = text.replace(p, repl)

    # Convert all remaining digits to words (e.g., "123" to "one hundred twenty-three")
    text = re.sub(r"\b\d+\b", lambda m: _inflect.number_to_words(m.group(), andword=""), text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    
    # Piper works best with clean sentence separation.
    text = text.replace(":", ",").replace(";", ".")

    return text


class TTSService:
    """A service to handle text-to-speech conversion using the Piper engine."""

    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        """
        Initializes the TTS service with a specific voice model.
        
        Args:
            voice (str): The filename of the Piper voice model to use.
        """
        self.voice_path = Path(f"/app/voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        """
        Synthesizes the given text into an MP3 file using Piper.
        
        Args:
            text (str): The text to convert to speech.
            output_path (str): The path to save the generated MP3 file.
        """
        # First, normalize the text to improve TTS quality
        normalized_text = normalize_text(text)

        # Use subprocess to call the piper command
        command = [
            "piper",
            "--model", str(self.voice_path),
            "--output_file", output_path
        ]
        
        # Pipe the text to the Piper process
        process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True)
        process.communicate(input=normalized_text)

        if process.returncode != 0:
            raise RuntimeError("Piper TTS process failed.")
            
        return output_path, normalized_text
