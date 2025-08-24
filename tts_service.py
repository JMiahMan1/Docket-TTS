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
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
else:
    # Provide empty fallbacks if the file is missing
    ABBREVIATIONS, BIBLE_BOOKS, AMBIGUOUS_BIBLE_ABBRS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION = {}, [], [], {}, {}, {}, {}

_inflect = inflect.engine()

# --- Scripture Reference Expansion ---
def build_scripture_patterns():
    """Builds two regex patterns: one for ambiguous abbreviations and one for unambiguous ones."""
    all_abbrs = [re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)]
    ambiguous_lower = [a.lower() for a in AMBIGUOUS_BIBLE_ABBRS]
    
    unambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') not in ambiguous_lower]
    ambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') in ambiguous_lower]
    
    # Pattern for ambiguous abbreviations (e.g., Is, Job) - REQUIRES a colon and verse
    ambiguous_pattern = re.compile(
        r"\b(" + "|".join(sorted(ambiguous, key=len, reverse=True)) + r")" +
        r"\s+(\d+):(\d[\d\s,–-]*)\b", # Colon is REQUIRED
        re.IGNORECASE
    )
    
    # Pattern for unambiguous abbreviations (e.g., Gen, Matt) - colon is optional
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
        # Gracefully handle matches with or without a verse group
        book_abbr, chapter, verses = match.groups() if len(match.groups()) == 3 else (match.group(1), match.group(2), None)
        
        book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), ABBREVIATIONS.get(book_abbr, book_abbr))
        chapter_words = _inflect.number_to_words(chapter)
        
        if not verses:
            return f"{book_full} chapter {chapter_words}"

        verse_prefix = "verse"
        if ',' in verses or '-' in verses or '–' in verses:
            verse_prefix = "verses"
            verses = verses.replace('-', ' to ').replace('–', ' to ')
        
        verse_words = re.sub(r'\d+', lambda m: _inflect.number_to_words(m.group()), verses)
        return f"{book_full} chapter {chapter_words}, {verse_prefix} {verse_words}"

    # Apply the strict (ambiguous) pattern first, then the general one
    text = AMBIGUOUS_PATTERN.sub(replacer, text)
    text = UNAMBIGUOUS_PATTERN.sub(replacer, text)
    return text

def normalize_text(text: str) -> str:
    """Cleans and normalizes text to be more TTS-friendly."""
    # Remove common footnote markers like [1], (1), or ¹
    text = re.sub(r'\[\d+\]|\(\d+\)|[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)
    
    # Handle scripture references FIRST, as they are the most specific rule.
    text = expand_scripture_references(text)

    # Create a dictionary of ONLY non-biblical abbreviations
    non_bible_abbrs = {
        k: v for k, v in ABBREVIATIONS.items()
        if not any(book in v for book in BIBLE_BOOKS)
    }
    # Now, only expand general abbreviations, leaving Bible books untouched.
    for abbr, expanded in non_bible_abbrs.items():
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)

    # Handle specific Bible references with 'f' or 'ff'
    def bible_ff_repl(match):
        book, verse = match.group(1), _inflect.number_to_words(match.group(2))
        suffix = BIBLE_REFS.get(match.group(3).lower(), "")
        return f"{book} verse {verse} {suffix}"
    text = re.sub(r"([A-Za-z]+\s?\d*):(\d+)(ff|f)\b", bible_ff_repl, text)

    # Expand contractions, symbols, and punctuation
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
        
        #A line is a heading if the majority of its letters are uppercase.
        is_mostly_caps_heading = False
        if len(stripped_line) >= 3: 
            letters = [char for char in stripped_line if char.isalpha()]
            if len(letters) > 1:
                uppercase_letters = [char for char in letters if char.isupper()]
                if (len(uppercase_letters) / len(letters)) > 0.75:
                    is_mostly_caps_heading = True

        # A line is a heading if it's a short phrase in Title Case.
        is_title_case_heading = False
        words = stripped_line.split()
        word_count = len(words)
        # Must be a short phrase, end with a letter, and start with a capital.
        if (1 < word_count < 9) and (stripped_line[-1].isalpha()) and (stripped_line[0].isupper()):
            # At least half the words must be capitalized.
            capitalized_words = sum(1 for word in words if word[0].isupper())
            if (capitalized_words / word_count) >= 0.5:
                is_title_case_heading = True

        if is_mostly_caps_heading or is_title_case_heading:
            processed_lines.append(stripped_line + ". ,")
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)
    
    # Convert any remaining standalone digits to words
    text = re.sub(r"\b\d+\b", lambda m: _inflect.number_to_words(m.group(), andword=""), text)

    # Final cleanup
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
