import re
import json
import inflect
import subprocess
import unicodedata
import yaml
from pathlib import Path
from argostranslate import translate

# --- DATA LOADING ---
NORMALIZATION_PATH = Path(__file__).parent / "normalization.json"
RULES_PATH = Path(__file__).parent / "rules.yaml"

if NORMALIZATION_PATH.exists():
    NORMALIZATION = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
    CI_ABBREVIATIONS = {k.lower(): v for k, v in ABBREVIATIONS.items()}
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
    SUPERSCRIPT_MAP = NORMALIZATION.get("SUPERSCRIPT_MAP", {})

else:
    # Define empty structures if file is missing
    (ABBREVIATIONS, CI_ABBREVIATIONS, BIBLE_BOOKS, CASE_SENSITIVE_ABBRS, ROMAN_EXCEPTIONS, 
     BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_TRANSLITERATION, 
     SUPERSCRIPTS, SUPERSCRIPT_MAP) = [{}, {}, [], [], set(), {}, {}, {}, {}, {}, {}, [], {}]

if RULES_PATH.exists():
    RULES = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))['normalization_rules']
else:
    RULES = []

_inflect = inflect.engine()
try:
    HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
except Exception:
    HEBREW_TO_ENGLISH = None

# --- HELPER & NORMALIZATION FUNCTIONS (to be called by the rules engine) ---

def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize('NFD', text)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def normalize_hebrew(text: str) -> str:
    def translate_match(match):
        hebrew_text = match.group(0)
        if HEBREW_TO_ENGLISH:
            return f" , translation from Hebrew: {HEBREW_TO_ENGLISH.translate(hebrew_text)} , "
        return " [Hebrew text] "
    return re.sub(r'[\u0590-\u05FF]+', translate_match, text)

def normalize_greek(text: str) -> str:
    return text.translate(str.maketrans(GREEK_TRANSLITERATION))

def remove_superscripts(text: str) -> str:
    def to_superscript(chars: str) -> str:
        return "".join(SUPERSCRIPT_MAP.get(c, c) for c in chars)
    text = re.sub(r'([A-Za-z]+)(\d+)\b', lambda m: m.group(1) + to_superscript(m.group(2)), text)
    if BIBLE_BOOKS:
        bible_books_pattern = r'|'.join(map(re.escape, BIBLE_BOOKS))
        text = re.sub(rf'\b(\d+|[a-z])(?=(?:{bible_books_pattern}))', lambda m: m.group(1), text)
        text = re.sub(rf'\b(\d+|[a-z])(?=[A-Z][a-z])', lambda m: to_superscript(m.group(1)), text)
    else:
        text = re.sub(r'\b(\d+|[a-z])(?=[A-Z][a-z])', lambda m: to_superscript(m.group(1)), text)
    if SUPERSCRIPTS:
        pattern = f"[{''.join(re.escape(c) for c in SUPERSCRIPTS)}]"
        text = re.sub(pattern, "", text)
    return text

def expand_roman_numerals(text: str) -> str:
    def roman_to_int(s):
        roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        s, i, num = s.upper(), 0, 0
        while i < len(s):
            if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
                num += roman_map[s[i+1]] - roman_map[s[i]]; i += 2
            else:
                num += roman_map[s[i]]; i += 1
        return num
    def int_to_roman(num):
        val_map = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        roman_str = ""
        while num > 0:
            for i, r in val_map:
                while num >= i:
                    roman_str += r; num -= i
        return roman_str
    def replacer(match):
        roman_str = match.group(1)
        if roman_str.upper() in ROMAN_EXCEPTIONS: return roman_str
        try:
            integer_val = roman_to_int(roman_str)
            if int_to_roman(integer_val).lower() != roman_str.lower(): return roman_str
            return f"Roman Numeral {_inflect.number_to_words(integer_val)}"
        except (KeyError, IndexError): return roman_str
    return re.sub(r'\b([IVXLCDMivxlcdm]+)\b', replacer, text)

def _format_ref_segment(book_full, chapter, verses_str):
    chapter_words = _inflect.number_to_words(int(chapter))
    if not verses_str: return f"{book_full} chapter {chapter_words}"
    suffix = ""
    verses_str = verses_str.strip().rstrip(".;,")
    if verses_str.lower().endswith("ff"):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith("f"):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"
    prefix = "verses" if any(c in verses_str for c in ",–-") else "verse"
    verses_str = re.sub(r"(\d)([a-z])", r"\1 \2", verses_str, flags=re.IGNORECASE)
    verses_str = verses_str.replace("–", "-").replace("-", " through ")
    verse_words = re.sub(r"\d+", lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    return f"{book_full} chapter {chapter_words}, {prefix} {verse_words}{suffix}"

def normalize_scripture(text: str) -> str:
    bible_abbr_keys = {re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)}
    full_book_names = {re.escape(book) for book in BIBLE_BOOKS}
    book_keys = sorted(list(bible_abbr_keys.union(full_book_names)), key=len, reverse=True)
    book_pattern_str = '|'.join(book_keys)
    book_chapter_pattern = re.compile(r'^\s*(' + book_pattern_str + r')\s+(\d+)\s*$', re.IGNORECASE | re.MULTILINE)
    ref_pattern = re.compile(r'\b(' + book_pattern_str + r')?'+r'\s*'+r'(\d+)'+r'[:\s]'+r'([\d\w\s,.\–-]+(?:ff|f)?)', re.IGNORECASE)
    prose_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+(\d+):([\d\w\s,.-]+(?:ff|f)?)', re.IGNORECASE)
    enclosed_pattern = re.compile(r'([(\[])([^)\]]+)([)\]])')
    last_book_abbr = None
    def book_chapter_replacer(match):
        book_abbr, chapter = match.groups()
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return f"{book_full} chapter {_inflect.number_to_words(int(chapter))}"
    def replacer(match):
        nonlocal last_book_abbr
        book_abbr, chapter, verses = match.groups()
        if book_abbr: last_book_abbr = book_abbr.strip()
        if not last_book_abbr: return match.group(0)
        book_full = CI_ABBREVIATIONS.get(last_book_abbr.replace('.','').lower(), last_book_abbr)
        return _format_ref_segment(book_full, chapter, verses or "")
    def replacer_simple(match):
        book_abbr, chapter, verses = match.groups()
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return _format_ref_segment(book_full, chapter, verses or "")
    def enclosed_replacer(match):
        nonlocal last_book_abbr
        last_book_abbr = None
        opener, inner_text, closer = match.groups()
        parts, final_text_parts = re.split(r'(;)', inner_text), []
        for i, part in enumerate(parts):
            if i % 2 == 1: final_text_parts.append(part); continue
            last_end, new_chunk_parts = 0, []
            for m in ref_pattern.finditer(part):
                new_chunk_parts.append(part[last_end:m.start()]); new_chunk_parts.append(replacer(m)); last_end = m.end()
            new_chunk_parts.append(part[last_end:]); final_text_parts.append("".join(new_chunk_parts))
        return "".join(final_text_parts)
    text = book_chapter_pattern.sub(book_chapter_replacer, text)
    text = enclosed_pattern.sub(enclosed_replacer, text)
    text = prose_pattern.sub(replacer_simple, text)
    return text

def _replace_leading_verse_marker(match):
    chapter, verse = match.groups()
    verse_words = _inflect.number_to_words(verse)
    if chapter:
        return f"chapter {_inflect.number_to_words(chapter)} verse {verse_words} "
    return f"verse {verse_words} "

def number_replacer(match):
    num_str = match.group(0).strip()
    if not num_str.isdigit(): return num_str
    num_int = int(num_str)
    if len(num_str) == 4 and 1100 <= num_int <= 1999:
        return f"{_inflect.number_to_words(num_str[:2])} {_inflect.number_to_words(num_str[2:])}"
    elif len(num_str) == 4 and 2000 <= num_int <= 2099:
        return _inflect.number_to_words(num_int).replace(" and ", " ")
    return _inflect.number_to_words(num_int)

# --- REGISTRIES FOR THE RULES ENGINE ---
FUNCTION_REGISTRY = {
    "remove_superscripts": remove_superscripts,
    "normalize_scripture": normalize_scripture,
    "_strip_diacritics": _strip_diacritics,
    "normalize_hebrew": normalize_hebrew,
    "normalize_greek": normalize_greek,
    "expand_roman_numerals": expand_roman_numerals,
    "_replace_leading_verse_marker": _replace_leading_verse_marker,
    "number_replacer": number_replacer,
}
DICTIONARY_REGISTRY = {
    "latin_phrases": LATIN_PHRASES,
    "non_bible_abbrs": {k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS)},
    "contractions": CONTRACTIONS,
    "symbols": SYMBOLS,
    "punctuation": PUNCTUATION,
}

# --- PRIMARY NORMALIZATION FUNCTION (RULES ENGINE) ---
def normalize_text(text: str) -> str:
    for rule in RULES:
        rule_type = rule.get("type")
        
        if rule_type == "function":
            func = FUNCTION_REGISTRY.get(rule["function_name"])
            if func:
                text = func(text)

        elif rule_type == "regex":
            flags = 0
            for flag_name in rule.get("flags", []):
                flags |= getattr(re, flag_name, 0)
            text = re.sub(rule["pattern"], rule["replacement"], text, flags=flags)

        elif rule_type == "regex_callback":
            func = FUNCTION_REGISTRY.get(rule["function_name"])
            if func:
                flags = 0
                for flag_name in rule.get("flags", []):
                    flags |= getattr(re, flag_name, 0)
                text = re.sub(rule["pattern"], func, text, flags=flags)

        elif rule_type == "dict_lookup":
            dictionary = DICTIONARY_REGISTRY.get(rule["dictionary_name"], {})
            options = rule.get("options", {})
            
            for key, value in sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True):
                pattern = re.escape(key)
                if options.get("word_boundary"):
                    pattern = r'\b' + pattern + r'\b'
                
                flags = 0
                if options.get("use_case_sensitive_list"):
                    if key not in CASE_SENSITIVE_ABBRS:
                        flags |= re.IGNORECASE
                elif options.get("case_insensitive"):
                    flags |= re.IGNORECASE
                
                text = re.sub(pattern, value, text, flags=flags)

    return text.strip()

# --- TTS SERVICE CLASS (UNCHANGED) ---
class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx", speed_rate: str = "1.0"):
        self.speed_rate = speed_rate
        voices_dir = Path(__file__).parent / 'voices'
        self.voice_path = voices_dir / voice
        if not self.voice_path.exists():
            cwd_voices_path = Path.cwd() / "voices" / voice
            if cwd_voices_path.exists():
                self.voice_path = cwd_voices_path
            else:
                 raise ValueError(f"Voice model not found at {voices_dir} or {Path.cwd() / 'voices'}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)
        piper_command = ["piper", "--model", str(self.voice_path), "--length_scale", str(self.speed_rate), "--output_file", "-"]
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
