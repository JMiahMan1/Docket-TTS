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
    CASE_SENSITIVE_ABBRS = NORMALIZATION.get("case_sensitive_abbrs", [])
    ROMAN_EXCEPTIONS = set(NORMALIZATION.get("roman_numeral_exceptions", []))
    BIBLE_REFS = NORMALIZATION.get("bible_refs", {})
    CONTRACTIONS = NORMALIZATION.get("contractions", {})
    SYMBOLS = NORMALIZATION.get("symbols", {})
    PUNCTUATION = NORMALIZATION.get("punctuation", {})
    LATIN_PHRASES = NORMALIZATION.get("latin_phrases", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
else:
    ABBREVIATIONS, BIBLE_BOOKS, CASE_SENSITIVE_ABBRS, ROMAN_EXCEPTIONS, BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_TRANSLITERATION = {}, [], [], set(), {}, {}, {}, {}, {}, {}

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

# -----------------------
# Scripture expansion helpers (improved)
# -----------------------

def _format_ref_segment(book_full: str, chapter: str, verses_str: str) -> str:
    """
    Format a single book/chapter/verses chunk into prose.
    Handles:
      - trailing punctuation after verse specs (ff., ff.; etc.)
      - f / ff suffixes
      - lettered partial verses (19a -> 'nineteen a')
      - ranges (1-15 -> one through fifteen)
      - lists (1,3,5 -> one, three, five)
    """
    chapter_words = _inflect.number_to_words(int(chapter))
    if not verses_str:
        return f"{book_full} chapter {chapter_words}"

    # Trim whitespace & trailing punctuation commonly found in PDFs
    verses_str = verses_str.strip().rstrip(".;,")

    # Handle f / ff suffixes (after punctuation removal)
    low = verses_str.lower()
    suffix = ""
    if low.endswith("ff"):
        verses_str = verses_str[:-2].strip()
        suffix = f" {BIBLE_REFS.get('ff', 'and following')}"
    elif low.endswith("f"):
        verses_str = verses_str[:-1].strip()
        suffix = f" {BIBLE_REFS.get('f', 'and the following verse')}"

    # Normalize digit-letter (partial verses) "19b" -> "19 b"
    verses_str = re.sub(r'(\d)([a-zA-Z])\b', r'\1 \2', verses_str, flags=re.IGNORECASE)

    # Normalize en-dash and hyphen ranges to " through "
    verses_str = verses_str.replace('–', '-')
    verses_str = re.sub(r'\s*-\s*', ' through ', verses_str)

    # Convert numeric tokens to words, keep alphabetic suffixes
    def num_to_words(m):
        return _inflect.number_to_words(int(m.group(0)))
    verse_words = re.sub(r'\b\d+\b', num_to_words, verses_str)

    # Decide "verse" vs "verses"
    prefix = "verses" if ("," in verse_words or "through" in verse_words or " and " in verse_words) else "verse"

    # Clean extra spaces and return
    verse_words = re.sub(r'\s+', ' ', verse_words).strip()
    return f"{book_full} chapter {chapter_words}, {prefix} {verse_words}{suffix}"

def normalize_scripture(text: str) -> str:
    """
    Expand scripture references both inside parentheses/brackets and in prose.
    Handles implied-book across semicolon/comma-separated segments inside parentheses.
    """

    # Build alternation of abbreviations and full book names
    bible_abbr_keys = {re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)}
    full_book_names = {re.escape(book) for book in BIBLE_BOOKS}
    book_keys = sorted(list(bible_abbr_keys.union(full_book_names)), key=len, reverse=True)
    if not book_keys:
        return text
    book_pattern_str = '|'.join(book_keys)

    # A forgiving single-ref regex (captures optional book, chapter, verses block)
    # groups:
    #  1: overall optional book-with-period (may be None)
    #  2: inner book token (without trailing punctuation)
    #  3: chapter digits
    #  4: verses part (digits, ranges, commas, possible trailing f/ff)
    single_ref = re.compile(
        rf"\b(({book_pattern_str})\b\.?)?\s*(\d+)\s*[:\s]\s*([0-9a-zA-Z,\s.\-–]+?)\b",
        re.IGNORECASE
    )

    def expand_sequence(s: str) -> str:
        """
        Expand a string that may contain a sequence of refs where the book may
        only be provided on the first item (e.g., Gen 17:17; 18:1-15; 21:1-7).
        """
        last_book = None

        def repl(m: re.Match) -> str:
            nonlocal last_book
            inner_book = m.group(2)  # may be None
            chapter = m.group(3)
            verses = m.group(4) or ""
            if inner_book:
                last_book = inner_book.strip()
            if not last_book:
                # nothing we can expand (no book context)
                return m.group(0)
            book_full = ABBREVIATIONS.get(last_book.replace('.', ''), last_book)
            return _format_ref_segment(book_full, chapter, verses)

        prev = None
        cur = s
        # repeat a couple of times to allow chained replacements
        for _ in range(3):
            prev = cur
            cur = single_ref.sub(repl, cur)
            if cur == prev:
                break
        return cur

    # 1) Expand inside parentheses/brackets (preserve delimiters)
    enclosed_pattern = re.compile(r'([(\[])([^)\]]+)([)\]])')
    def enclosed_replacer(m: re.Match) -> str:
        opener, inner, closer = m.groups()
        # Quick heuristic: only process if contains a book token or a digit+colon
        if not (re.search(rf"\b({book_pattern_str})\b", inner, re.IGNORECASE) or re.search(r"\d+\s*:\s*\d", inner)):
            return m.group(0)
        return opener + expand_sequence(inner) + closer

    text = enclosed_pattern.sub(enclosed_replacer, text)

    # 2) Expand prose occurrences where the book name is present for each reference
    prose_ref = re.compile(
        rf"\b({book_pattern_str})\b\.?\s+(\d+)\s*[:\s]\s*([0-9a-zA-Z,\s.\-–]+?)\b",
        re.IGNORECASE
    )

    def prose_repl(m: re.Match) -> str:
        book_abbr = m.group(1)
        chapter = m.group(2)
        verses = m.group(3) or ""
        book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), book_abbr)
        return _format_ref_segment(book_full, chapter, verses)

    text = prose_ref.sub(prose_repl, text)
    return text

# -----------------------
# End scripture helpers
# -----------------------

def number_replacer(match):
    num_str = match.group(0)
    if len(num_str) == 4 and 1100 <= int(num_str) <= 1999:
        part1 = _inflect.number_to_words(num_str[:2])
        part2 = _inflect.number_to_words(num_str[2:])
        return f"{part1} {part2}"
    
    num_int = int(num_str)
    if len(num_str) == 4 and 1000 <= num_int <= 2099:
        if 2000 <= num_int <= 2009: return _inflect.number_to_words(num_int, andword="")
        else: return _inflect.number_to_words(num_int, group=2).replace(",", "")
    else:
        return _inflect.number_to_words(num_int, andword="")

def normalize_text(text: str) -> str:
    # --- Clean PDF artifacts first ---

    # 1) Remove superscript-like footnote digits (Unicode superscript block)
    text = re.sub(r'[\u00B2\u00B3\u00B9\u2070-\u209F]+', '', text)

    # 2) Remove verse/footnote numbers that are glued to words (e.g. "1What" -> "What", "1oracles" -> "oracles")
    text = re.sub(r'\b\d+(?=[A-Za-z])', '', text)

    # 3) Replace bracketed footnote markers like [1], [fn] etc.
    text = re.sub(r'\[\d+\]|\[fn\]', '', text)

    # 4) Normalize isolated uppercase headings on their own lines (preserve as needed)
    # (keep your existing leading verse/group normalization if present)
    # If you had a _replace_leading_verse_marker, you can call it here.

    # Scripture expansion (done before converting all remaining digits to words)
    text = normalize_scripture(text)

    # Latin phrases and other phrase replacements (keep as-is)
    for phrase in sorted(LATIN_PHRASES.keys(), key=len, reverse=True):
        replacement = LATIN_PHRASES[phrase]
        text = re.sub(rf'\b{re.escape(phrase)}(?!\w)', replacement, text, flags=re.IGNORECASE)

    text = _strip_diacritics(text)
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    text = expand_roman_numerals(text)

    # Non-bible abbreviations (keep existing behavior)
    non_bible_abbrs = { k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS) }
    for abbr, expanded in non_bible_abbrs.items():
        flags = 0 if abbr in CASE_SENSITIVE_ABBRS else re.IGNORECASE
        text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=flags)
    
    # contractions, symbols, punctuation replacements (preserve)
    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)

    # Remove leftover numeric footnotes like "1)" or superscript number groups
    text = re.sub(r'\b\d+\)', '', text)
    text = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)

    # Convert remaining whole numbers to words (runs after scripture expansion)
    text = re.sub(r"\b\d+\b", number_replacer, text)

    # Bracket replacements and whitespace cleanup
    text = re.sub(r"\[|\]", " , ", text).replace("(", "").replace(")", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx", speed_rate: float = 1.0):
        self.voice_path = Path(f"/app/voices/{voice}")
        self.speed_rate = float(speed_rate)
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
