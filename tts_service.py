import re
import json
import inflect
import subprocess
from pathlib import Path
from argostranslate import translate

# --- Load Normalization Rules (Unchanged) ---
NORMALIZATION_PATH = Path("normalization.json")
if NORMALIZATION_PATH.exists():
    NORMALIZATION = json.loads(NORMALIZATION_PATH.read_text(encoding="utf-8"))
    ABBREVIATIONS = NORMALIZATION.get("abbreviations", {})
    ROMAN_EXCEPTIONS = set(NORMALIZATION.get("roman_numeral_exceptions", []))
    LATIN_PHRASES = NORMALIZATION.get("latin_phrases", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
else:
    ABBREVIATIONS, ROMAN_EXCEPTIONS, LATIN_PHRASES, GREEK_TRANSLITERATION = {}, set(), {}, {}

_inflect = inflect.engine()
HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")

# --- Helper Functions (Improved and Consolidated) ---

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
            return f"Roman Numeral {integer_val}"
        except (KeyError, IndexError):
            return roman_str
    return pattern.sub(replacer, text)

def normalize_parentheticals(text: str) -> str:
    def replacer(match):
        content = match.group(1)
        cleaned_content = content.strip().strip('.,;')
        return f" , {cleaned_content} , "
    return re.sub(r'\((.*?)\)', replacer, text)

# --- NEW: Unified and improved scripture parser ---
def normalize_scripture(text: str) -> str:
    """
    Finds and expands all forms of scripture references, including complex lists.
    This single function replaces the three previous scripture functions.
    """
    book_keys = '|'.join(re.escape(k) for k in ABBREVIATIONS.keys())
    pattern = re.compile(
        r'\b'
        r'(' + book_keys + r')'  # Group 1: The book name
        r'\.?\s+'                  # Optional period and space
        r'([\d:–,\-\s]+'         # Group 2: The chapter/verse numbers
        r'(?:;(?:\s*(?:[1-3]?\s*[A-Za-z]+)\.?\s+)?[\d\w:–,\-\s]+)*' # Optional additional references
        r')'
        r'\b', re.IGNORECASE
    )

    def replacer(match):
        book_abbr = match.group(1).strip()
        references_str = match.group(2).strip()
        
        last_book = ABBREVIATIONS.get(book_abbr.replace('.', ''), book_abbr)
        parts = [p.strip() for p in references_str.split(';')]
        expanded_parts = []
        
        for i, part in enumerate(parts):
            # Check if this part starts with a new book name
            part_book_match = re.match(r'([1-3]?\s*[A-Za-z]+)\.?', part)
            if part_book_match:
                part_book_abbr = part_book_match.group(1).strip()
                if ABBREVIATIONS.get(part_book_abbr.replace('.', '')) :
                    current_book = ABBREVIATIONS.get(part_book_abbr.replace('.', ''))
                    last_book = current_book
                    # Remove book from part to isolate numbers
                    part = part[part_book_match.end():].strip()
            
            # Use the last known book name
            current_book = last_book

            try:
                # Parse chapter and verses from the numeric part
                chapter, verses = (part.split(':', 1) + [None])[:2]
                chapter = chapter.strip()
                
                # Build the spoken string
                spoken_ref = ""
                # Only mention the book for the first part of a multi-part reference
                if i == 0:
                    spoken_ref += f"{current_book} "
                
                spoken_ref += f"chapter {_inflect.number_to_words(int(chapter))}"

                if verses:
                    verses = verses.strip()
                    verse_prefix = "verses" if ',' in verses or '-' in verses or '–' in verses else "verse"
                    # Replace hyphens with 'through' for natural reading
                    verses = verses.replace('–', '-').replace('-', ' through ')
                    
                    # Robustly convert numbers within the verse string
                    def num_to_words_replacer(m):
                        num_str = re.sub(r'\D', '', m.group(0)) # Strip non-digits like 'b' from '13b'
                        return _inflect.number_to_words(int(num_str)) if num_str else ""
                    
                    verse_words = re.sub(r'[\d\w]+', num_to_words_replacer, verses)
                    spoken_ref += f", {verse_prefix} {verse_words}"
                
                expanded_parts.append(spoken_ref)
            except (ValueError, TypeError):
                # If anything fails (e.g., trying to int('word')), skip this part
                continue
        
        # Join the parts into a final string
        return ", " + "; and ".join(expanded_parts) + ", "

    return pattern.sub(replacer, text)


# --- NEW: Refactored main normalization function with a more logical order ---
def normalize_text(text: str) -> str:
    # 1. First, handle specific, complex patterns like scripture lists
    text = normalize_scripture(text)
    
    # 2. Handle language-specific content
    text = normalize_hebrew(text)
    text = normalize_greek(text)
    for phrase, replacement in LATIN_PHRASES.items():
        text = re.sub(rf'\b{re.escape(phrase)}\b', replacement, text, flags=re.IGNORECASE)

    # 3. Strip out all footnote markers and leading verse numbers from lines
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Removes [1], [fn], etc. and leading numbers like "1. " or "1 "
        line = re.sub(r'\[\d+\]|\[fn\]', '', line, flags=re.IGNORECASE)
        line = re.sub(r'^\s*\d+\.?\s*', '', line.strip())
        if line:
            cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)
    
    # 4. Process general patterns like headings and parentheticals
    lines = text.split('\n')
    processed_lines = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line: continue
            
        # Heading detection
        is_heading = False
        words = stripped_line.split()
        if len(words) < 9:
            letters = [char for char in stripped_line if char.isalpha()]
            if len(letters) > 1:
                uppercase_letters = len([char for char in letters if char.isupper()])
                if (uppercase_letters / len(letters)) > 0.75:
                    is_heading = True
        
        if is_heading:
            processed_lines.append(f". . . {stripped_line} . . .")
        else:
            processed_lines.append(stripped_line)
    text = '\n'.join(processed_lines)
    
    text = normalize_parentheticals(text)
    text = expand_roman_numerals(text)

    # 5. Final general substitutions
    text = re.sub(r"(\d+)-(\d+)", r"\1 through \2", text) # Convert number ranges
    
    def number_replacer(match):
        num_str = match.group(0)
        num_int = int(num_str)
        if len(num_str) == 4 and 1000 <= num_int <= 2099:
            return _inflect.number_to_words(num_int, group=2) if not (2000 <= num_int <= 2009) else _inflect.number_to_words(num_int)
        return _inflect.number_to_words(num_int)
    text = re.sub(r"\b\d+\b", number_replacer, text)
    
    text = re.sub(r"\s+", " ", text).strip()
    return text

class TTSService:
    def __init__(self, voice: str = "en_US-hfc_male-medium.onnx"):
        self.voice_path = Path(f"/app/voices/{voice}")
        if not self.voice_path.exists():
            raise ValueError(f"Voice model not found at {self.voice_path}")

    def synthesize(self, text: str, output_path: str):
        normalized_text = normalize_text(text)
        piper_command = ["piper", "--model", str(self.voice_path), "--output_file", "-"]
        ffmpeg_command = ["ffmpeg", "-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "-", "-acodec", "libmp3lame", "-q:a", "2", output_path]
        
        try:
            piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout)
            
            piper_process.stdin.write(normalized_text.encode('utf-8'))
            piper_process.stdin.close()
            piper_process.stdout.close()
            
            ffmpeg_process.wait(timeout=300) # Add a timeout to prevent hangs
            if ffmpeg_process.returncode != 0:
                raise RuntimeError("FFmpeg encoding process failed.")
        except Exception as e:
            # Ensure processes are terminated if an error occurs
            if 'piper_process' in locals() and piper_process.poll() is None: piper_process.kill()
            if 'ffmpeg_process' in locals() and ffmpeg_process.poll() is None: ffmpeg_process.kill()
            raise e # Re-raise the exception
            
        return output_path, normalized_text
