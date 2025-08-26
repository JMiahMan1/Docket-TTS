import re
import json
import inflect
import subprocess
from pathlib import Path
from argostranslate import translate

# Load normalization rules from the JSON file
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

# Load the installed translation model once when the script starts
HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")

def normalize_hebrew(text: str) -> str:
    """Finds blocks of Hebrew, translates them, and formats for TTS."""
    def translate_match(match):
        hebrew_text = match.group(0)
        if HEBREW_TO_ENGLISH:
            translated_text = HEBREW_TO_ENGLISH.translate(hebrew_text)
            return f" , translation from Hebrew: {translated_text} , "
        return " [Hebrew text] "
    return re.sub(r'[\u0590-\u05FF]+', translate_match, text)

def normalize_greek(text: str) -> str:
    """Transliterates Greek characters to their English phonetic equivalents."""
    for char, replacement in GREEK_TRANSLITERATION.items():
        text = text.replace(char, replacement)
    return text

def roman_to_int(s):
    """Converts a Roman numeral string to an integer."""
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s = s.upper()
    i = 0
    num = 0
    while i < len(s):
        if i + 1 < len(s) and s[i:i+2] in ["IV", "IX", "XL", "XC", "CD", "CM"]:
            num += roman_map[s[i+1]] - roman_map[s[i]]
            i += 2
        else:
            num += roman_map[s[i]]
            i += 1
    return num

def expand_roman_numerals(text: str) -> str:
    """Finds and replaces Roman numerals with a spoken-word equivalent, ignoring exceptions."""
    pattern = re.compile(r'\b([IVXLCDMivxlcdm]+)\b')
    
    def replacer(match):
        roman_str = match.group(1)
        
        if roman_str.upper() in ROMAN_EXCEPTIONS:
            return roman_str 

        try:
            #Convert the found word (e.g., "did") to an integer (1001).
            integer_val = roman_to_int(roman_str)
            #Convert the integer back to a proper Roman numeral ("MI").
            canonical_roman = int_to_roman(integer_val)
            
            #If the proper form doesn't match the original word, it's a false positive.
            if canonical_roman.lower() != roman_str.lower():
                return roman_str # Return the original word unchanged.

            return f"Roman Numeral {integer_val}"
        except (KeyError, IndexError):
            # If any error occurs, it's not a valid numeral.
            return roman_str
            
    return pattern.sub(replacer, text)

def build_scripture_patterns():
    all_abbrs = [re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS)]
    ambiguous_lower = [a.lower() for a in AMBIGUOUS_BIBLE_ABBRS]
    unambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') not in ambiguous_lower]
    ambiguous = [a for a in all_abbrs if a.lower().replace('\\.', '') in ambiguous_lower]
    ambiguous_pattern = re.compile(r"\b(" + "|".join(sorted(ambiguous, key=len, reverse=True)) + r")" + r"\s+(\d+):(\d[\d\s,–-]*)\b", re.IGNORECASE)
    unambiguous_pattern = re.compile(r"\b(" + "|".join(sorted(unambiguous, key=len, reverse=True)) + r")" + r"\s+(\d+)(?::(\d[\d\s,–-]*))?\b", re.IGNORECASE)
    return ambiguous_pattern, unambiguous_pattern

AMBIGUOUS_PATTERN, UNAMBIGUOUS_PATTERN = build_scripture_patterns()

def expand_scripture_references(text: str) -> str:
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
    text = re.sub(r'\s*,\s*\.', '.', text) 
    text = re.sub(r'\s*\.\s*,', ',', text)
    text = re.sub(r'(\s*[\.,]\s*){2,}', '. ', text)
    
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'\b{re.escape(phrase)}\b', replacement, text, flags=re.IGNORECASE)
    
    text = re.sub(r'\[\d+\]|\(\d+\)|\b\d+\)|[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)
    text = expand_roman_numerals(text)
    text = expand_scripture_references(text)

    non_bible_abbrs = { k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS) }
    case_sensitive_set = {abbr.lower().replace('.', '') for abbr in CASE_SENSITIVE_ABBRS}
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text)
    for abbr, expanded in non_bible_abbrs.items():
        if abbr.lower().replace('.', '') not in case_sensitive_set:
            text = re.sub(rf"\b{re.escape(abbr)}\b", expanded, text, flags=re.IGNORECASE)

    def bible_ff_repl(match):
        book, verse = match.group(1), _inflect.number_to_words(match.group(2))
        suffix = BIBLE_REFS.get(match.group(3).lower(), "")
        return f"{book} verse {verse} {suffix}"
    text = re.sub(r"([A-Za-z]+\s?\d*):(\d+)(ff|f)\b", bible_ff_repl, text)

    for contr, expanded in CONTRACTIONS.items(): text = text.replace(contr, expanded)
    for sym, expanded in SYMBOLS.items(): text = text.replace(sym, expanded)
    for p, repl in PUNCTUATION.items(): text = text.replace(p, repl)

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
        """
        Generates audio by piping Piper's raw output to FFmpeg for proper MP3 encoding.
        """
        normalized_text = normalize_text(text)
        
        # Piper outputs raw audio (s16le, 22050Hz, mono) to standard output
        piper_command = ["piper", "--model", str(self.voice_path), "--output_file", "-"]
        
        # FFmpeg reads the raw audio from its standard input and encodes it to MP3
        ffmpeg_command = [
            "ffmpeg",
            "-f", "s16le",       # Format of the input audio
            "-ar", "22050",      # Audio rate
            "-ac", "1",          # Audio channels (mono)
            "-i", "-",           # Read from standard input
            "-acodec", "libmp3lame", # Encode using the LAME MP3 encoder
            "-q:a", "2",         # Set VBR quality (0-9, lower is better)
            output_path
        ]

        # Chain the processes together
        piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout)

        # Send the text to Piper and close its input stream
        piper_process.stdin.write(normalized_text.encode('utf-8'))
        piper_process.stdin.close()
        
        # Allow piper_process.stdout to be read by ffmpeg_process
        piper_process.stdout.close()
        
        # Wait for FFmpeg to finish encoding
        ffmpeg_process.wait()

        if ffmpeg_process.returncode != 0:
            raise RuntimeError("FFmpeg encoding process failed.")
            
        return output_path, normalized_text
