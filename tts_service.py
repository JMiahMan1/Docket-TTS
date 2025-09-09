import re
import json
import inflect
import subprocess
import unicodedata
from pathlib import Path
from argostranslate import translate

# --- Load Normalization Data ---
NORMALIZATION_PATH = Path("normalization.json")
if NORMALIZATION_PATH.exists():
    NORMALIZATION = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
    BIBLE_BOOKS = NORMALIZATION.get("bible_books", [])
    CASE_SENSITIVE_ABBRS = NORMALIZATION.get("case_sensitive_abbrs", [])
    ROMAN_EXCEPTIONS = set(NORMALIZATION.get("roman_numeral_exceptions", []))
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
    LATIN_PHRASES = NORMALIZATION.get("latin_phrases", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
    SUPERSCRIPTS = NORMALIZATION.get("superscripts", [])
else:
    # Fallback to empty structures if the file doesn't exist
    ABBREVIATIONS, BIBLE_BOOKS, CASE_SENSITIVE_ABBRS, ROMAN_EXCEPTIONS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_TRANSLITERATION, SUPERSCRIPTS = {}, [], [], set(), {}, {}, {}, {}, {}, {}, []

_inflect = inflect.engine()

try:
    HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
except Exception:
    HEBREW_TO_ENGLISH = None

def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize('NFD', text)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def normalize_hebrew(text: str) -> str:
    def translate_match(match):
        hebrew_text = match.group(0)
        if HEBREW_TO_ENGLISH:
            translated_text = HEBREW_TO_ENGLISH.translate(hebrew_text)
            return f" , translation from Hebrew: {translated_text} , "
        return " [Hebrew text] "
    return re.sub(r'[\u0590-\u05FF]+', translate_match, text)

def normalize_greek(text: str) -> str:
    return text.translate(str.maketrans(GREEK_TRANSLITERATION))

def roman_to_int(s):
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s, i, num = s.upper(), 0, 0
    while i < len(s):
        if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
            num += roman_map[s[i+1]] - roman_map[s[i]]
            i += 2
        else:
            num += roman_map[s[i]]
            i += 1
    return num

def int_to_roman(num):
    val_map = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    roman_str = ""
    while num > 0:
        for i, r in val_map:
            while num >= i:
                roman_str += r
                num -= i
    return roman_str

def expand_roman_numerals(text: str) -> str:
    pattern = re.compile(r'\b([IVXLCDMivxlcdm]+)\b')
    def replacer(match):
        roman_str = match.group(1)
        if roman_str.upper() in ROMAN_EXCEPTIONS: return roman_str
        try:
            integer_val = roman_to_int(roman_str)
            if int_to_roman(integer_val).lower() != roman_str.lower(): return roman_str
            return f"Roman Numeral {_inflect.number_to_words(integer_val)}"
        except (KeyError, IndexError):
            return roman_str
    return pattern.sub(replacer, text)

def _format_ref_segment(book_full, chapter, verses_str, last_book_was_same):
    chapter_words = _inflect.number_to_words(int(chapter))
    
    # Conditionally add the book name
    book_segment = "" if last_book_was_same else f"{book_full} "
    
    # Handle verse suffixes and ranges
    if not verses_str:
        return f"{book_segment}chapter {chapter_words}"
    
    suffix = ""
    verses_str = verses_str.strip().rstrip(".;,")

    if verses_str.lower().endswith("ff"):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith("f"):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"

    # Handle partial verses like "19a"
    verses_str = re.sub(r"(\d)([a-z])", r"\1 \2", verses_str, flags=re.IGNORECASE)
    
    # Handle ranges like "1-15"
    verses_str = verses_str.replace("–", "-").replace("-", " through ")

    # Determine prefix "verse" or "verses"
    prefix = "verses" if any(c in verses_str for c in ",-") else "verse"
    
    # Convert all remaining numbers in the verse string to words
    verse_words = re.sub(r"\d+", lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    
    return f"{book_segment}chapter {chapter_words}, {prefix} {verse_words}{suffix}"

def normalize_scripture(text: str) -> str:
    # Create a comprehensive pattern for all book abbreviations and full names
    all_books = {**ABBREVIATIONS, **{b: b for b in BIBLE_BOOKS}}
    book_keys = sorted(all_books.keys(), key=len, reverse=True)
    book_pattern_str = '|'.join(re.escape(k) for k in book_keys)

    # Regex to find scripture references. Handles multiple references and ranges.
    ref_pattern = re.compile(
        r'\b(' + book_pattern_str + r')\s*' + # Book name/abbreviation
        r'(\d+)' +                             # Chapter
        r'[:\s]' +                             # Separator
        r'([\d\w\s,.\–-]+(?:ff|f)?)' +         # Verses, ranges, suffixes
        r'((?:\s*;\s*\d+[:\s][\d\w\s,.\–-]+(?:ff|f)?)*)', # Optional additional chapters/verses
        re.IGNORECASE
    )

    last_book_full = None

    def replacer(match):
        nonlocal last_book_full
        book_abbr, main_chapter, main_verses, additional_refs = match.groups()
        
        book_full = all_books.get(book_abbr.strip().rstrip('.'), book_abbr)
        
        # Format the main reference
        formatted_refs = [_format_ref_segment(book_full, main_chapter, main_verses, False)]
        last_book_full = book_full

        # Format any additional references that follow (e.g., in "Gen 1:1; 2:3")
        if additional_refs:
            for part in additional_refs.strip().split(';'):
                part = part.strip()
                if not part: continue
                # Match chapter and verse within the additional part
                m = re.match(r'(\d+)[:\s]([\d\w\s,.\–-]+(?:ff|f)?)', part)
                if m:
                    chapter, verses = m.groups()
                    formatted_refs.append(_format_ref_segment(book_full, chapter, verses, True))

        return '; '.join(formatted_refs)

    return ref_pattern.sub(replacer, text)

def number_replacer(match):
    num_str = match.group(0)
    num_int = int(num_str)

    # Handle years like 1984 -> "nineteen eighty-four"
    if len(num_str) == 4 and 1100 <= num_int <= 1999:
        part1 = _inflect.number_to_words(num_str[:2])
        part2 = _inflect.number_to_words(num_str[2:])
        return f"{part1} {part2}"

    # Handle years like 2005 -> "two thousand five"
    if len(num_str) == 4 and 2000 <= num_int <= 2099:
        return _inflect.number_to_words(num_int, andword="")
        
    # Default number to words for other cases
    return _inflect.number_to_words(num_int, andword="")

def normalize_text(text: str) -> str:
    # --- STAGE 1: Initial Cleanup and Artifact Removal ---
    if SUPERSCRIPTS:
        text = text.translate(str.maketrans('', '', "".join(SUPERSCRIPTS)))
    text = re.sub(r'\[[a-zA-Z]\]', '', text)
    text = re.sub(r'(\d+)([a-zA-Z])', r'\1 \2', text)
    text = re.sub(r'([a-zA-Z.,?!;])(\d+)', r'\1 \2', text)
    
    # --- STAGE 2: Convert Markers, Scripture, and Special Formats ---
    def _replace_leading_verse_marker(match):
        # Handles cases like ":83"
        verse_num = match.group(1)
        return f"verse {_inflect.number_to_words(verse_num)} "
    text = re.sub(r'^\s*:(\d+)\b', _replace_leading_verse_marker, text, flags=re.MULTILINE)
    
    text = re.sub(r"verse\s+([A-Z\s]+)([a-z]+):([a-z]+)", r"\1. verse \3", text)
    text = normalize_scripture(text)
    
    # --- STAGE 3: Expand Phrases and Abbreviations ---
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'\b{re.escape(phrase)}(?!\w)', replacement, text, flags=re.IGNORECASE)

    text = _strip_diacritics(text)
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    text = expand_roman_numerals(text)
    
    non_bible_abbrs = {k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS)}
    for abbr, expanded in non_bible_abbrs.items():
        flags = re.IGNORECASE if abbr not in CASE_SENSITIVE_ABBRS else 0
        text = re.sub(rf"\b{re.escape(abbr)}\b(?!\.)", expanded, text, flags=flags)

    for contr, expanded in CONTRACTIONS.items():
        text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items():
        text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items():
        text = text.replace(p, repl)

    # --- STAGE 4: Final Number Conversion and Formatting ---
    text = re.sub(r'\b\d{1,3}\b', '', text) # Remove leftover standalone verse numbers
    text = re.sub(r"\b\d+\b", number_replacer, text) # Convert remaining numbers
    
    text = re.sub(r'^([A-Z][A-Z0-9\s,.-]{4,})$', r'. ... \1. ... ', text, flags=re.MULTILINE)
    text = re.sub(r'\n\s*\n', '. ... \n', text)
    
    text = re.sub(r"[\[\]()]", " , ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx", speed_rate: str = "1.0"):
        self.voice_path = Path(f"/app/voices/{voice}")
        self.speed_rate = speed_rate
        if not self.voice_path.exists():
            self.voice_path = Path(f"voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)

        piper_command = [
            "piper", 
            "--model", str(self.voice_path),
            "--length_scale", str(self.speed_rate),
            "--output_file", "-"
        ]

        ffmpeg_command = [
            "ffmpeg", "-y", "-f", "s16le", "-ar", "22050", "-ac", "1",
            "-i", "-", "-acodec", "libmp3lame", "-q:a", "2", output_path
        ]

        try:
            piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            piper_process.stdin.write(normalized_text.encode('utf-8'))
            piper_process.stdin.close()
            piper_process.stdout.close()

            _, ffmpeg_err = ffmpeg_process.communicate()

            if piper_process.wait() != 0:
                piper_err = piper_process.stderr.read().decode()
                raise RuntimeError(f"Piper process failed: {piper_err}")

            if ffmpeg_process.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding process failed: {ffmpeg_err.decode()}")
        except FileNotFoundError as e:
            raise RuntimeError(f"Command not found: {e.filename}. Ensure piper-tts and ffmpeg are installed.")

        return output_path, normalized_text
