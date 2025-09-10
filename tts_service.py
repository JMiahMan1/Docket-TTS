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
            return _inflect.number_to_words(integer_val)
        except (KeyError, IndexError):
            return roman_str
    return pattern.sub(replacer, text)

def _format_ref_segment(book_full, chapter, verses_str, last_book_was_same):
    chapter_words = _inflect.number_to_words(int(chapter))
    book_segment = "" if last_book_was_same else f"{book_full} "
    
    if not verses_str:
        return f"{book_segment}chapter {chapter_words}"
    
    suffix = ""
    verses_str = verses_str.strip().rstrip(".;,")

    if verses_str.lower().endswith("ff"):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith("f"):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"

    verses_str = re.sub(r"(\d)([a-z])", r"\1 \2", verses_str, flags=re.IGNORECASE)
    verses_str = verses_str.replace("–", "-").replace("-", " through ")

    prefix = "verses" if any(c in verses_str for c in ",-") else "verse"
    verse_words = re.sub(r"\d+", lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    chapter_segment = "chapter "

    return f"{book_segment}{chapter_segment}{chapter_words}, {prefix} {verse_words}{suffix}"

def normalize_scripture(text: str) -> str:
    all_books = {**ABBREVIATIONS, **{b: b for b in BIBLE_BOOKS}}
    book_keys = sorted(all_books.keys(), key=len, reverse=True)
    book_pattern_str = '|'.join(re.escape(k) for k in book_keys)
    
    # This regex handles multi-book references like "Genesis 50 to Exodus 1"
    multi_book_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+(\d+)\s+to\s+(' + book_pattern_str + r')\s+(\d+)\b', re.IGNORECASE)
    def multi_book_replacer(match):
        book1_abbr, chap1, book2_abbr, chap2 = match.groups()
        book1_full = all_books.get(book1_abbr.strip().rstrip('.'), book1_abbr)
        book2_full = all_books.get(book2_abbr.strip().rstrip('.'), book2_abbr)
        return f"{book1_full} chapter {_inflect.number_to_words(int(chap1))} to {book2_full} chapter {_inflect.number_to_words(int(chap2))}"
    text = multi_book_pattern.sub(multi_book_replacer, text)

    # This regex handles references inside parentheses
    def process_enclosed_refs(match):
        inner_text = match.group(1)
        last_book_full = None
        parts = re.split(';', inner_text)
        processed_parts = []
        for part in parts:
            part = part.strip()
            full_ref_match = re.match(r'(' + book_pattern_str + r')\s*(\d+)[:\.](\d+[\w\s,.\–-]*(?:ff|f)?)', part, re.IGNORECASE)
            subsequent_ref_match = re.match(r'(\d+)[:\.](\d+[\w\s,.\–-]*(?:ff|f)?)', part)

            if full_ref_match:
                book_abbr, chapter, verses = full_ref_match.groups()
                book_full = all_books.get(book_abbr.strip().rstrip('.'), book_abbr)
                processed_parts.append(_format_ref_segment(book_full, chapter, verses, False))
                last_book_full = book_full
            elif subsequent_ref_match and last_book_full:
                chapter, verses = subsequent_ref_match.groups()
                processed_parts.append(_format_ref_segment(last_book_full, chapter, verses, True))
        
        return f"({'; '.join(processed_parts)})"
    text = re.sub(r'\(([^)]+)\)', process_enclosed_refs, text)
    
    # This regex handles standalone prose references
    standalone_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+(\d+)[:\.](\d+[\w\s,.\–-]*(?:ff|f)?)\b', re.IGNORECASE)
    def standalone_replacer(match):
        book_abbr, chapter, verses = match.groups()
        book_full = all_books.get(book_abbr.strip().rstrip('.'), book_abbr)
        return _format_ref_segment(book_full, chapter, verses, False)
    text = standalone_pattern.sub(standalone_replacer, text)
    return text

def number_replacer(match):
    num_str = match.group(0)
    num_int = int(num_str)

    if len(num_str) == 4 and 1100 <= num_int <= 2099:
        if num_int >= 2000:
             return _inflect.number_to_words(num_int, group=0)
        part1 = _inflect.number_to_words(num_str[:2])
        part2 = _inflect.number_to_words(num_str[2:])
        if part2 == "zero": return f"{part1} hundred"
        return f"{part1} {part2}"
        
    return _inflect.number_to_words(num_int)

def normalize_text(text: str) -> str:
    # --- STAGE 1: Pre-processing and Artifact Removal ---
    if SUPERSCRIPTS:
        text = text.translate(str.maketrans('', '', "".join(SUPERSCRIPTS)))
    text = re.sub(r'\[[a-zA-Z0-9]\]', '', text) # Remove footnote markers like [a], [1]

    # Process line-by-line to strip unwanted leading numbers before anything else
    lines = text.split('\n')
    processed_lines = []
    book_name_pattern = r'\b(?:[1-3]|First|Second|Third)\s+[A-Za-z]+'
    for line in lines:
        stripped_line = line.strip()
        # Strip leading numbers (e.g., "11 Paul...") but not from book names (e.g., "1 Corinthians")
        if re.match(r'^\d+\s+', stripped_line) and not re.match(book_name_pattern, stripped_line, re.IGNORECASE):
            processed_lines.append(re.sub(r'^\d+\s+', '', stripped_line))
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)
    
    # --- STAGE 2: Convert Verse Markers and Scripture References ---
    # Handles chapter:verse (e.g., "2:5") and :verse (e.g., ":83")
    def _replace_verse_marker(match):
        chapter, verse = match.groups()
        chapter_words = _inflect.number_to_words(chapter) if chapter else ""
        verse_words = _inflect.number_to_words(verse)
        return f"chapter {chapter_words} verse {verse_words}" if chapter else f"verse {verse_words}"
    text = re.sub(r'\b(?:(\d+):)?(\d+)\b', _replace_verse_marker, text)

    text = normalize_scripture(text)

    # --- STAGE 3: Expand Phrases, Abbreviations, and Symbols ---
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'\b{re.escape(phrase)}\b', replacement, text, flags=re.IGNORECASE)

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
    # All semantic numbers (verses, etc.) are handled, now convert remaining numbers
    text = re.sub(r"\b\d+\b", number_replacer, text)
    
    text = re.sub(r'\n\s*\n', '. \n', text)
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
