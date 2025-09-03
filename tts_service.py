import re
import json
import inflect
import subprocess
import unicodedata
from pathlib import Path
from argostranslate import translate

NORMALIZATION_PATH = Path("normalization.json")
if NORMALIZATION_PATH.exists():
    NORMALIZATION = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
    BIBLE_BOOKS = NORMALIZATION.get("bible_books", [])
    AMBIGUOUS_BIBLE_ABBRS = NORMALIZATION.get("ambiguous_bible_abbrs", [])
    CASE_SENSITIVE_ABBRS = NORMALIZATION.get("case_sensitive_abbrs", [])
    ROMAN_EXCEPTIONS = set(NORMALIZATION.get("roman_numeral_exceptions", []))
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
    LATIN_PHRASES = NORMALIZATION.get("latin_phrases", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
else:
    ABBREVIATIONS, BIBLE_BOOKS, AMBIGUOUS_BIBLE_ABBRS, CASE_SENSITIVE_ABBRS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_TRANSLITERATION = {}, [], [], [], {}, {}, {}, {}, {}, {}
    ROMAN_EXCEPTIONS = set()

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
    for char, replacement in GREEK_TRANSLITERATION.items():
        text = text.replace(char, replacement)
    return text

def roman_to_int(s):
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s = s.upper()
    i, num = 0, 0
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

# --- UNIFIED SCRIPTURE PARSING LOGIC ---

# Pattern to find a book name (e.g., "Gen" or "1 Cor.")
BOOK_PATTERN_STRING = '|'.join(sorted([re.escape(k) for k in ABBREVIATIONS.keys() if any(book in ABBREVIATIONS[k] for book in BIBLE_BOOKS)], key=len, reverse=True))
BOOK_REGEX = re.compile(r'\b(' + BOOK_PATTERN_STRING + r')', re.IGNORECASE)

# Pattern to find a verse segment (e.g., "17:1-15a", "18:1ff", or just "19b")
VERSE_REGEX = re.compile(r'(\d+)(?::([\d\w\s,–-]*))?')

def _format_single_ref(book_full, chapter, verses_str):
    chapter_words = _inflect.number_to_words(int(chapter))
    if not verses_str: return f"{book_full} chapter {chapter_words}"
    suffix = ""
    verses_str = verses_str.strip()
    if verses_str.lower().endswith('ff'):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith('f'):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"
    
    verse_prefix = "verses" if ',' in verses_str or '-' in verses_str or '–' in verses_str else "verse"
    verses_str = re.sub(r'(\d)([a-z])', r'\1 \2', verses_str, flags=re.IGNORECASE)
    verses_str = verses_str.replace('–', '-').replace('-', ' through ')
    verse_words = re.sub(r'\d+', lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    return f"{book_full} chapter {chapter_words}, {verse_prefix} {verse_words}{suffix}"

def expand_scripture_references(text: str) -> str:
    """The main scripture parsing function, handles simple, chained, and multi-book references."""
    segments = BOOK_REGEX.split(text)
    result = [segments[0]]
    last_book_full = ""
    last_chapter = ""

    i = 1
    while i < len(segments):
        book_abbr = segments[i]
        content = segments[i+1]
        book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), ABBREVIATIONS.get(book_abbr, book_abbr))
        
        verse_match = VERSE_REGEX.match(content.strip())
        if verse_match:
            chapter, verses = verse_match.groups()
            last_book_full = book_full
            last_chapter = chapter
            result.append(_format_single_ref(book_full, chapter, verses or ""))
            
            # Check for chained refs in the rest of the content
            remaining_content = content.strip()[verse_match.end():].strip(' ;')
            if remaining_content:
                chained_refs = re.split(r'\s*;\s*', remaining_content)
                for ref in chained_refs:
                    if not ref: continue
                    if ':' in ref:
                        new_chapter, new_verses = ref.split(':', 1)
                        last_chapter = new_chapter.strip()
                        result.append(_format_single_ref(last_book_full, last_chapter, new_verses))
                    else:
                        result.append(_format_single_ref(last_book_full, last_chapter, ref))
        else:
            result.append(book_abbr + content)
        i += 2
    
    return "".join(result)

def expand_enclosed_scripture_references(text: str) -> str:
    """Finds content in () or [] and expands scripture refs within."""
    pattern = re.compile(r'([(\[])([^)\]]+)[)\]]') # Handles both () and []
    def replacer(match):
        _, inner_text, _ = match.groups() if len(match.groups()) == 3 else ('', match.group(1), '')
        # Heuristic check
        if not (BOOK_REGEX.search(inner_text) and re.search(r'\d', inner_text)):
            return match.group(0)
        return f" {expand_scripture_references(inner_text)} "
    return pattern.sub(replacer, text)

# --- END OF SCRIPTURE LOGIC ---

def number_replacer(match):
    num_str = match.group(0)
    num_int = int(num_str)
    if len(num_str) == 4 and 1000 <= num_int <= 2099:
        if 2000 <= num_int <= 2009: return _inflect.number_to_words(num_int, andword="")
        else: return _inflect.number_to_words(num_int, group=2).replace(",", "")
    else:
        return _inflect.number_to_words(num_int, andword="")

def normalize_text(text: str) -> str:
    text = expand_enclosed_scripture_references(text)
    text = expand_scripture_references(text)

    # Cleanup for biblical text artifacts
    text = re.sub(r'\[\d+\]|\[fn\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\b\d+\)', '', text)
    
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'{re.escape(phrase)}(?!\w)', replacement, text, flags=re.IGNORECASE)

    text = _strip_diacritics(text)
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    
    text = expand_roman_numerals(text)

    # Handle non-bible abbreviations
    non_bible_abbrs = {k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS)}
    for abbr, expanded in non_bible_abbrs.items():
        flags = 0 if abbr in CASE_SENSITIVE_ABBRS else re.IGNORECASE
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=flags)

    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)

    # General cleanup
    text = re.sub(r'^\s*\d{1,3}\b', '', text, flags=re.M) # Verse numbers at start of line
    text = re.sub(r'([.?!;])\s*("?)\s*\d{1,3}\b', r'\1\2 ', text) # Verse numbers after punctuation

    text = re.sub(r"\b\d+\b", number_replacer, text)
    text = re.sub(r"\[|\]", " , ", text)
    text = re.sub(r"(\s*,\s*){2,}", ", ", text) # multiple commas
    text = re.sub(r"\s+", " ", text).strip()
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        self.voice_path = Path(f"/app/voices/{voice}")
        if not self.voice_path.exists():
            self.voice_path = Path(f"voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)
        piper_command = ["piper", "--model", str(self.voice_path), "--output_file", "-"]
        ffmpeg_command = ["ffmpeg", "-y", "-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "-", "-acodec", "libmp3lame", "-q:a", "2", output_path]

        try:
            piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            piper_process.stdin.write(normalized_text.encode('utf-8'))
            piper_process.stdin.close()
            piper_process.stdout.close()
            _, ffmpeg_err = ffmpeg_process.communicate()
            if piper_process.wait() != 0:
                raise RuntimeError(f"Piper process failed: {piper_process.stderr.read().decode()}")
            if ffmpeg_process.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding process failed: {ffmpeg_err.decode()}")
        except FileNotFoundError as e:
            raise RuntimeError(f"Command not found: {e.filename}. Ensure piper-tts and ffmpeg are installed.")
        return output_path, normalized_text
