import re
import json
import inflect
import subprocess
import unicodedata
from pathlib import Path
from argostranslate import translate

NORMALIZATION_PATH = Path(__file__).parent / "normalization.json"
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

def build_scripture_patterns():
    all_abbrs = [re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)]
    verse_pattern = r"([^;]*)"
    unambiguous_pattern = re.compile(r"\b(" + "|".join(sorted(all_abbrs, key=len, reverse=True)) + r")" + r"\s+(\d+)(?::" + verse_pattern + r")?", re.IGNORECASE)
    return unambiguous_pattern

UNAMBIGUOUS_PATTERN = build_scripture_patterns()

def _scripture_replacer(match):
    book_abbr, chapter, verses = match.groups() if len(match.groups()) == 3 else (match.group(1), match.group(2), None)
    book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), ABBREVIATIONS.get(book_abbr, book_abbr))
    
    verses_str = (verses or "").strip().rstrip(".;")
    
    if verses_str.lower().endswith('ff'):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith('f'):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"
    else:
        suffix = ""

    chapter_words = _inflect.number_to_words(int(chapter))
    if not verses_str:
        return f"{book_full} chapter {chapter_words}{suffix}"

    prefix = "verses" if any(c in verses_str for c in ",–-") else "verse"
    verses_str = re.sub(r"(\d)([a-z])", r"\1 \2", verses_str, flags=re.IGNORECASE)
    verses_str = verses_str.replace("–", "-").replace("-", " through ")
    verse_words = re.sub(r"\d+", lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    return f"{book_full} chapter {chapter_words}, {prefix} {verse_words}{suffix}"

def expand_scripture_references(text: str) -> str:
    return UNAMBIGUOUS_PATTERN.sub(_scripture_replacer, text)

def expand_complex_scripture_references(text: str) -> str:
    pattern = re.compile(r'([(\[])([^)\]]+)([)\]])')
    def complex_replacer(match):
        opener, inner_text, closer = match.groups()
        book_pattern_str = '|'.join([re.escape(k) for k in ABBREVIATIONS.keys() if any(book in ABBREVIATIONS[k] for book in BIBLE_BOOKS)])
        if not (re.search(r'\b(' + book_pattern_str + r')', inner_text, re.IGNORECASE) and re.search(r'\d', inner_text)):
            return match.group(0)
        
        last_book_abbr = None
        segments = inner_text.split(';')
        expanded_segments = []
        for segment in segments:
            segment = segment.strip()
            book_match = re.match(r'(' + book_pattern_str + r')\b(.*)', segment, re.IGNORECASE)
            if book_match:
                last_book_abbr = book_match.group(1)
                expanded_segments.append(expand_scripture_references(segment))
            elif last_book_abbr:
                expanded_segments.append(expand_scripture_references(f"{last_book_abbr} {segment}"))
            else:
                expanded_segments.append(segment)
        return " ".join(expanded_segments)
    return pattern.sub(complex_replacer, text)

def number_replacer(match):
    num_str = match.group(0)
    num_int = int(num_str)
    if len(num_str) == 4 and 1000 <= num_int <= 2099:
        if 2000 <= num_int <= 2009: return _inflect.number_to_words(num_int, andword="")
        else: return _inflect.number_to_words(num_int, group=2).replace(",", "")
    else:
        return _inflect.number_to_words(num_int, andword="")

def normalize_text(text: str) -> str:
    text = expand_complex_scripture_references(text)
    text = expand_scripture_references(text)
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'{re.escape(phrase)}(?!\w)', replacement, text, flags=re.IGNORECASE)
    text = _strip_diacritics(text)
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    text = expand_roman_numerals(text)
    non_bible_abbrs = { k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS) }
    for abbr, expanded in non_bible_abbrs.items():
        flags = 0 if abbr in CASE_SENSITIVE_ABBRS else re.IGNORECASE
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=flags)
    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)
    text = re.sub(r'^\s*\d{1,3}\b', '', text, flags=re.M)
    text = re.sub(r'([.?!;])\s*("?)\s*\d{1,3}\b', r'\1\2 ', text)
    text = re.sub(r"\[\d+\]|\[fn\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\b\d+\)", "", text)
    text = re.sub(r"\b\d+\b", number_replacer, text)
    text = re.sub(r"\[|\]", " , ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        # This logic robustly finds the voice model relative to this file's location
        voices_dir = Path(__file__).parent / 'voices'
        self.voice_path = voices_dir / voice
        if not self.voice_path.exists():
            # Fallback for test environments where CWD is the project root
            cwd_voices_path = Path.cwd() / "voices" / voice
            if cwd_voices_path.exists():
                self.voice_path = cwd_voices_path
            else:
                 raise ValueError(f"Voice model not found at {self.voice_path} or {cwd_voices_path}")

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
