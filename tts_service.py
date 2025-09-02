import re
import json
import inflect
import subprocess
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

HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")

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
    i = 0
    num = 0
    while i < len(s):
        if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
            num += roman_map[s[i+1]] - roman_map[s[i]]
            i += 2
        else:
            num += roman_map[s[i]]
            i += 1
    return num

def int_to_roman(num):
    val_map = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"),
        (1, "I")
    ]
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
        
        if roman_str.upper() in ROMAN_EXCEPTIONS:
            return roman_str 

        try:
            integer_val = roman_to_int(roman_str)
            canonical_roman = int_to_roman(integer_val)
            
            if canonical_roman.lower() != roman_str.lower():
                return roman_str

            return f"Roman Numeral {_inflect.number_to_words(integer_val)}"
        except (KeyError, IndexError):
            return roman_str
            
    return pattern.sub(replacer, text)

def build_scripture_patterns():
    all_abbrs = [re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)]
    ambiguous_lower = [a.lower() for a in AMBIGUOUS_BIBLE_ABBRS]
    unambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') not in ambiguous_lower]
    ambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') in ambiguous_lower]
    
    verse_pattern = r"([\w\s,–-]*)"
    ambiguous_pattern = re.compile(r"\b(" + "|".join(sorted(ambiguous, key=len, reverse=True)) + r")" + r"\s+(\d+):" + verse_pattern, re.IGNORECASE)
    unambiguous_pattern = re.compile(r"\b(" + "|".join(sorted(unambiguous, key=len, reverse=True)) + r")" + r"\s+(\d+)(?::" + verse_pattern + r")?", re.IGNORECASE)
    return ambiguous_pattern, unambiguous_pattern

AMBIGUOUS_PATTERN, UNAMBIGUOUS_PATTERN = build_scripture_patterns()

def _scripture_replacer(match):
    """Helper function to expand a single matched scripture reference."""
    book_abbr, chapter, verses = match.groups() if len(match.groups()) == 3 else (match.group(1), match.group(2), None)
    book_full = ABBREVIATIONS.get(book_abbr.replace('.', ''), ABBREVIATIONS.get(book_abbr, book_abbr))
    chapter_words = _inflect.number_to_words(int(chapter))
    if not verses: return f"{book_full} chapter {chapter_words}"

    suffix = ""
    verses = verses.strip()
    if verses.lower().endswith('ff'):
        verses = verses[:-2].strip()
        suffix = f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses.lower().endswith('f'):
        verses = verses[:-1].strip()
        suffix = f" {BIBLE_REFS.get('f', 'and the following verse')}"

    verse_prefix = "verses" if ',' in verses or '-' in verses or '–' in verses else "verse"
    verses = re.sub(r'(\d)([a-z])', r'\1 \2', verses, flags=re.IGNORECASE)
    verses = verses.replace('–', '-').replace('-', ' through ')
    verse_words = re.sub(r'\d+', lambda m: _inflect.number_to_words(int(m.group())), verses)
    
    return f"{book_full} chapter {chapter_words}, {verse_prefix} {verse_words}{suffix}"

def expand_scripture_references(text: str) -> str:
    """Finds and replaces simple scripture references."""
    text = AMBIGUOUS_PATTERN.sub(_scripture_replacer, text)
    text = UNAMBIGUOUS_PATTERN.sub(_scripture_replacer, text)
    return text

def expand_complex_scripture_references(text: str) -> str:
    """Finds parenthesized text and attempts to expand scripture references within it."""
    pattern = re.compile(r'\(([^)]+)\)')
    
    def complex_replacer(match):
        inner_text = match.group(1)
        
        # Heuristic: only process if it contains a known book abbreviation and a digit
        book_pattern = r'\b(' + '|'.join(re.escape(k) for k in ABBREVIATIONS.keys() if any(book in ABBREVIATIONS[k] for book in BIBLE_BOOKS)) + r')'
        if not (re.search(book_pattern, inner_text, re.IGNORECASE) and re.search(r'\d', inner_text)):
            return match.group(0) # Not a scripture reference, return original with parens

        # It looks like a scripture reference, so expand it
        expanded_text = expand_scripture_references(inner_text)
        return expanded_text

    return pattern.sub(complex_replacer, text)

def normalize_parentheticals(text: str) -> str:
    def replacer(match):
        content = match.group(1)
        cleaned_content = content.strip().strip('.,;')
        return f" , {cleaned_content} , "
    
    text = re.sub(r'\((?![A-Za-z\s]+\.?\s+\d+)([^)]+)\)', replacer, text)
    return text

def expand_ambiguous_citations(text: str) -> str:
    def replacer(match):
        chapter, verses = match.groups()
        chapter_words = _inflect.number_to_words(int(chapter))
        
        if '-' in verses or '–' in verses:
            verse_prefix = "verses"
        else:
            verse_prefix = "verse"
        
        verses = verses.replace('–', '-').replace('-', ' through ')
        verse_words = re.sub(r'\d+', lambda m: _inflect.number_to_words(int(m.group())), verses)
        
        return f", chapter {chapter_words}, {verse_prefix} {verse_words} ,"
    
    return re.sub(r'\((\d+):([\d,-]+)\)', replacer, text)

def number_replacer(match):
    num_str = match.group(0)
    num_int = int(num_str)
    
    if len(num_str) == 4 and 1000 <= num_int <= 2099:
        if 2000 <= num_int <= 2009:
            return _inflect.number_to_words(num_int, andword="")
        else:
            return _inflect.number_to_words(num_int, group=2).replace(",", "")
    else:
        return _inflect.number_to_words(num_int, andword="")

def normalize_text(text: str) -> str:
    text = expand_complex_scripture_references(text)
    text = expand_scripture_references(text)

    # Cleanup for biblical and other text artifacts
    suffix_words = 'Their|Whose|There'
    text = re.sub(rf'\b({suffix_words})([a-z])\b', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+)([a-zA-Z])', r'\1 \2', text)
    text = re.sub(r'\b([a-z])([A-Z])', r'\1 \2', text)
    prefix_words = 'the|by|every|is|mouth|feet|fear|law|throat|tongues|poison|lips|to|some|how|oracles'
    text = re.sub(rf'\b([a-z])({prefix_words})\b', r'\1 \2', text, flags=re.IGNORECASE)
    text = re.sub(r'([a-z])(“|")', r'\1 \2', text)
    text = re.sub(r'\[\d+\]|\[fn\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\b\d+\)', '', text)
    
    # Run Latin phrase expansion before general punctuation cleanup
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'{re.escape(phrase)}(?!\w)', replacement, text, flags=re.IGNORECASE)

    # General normalization and cleanup
    text = re.sub(r'\s*,\s*\.', '.', text) 
    text = re.sub(r'\s*\.\s*,', ',', text)
    text = re.sub(r'(\s*[\.,]\s*){2,}', '. ', text)
    
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    
    text = expand_ambiguous_citations(text)
    text = normalize_parentheticals(text)
    text = expand_roman_numerals(text)

    non_bible_abbrs = { k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS) }
    case_sensitive_set = {abbr.lower().replace('.', '') for abbr in CASE_SENSITIVE_ABBRS}
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text)
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') not in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)
    
    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)

    # Safer verse number and footnote cleanup runs after major expansions
    text = re.sub(r'^\s*\d{1,3}\b', '', text, flags=re.M)
    text = re.sub(r'([.?!;])\s*("?)\s*\d{1,3}\b', r'\1\2 ', text)
    text = re.sub(r'\s\b([b-hB-HJ-Zj-zJ-Z])\b\s', ' ', text)
    text = re.sub(r'^\s*[b-hB-HJ-Zj-zJ-Z]\b\s*', '', text, flags=re.M)

    lines = text.split('\n')
    processed_lines = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            processed_lines.append(line)
            continue
        is_mostly_caps_heading, is_title_case_heading = False, False
        if len(stripped_line) >= 3: 
            letters = [char for char in stripped_line if char.isalpha()]
            if len(letters) > 1:
                uppercase_letters = [char for char in letters if char.isupper()]
                if (len(uppercase_letters) / len(letters)) > 0.75: is_mostly_caps_heading = True
        words = stripped_line.split()
        word_count = len(words)
        if (1 < word_count < 9) and (stripped_line[-1].isalpha()) and (stripped_line[0].isupper()):
            capitalized_words = sum(1 for word in words if word[0].isupper())
            if (capitalized_words / word_count) >= 0.5: is_title_case_heading = True

        if is_mostly_caps_heading or is_title_case_heading:
            processed_lines.append(". . . " + stripped_line + ". . . ")
        else:
            processed_lines.append(stripped_line)
    text = '\n'.join(processed_lines)
    
    text = re.sub(r"\b\d+\b", number_replacer, text)
    text = re.sub(r"\[|\]", " , ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(":", ",")
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        self.voice_path = Path(f"/app/voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)
        
        piper_command = ["piper", "--model", str(self.voice_path), "--output_file", "-"]
        
        ffmpeg_command = [
            "ffmpeg",
            "-f", "s16le",
            "-ar", "22050",
            "-ac", "1",
            "-i", "-",
            "-acodec", "libmp3lame",
            "-q:a", "2",
            output_path
        ]

        piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout)

        piper_process.stdin.write(normalized_text.encode('utf-8'))
        piper_process.stdin.close()
        
        piper_process.stdout.close()
        
        ffmpeg_process.wait()

        if ffmpeg_process.returncode != 0:
            raise RuntimeError("FFmpeg encoding process failed.")
            
        return output_path, normalized_text
