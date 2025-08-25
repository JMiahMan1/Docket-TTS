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
    BIBLE_BOOKS = NORMALIZATION.get("bible_books", [])
    AMBIGUOUS_BIBLE_ABBRS = NORMALIZATION.get("ambiguous_bible_abbrs", [])
    CASE_SENSITIVE_ABBRS = NORMALIZATION.get("case_sensitive_abbrs", [])
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
else:
    # Provide empty fallbacks if the file is missing
    ABBREVIATIONS, BIBLE_BOOKS, AMBIGUOUS_BIBLE_ABBRS, CASE_SENSITIVE_ABBRS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION = {}, [], [], [], {}, {}, {}, {}

_inflect = inflect.engine()

# --- Scripture Reference Expansion ---
def build_scripture_patterns():
    """Builds two regex patterns: one for ambiguous abbreviations and one for unambiguous ones."""
    all_abbrs = [re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)]
    ambiguous_lower = [a.lower() for a in AMBIGUOUS_BIBLE_ABBRS]
    
    unambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') not in ambiguous_lower]
    ambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') in ambiguous_lower]
    
    ambiguous_pattern = re.compile(
        r"\b(" + "|".join(sorted(ambiguous, key=len, reverse=True)) + r")" +
        r"\s+(\d+):(\d[\d\s,–-]*)\b", # Colon is REQUIRED
        re.IGNORECASE
    )
    unambiguous_pattern = re.compile(
        r"\b(" + "|".join(sorted(unambiguous, key=len, reverse=True)) + r")" +
        r"\s+(\d+)(?::(\d[\d\s,–-]*))?\b", # Colon is OPTIONAL
        re.IGNORECASE
    )
    return ambiguous_pattern, unambiguous_pattern

AMBIGUOUS_PATTERN, UNAMBIGUOUS_PATTERN = build_scripture_patterns()

def expand_scripture_references(text: str) -> str:
    """Finds and expands scripture references, handling ambiguous ones carefully."""
    def replacer(match):
        book_abbr, chapter, verses = match.groups() if len(match.groups()) == 3 else (match.group(1), match.group(2), None)
        book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), ABBREVIATIONS.get(book_abbr, book_abbr))
        chapter_words = _inflect.number_to_words(chapter)
        if not verses: return f"{book_full} chapter {chapter_words}"
        verse_prefix = "verses" if ',' in verses or '-' in verses or '–' in verses else "verse"
        verses = verses.replace('-', ' to ').replace('–', ' to ')
        verse_words = re.sub(r'\d+', lambda m: _inflect.number_to_words(m.group()), verses)
        return f"{book_full} chapter {chapter_words}, {verse_prefix} {verse_words}"
    text = AMBIGUOUS_PATTERN.sub(replacer, text)
    text = UNAMBIGUOUS_PATTERN.sub(replacer, text)
    return text

def normalize_text(text: str) -> str:
    """Cleans and normalizes text to be more TTS-friendly."""
    text = re.sub(r'\[\d+\]|\(\d+\)|\b\d+\)|[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)
    text = expand_scripture_references(text)

    # --- MODIFIED SECTION: Handle Case-Sensitive and Insensitive Abbreviations Separately ---
    non_bible_abbrs = { k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS) }
    
    # 1. Process case-sensitive abbreviations first (no IGNORECASE flag)
    # We strip periods for matching against the case-sensitive list
    case_sensitive_set = {abbr.lower().replace('.', '') for abbr in CASE_SENSITIVE_ABBRS}
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text) # No IGNORECASE

    # 2. Process the remaining abbreviations as case-insensitive
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') not in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)
    # --- END MODIFIED SECTION ---

    def bible_ff_repl(match):
        book, verse = match.group(1), _inflect.number_to_words(match.group(2))
        suffix = BIBLE_REFS.get(match.group(3).lower(), "")
        return f"{book} verse {verse} {suffix}"
    text = re.sub(r"([A-Za-z]+\s?\d*):(\d+)(ff|f)\b", bible_ff_repl, text)

    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)

    # Smarter heading detection
    lines = text.split('\n')
    processed_lines = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            processed_lines.append(line)
            continue
        
        is_mostly_caps_heading = False
        if len(stripped_line) >= 3: 
            letters = [char for char in stripped_line if char.isalpha()]
            if len(letters) > 1:
                uppercase_letters = [char for char in letters if char.isupper()]
                if (len(uppercase_letters) / len(letters)) > 0.75: is_mostly_caps_heading = True

        is_title_case_heading = False
        words = stripped_line.split()
        word_count = len(words)
        if (1 < word_count < 9) and (stripped_line[-1].isalpha()) and (stripped_line[0].isupper()):
            capitalized_words = sum(1 for word in words if word[0].isupper())
            if (capitalized_words / word_count) >= 0.5: is_title_case_heading = True

        if is_mostly_caps_heading or is_title_case_heading:
            processed_lines.append(", .. " + stripped_line + " .. ,")
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)
    
    text = re.sub(r"\b\d+\b", lambda m: _inflect.number_to_words(m.group(), andword=""), text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(":", ",")
    return text

class TTSService:
    """A service to handle text-to-speech conversion using the Piper engine."""
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        self.voice_path = Path(f"/app/voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)
        command = ["piper", "--model", str(self.voice_path), "--output_file", output_path]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True)
        process.communicate(input=normalized_text)
        if process.returncode != 0:
            raise RuntimeError("Piper TTS process failed.")
        return output_path, normalized_text
