import re
import json
import inflect
import subprocess
import unicodedata
import yaml
import time
from pathlib import Path
import os # ADDED
import io # ADDED
import soundfile as sf # ADDED
import requests # ADDED
import numpy as np
import torch # ADDED

# New import for Kokoro-TTS
from kokoro_onnx import Kokoro # ADDED

# The URL to the VOICES.md file for dynamic voice listing
VOICES_MD_URL = "https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md" # ADDED

NORMALIZATION_PATH = Path(__file__).parent / "normalization.json"
RULES_PATH = Path(__file__).parent / "rules.yaml"
LOCK_FILE = Path("/tmp/argos_he_en_install.lock")

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
    GREEK_WORDS = NORMALIZATION.get("greek_words", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
    SUPERSCRIPTS = NORMALIZATION.get("superscripts", [])
    SUPERSCRIPT_MAP = NORMALIZATION.get("SUPERSCRIPT_MAP", {})

else:
    (ABBREVIATIONS, CI_ABBREVIATIONS, BIBLE_BOOKS, CASE_SENSITIVE_ABBRS, ROMAN_EXCEPTIONS,
     BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_WORDS, GREEK_TRANSLITERATION,
     SUPERSCRIPTS, SUPERSCRIPT_MAP) = [{}, {}, [], [], set(), {}, {}, {}, {}, {}, {}, {}, [], {}]

if RULES_PATH.exists():
    RULES = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))['normalization_rules']
else:
    RULES = []

_inflect = inflect.engine()
HEBREW_TO_ENGLISH = None

def ensure_translation_models_are_loaded():
    """Checks for and installs translation models if they are not present, using a lock to prevent concurrent installation."""
    # Imports moved inside to respect the user's intent to lower app startup time
    try:
        from argostranslate import translate
        import argostranslate.package
    except ImportError as e:
        print(f"Warning: Argos Translate not installed. Cannot initialize Hebrew translation model: {e}")
        return
        
    global HEBREW_TO_ENGLISH
    if HEBREW_TO_ENGLISH:
        return

    # 1. First, attempt to load the model (in case it was already installed by a previous run/process).
    try:
        HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
        if HEBREW_TO_ENGLISH:
            return # Successfully loaded, no need to proceed with installation.
    except Exception:
        # Pass and proceed to installation/locking if loading failed.
        pass
    
    # 2. Check for the lock file to prevent a race condition during installation.
    if LOCK_FILE.exists():
        # Another process is installing. Wait briefly and try to load again.
        print("Lock file found. Waiting for other process to finish Argos installation...")
        # Wait for up to 20 seconds to allow the other process to finish
        for _ in range(20):
            time.sleep(1)
            try:
                HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
                if HEBREW_TO_ENGLISH:
                    print("Successfully loaded Argos model after waiting.")
                    return
            except:
                continue
        
        # If after waiting, we still can't load, something failed.
        print("Warning: Timed out waiting for Argos installation lock.")
        return

    # 3. If no lock, acquire the lock and proceed with installation (the critical section).
    try:
        # Use exist_ok=False to ensure only one process creates the file (acquires the lock)
        LOCK_FILE.touch(exist_ok=False) 
        print("Acquired Argos installation lock. Starting download/install.")

        argostranslate.package.update_package_index()
        available_packages = argostranslate.package.get_available_packages()
        
        package_to_install = next(
            filter(
                lambda x: x.from_code == "he" and x.to_code == "en",
                available_packages
            ),
            None
        )
        
        if package_to_install:
            # Check if the package is already installed to avoid unnecessary installation attempts
            if not getattr(package_to_install, 'installed', False): 
                print(f"Downloading and installing Argos Translate package: {package_to_install}")
                package_to_install.install()
            
            # Attempt to load the model after successful install
            HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
        else:
            print("Warning: Hebrew to English translation package not found in Argos Translate index.")
            
    except Exception as e:
        print(f"Warning: Could not initialize Hebrew translation model: {e}")
        HEBREW_TO_ENGLISH = None
    finally:
        # 4. Ensure the lock is released (even if installation failed).
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            print("Released Argos installation lock.")

def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize('NFD', text)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def normalize_hebrew(text: str) -> str:
    def translate_match(match):
        hebrew_text = match.group(0)
        if HEBREW_TO_ENGLISH:
            try:
                translated_text = HEBREW_TO_ENGLISH.translate(hebrew_text)
                return f" {translated_text} "
            except Exception as e:
                print(f"Error during Hebrew translation: {e}")
                return " [Hebrew text] "
        return " [Hebrew text] "
    return re.sub(r'[\u0590-\u05FF]+', translate_match, text)

def normalize_greek(text: str) -> str:
    for greek_word, transliteration in sorted(GREEK_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(greek_word, transliteration)

    text = text.translate(str.maketrans(GREEK_TRANSLITERATION))
    text = text.replace("’", "'")
    return text

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
    valid_roman_pattern = re.compile(
        r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.IGNORECASE)
    
    common_words_to_exclude = {'i', 'a', 'v', 'x', 'l', 'c', 'd', 'm', 'did', 'mix', 'civil', 'mid', 'dim', 'lid', 'ill'}

    def roman_to_int(s):
        roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        s, i, num = s.upper(), 0, 0
        while i < len(s):
            if i + 1 < len(s) and roman_map[s[i]] < roman_map[s[i+1]]:
                num += roman_map[s[i+1]] - roman_map[s[i]]; i += 2
            else:
                num += roman_map[s[i]]; i += 1
        return num

    def _convert_to_words(s):
        if s.upper() in ROMAN_EXCEPTIONS:
            return s
        try:
            integer_val = roman_to_int(s)
            return f"Roman Numeral {_inflect.number_to_words(integer_val)}"
        except (KeyError, IndexError):
            return s

    def replacer(match):
        roman_str = match.group(1)

        if not valid_roman_pattern.match(roman_str):
            return roman_str
        
        keywords = {'chapter', 'part', 'book', 'section', 'act', 'unit', 'volume'}
        preceding_text = text[:match.start()]
        preceding_words = preceding_text.split()
        
        has_strong_clue = False
        if preceding_words:
            last_word = preceding_words[-1].strip('.,:;()[]')
            if last_word.lower() in keywords or (last_word.istitle() and len(last_word) > 1):
                has_strong_clue = True
        
        if roman_str.lower() in common_words_to_exclude and not has_strong_clue:
            return roman_str
        
        return _convert_to_words(roman_str)

    return re.sub(r'\b([IVXLCDMivxlcdm]+)(?!\.)\b', replacer, text)


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
    ref_pattern = re.compile(r'\b(?:(' + book_pattern_str + r')\s+)?(\d+)[:\s]([\d\w\s,.\–-]+(?:ff|f)?)', re.IGNORECASE)
    prose_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+(\d+):([\d\w\s,.-]+(?:ff|f)?)', re.IGNORECASE)
    enclosed_pattern = re.compile(r'([(\[])([^)\]]+)([)\]])')
    
    last_context = {'book': None, 'chapter': None}
    
    def book_chapter_replacer(match):
        nonlocal last_context
        book_abbr, chapter = match.groups()
        last_context['book'] = book_abbr.strip()
        last_context['chapter'] = chapter.strip()
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return f"{book_full} chapter {_inflect.number_to_words(int(chapter))}"

    def replacer(match):
        nonlocal last_context
        book_abbr, chapter, verses = match.groups()
        book_to_use = book_abbr.strip() if book_abbr else last_context.get('book')
        
        if book_abbr:
            last_context['book'] = book_abbr.strip()
            last_context['chapter'] = chapter.strip()
        
        if not book_to_use: return match.group(0)
        
        book_full = CI_ABBREVIATIONS.get(book_to_use.replace('.','').lower(), book_to_use)
        return _format_ref_segment(book_full, chapter, verses or "")

    def replacer_simple(match):
        nonlocal last_context
        book_abbr, chapter, verses = match.groups()
        last_context['book'] = book_abbr.strip()
        last_context['chapter'] = chapter.strip()
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return _format_ref_segment(book_full, chapter, verses or "")

    def enclosed_replacer(match):
        nonlocal last_context
        original_match_text = match.group(0)
        opener, inner_text, closer = match.groups()
        
        verse_abbr_match = re.match(r'^\s*v{1,2}\.\s*([\d\w\s,.\–-]+)\s*$', inner_text, re.IGNORECASE)
        if verse_abbr_match and last_context.get('book') and last_context.get('chapter'):
            verse_part = verse_abbr_match.group(1)
            book_full = CI_ABBREVIATIONS.get(last_context['book'].lower().replace('.', ''), last_context['book'])
            return _format_ref_segment(book_full, last_context['chapter'], verse_part)

        if inner_text.strip().isdigit() and last_context.get('book') and last_context.get('chapter'):
            book_full = CI_ABBREVIATIONS.get(last_context['book'].replace('.','').lower(), last_context['book'])
            return _format_ref_segment(book_full, last_context['chapter'], inner_text)

        parts, final_text_parts = re.split(r'(;)', inner_text), []
        found_scripture = False
        for i, part in enumerate(parts):
            if i % 2 == 1: final_text_parts.append(part); continue
            last_end, new_chunk_parts = 0, []
            for m in ref_pattern.finditer(part):
                found_scripture = True
                new_chunk_parts.append(part[last_end:m.start()]); new_chunk_parts.append(replacer(m)); last_end = m.end()
            new_chunk_parts.append(part[last_end:]); final_text_parts.append("".join(new_chunk_parts))
        
        if not found_scripture:
            return original_match_text
            
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
    try:
        is_ordinal = any(num_str.lower().endswith(s) for s in ['st', 'nd', 'rd', 'th'])
        
        if not is_ordinal and len(num_str) == 4 and num_str.isdigit():
            num_int = int(num_str)

            if 2000 <= num_int <= 2099:
                if num_int < 2010:
                    return _inflect.number_to_words(num_str).replace(" and ", " ")
                else:
                    first_part = _inflect.number_to_words(num_str[:2])
                    second_part = _inflect.number_to_words(num_str[2:])
                    return f"{first_part} {second_part}"
            
            elif 1100 <= num_int <= 1999:
                first_part = _inflect.number_to_words(num_str[:2])
                last_two_digits = num_str[2:]

                if '00' < last_two_digits < '10':
                    second_part = f"oh {_inflect.number_to_words(last_two_digits[1])}"
                    return f"{first_part} {second_part}"
                else:
                    second_part = _inflect.number_to_words(last_two_digits)
                    if second_part == "zero":
                        second_part = "hundred"
                    return f"{first_part} {second_part}"

        words = _inflect.number_to_words(num_str)
        return words
    except:
        return num_str

def currency_replacer(match):
    num_str = match.group(1)
    num_words = _inflect.number_to_words(num_str)
    return f"{num_words} dollars"

def time_replacer(match):
    hour, minutes, period = match.groups()
    
    hour_words = _inflect.number_to_words(int(hour))
    period_words = " ".join(list(period.lower())) # Creates "a m" or "p m"
    
    if minutes == "00":
        # For times like 7:00 AM, say "seven a m"
        return f"{hour_words} {period_words}"
    else:
        # For times like 8:30 AM, say "eight thirty a m"
        minutes_words = _inflect.number_to_words(int(minutes))
        return f"{hour_words} {minutes_words} {period_words}"

FUNCTION_REGISTRY = {
    "remove_superscripts": remove_superscripts,
    "normalize_scripture": normalize_scripture,
    "_strip_diacritics": _strip_diacritics,
    "normalize_hebrew": normalize_hebrew,
    "normalize_greek": normalize_greek,
    "expand_roman_numerals": expand_roman_numerals,
    "_replace_leading_verse_marker": _replace_leading_verse_marker,
    "number_replacer": number_replacer,
    "currency_replacer": currency_replacer,
    "time_replacer": time_replacer, 
}
SYMBOLS.pop('$', None)
DICTIONARY_REGISTRY = {
    "latin_phrases": LATIN_PHRASES,
    "non_bible_abbrs": {k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS)},
    "contractions": CONTRACTIONS,
    "symbols": SYMBOLS,
    "punctuation": PUNCTUATION,
}

def normalize_text(text: str) -> str:
    ensure_translation_models_are_loaded()
    
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

# MODIFIED TTSService CLASS IMPLEMENTATION
class TTSService:
    """
    Text-to-Speech service using Kokoro-TTS (ONNX) for synthesis
    and FFmpeg for final MP3 encoding.
    """
    def __init__(self, voice_name: str, voice_data: any, speed_rate: str = "1.0"):
        """
        Initializes the TTS service.
        :param voice_name: The string name of the voice (e.g., "af_bella") for lang detection.
        :param voice_data: The data to pass to Kokoro (either a string for defaults or a torch.Tensor for custom).
        :param speed_rate: The speed rate (length scale).
        """
        self.speed_rate = speed_rate
        self.voice_name = voice_name
        self.voice_data = voice_data
        
        # --- Kokoro-TTS Initialization ---
        # Rely on environment variables/defaults for model/voice file paths
        self.voices_folder = Path(os.environ.get("KOKORO_VOICES_PATH", "/app/voices"))
        self.model_path = self.voices_folder / os.environ.get("KOKORO_MODEL_FILE", "kokoro-v1.0.onnx")
        self.voices_file_path = self.voices_folder / os.environ.get("KOKORO_VOICES_FILE", "voices-v1.0.bin")

        if not self.model_path.exists():
            raise FileNotFoundError(f"Kokoro model not found at: {self.model_path}")
        if not self.voices_file_path.exists():
            raise FileNotFoundError(f"Kokoro voices file not found at: {self.voices_file_path}")

        print(f"DEBUG: Initializing Kokoro TTS with model: {self.model_path}")
        
        # Initialize the Kokoro model
        self.kokoro = Kokoro(
            model_path=str(self.model_path), 
            voices_path=str(self.voices_file_path)
        )
        
        # Determine language code for Kokoro's 'lang' parameter (e.g., 'en-us', 'en-gb', 'ja', etc.)
        # This now safely uses the voice_name string
        lang_prefix = self.voice_name.split('_')[0]
        # Basic language mapping based on Kokoro conventions (af/am=en-us, bf/bm=en-gb)
        if lang_prefix.startswith('a'):
            self.lang = 'en-us'
        elif lang_prefix.startswith('b'):
            self.lang = 'en-gb'
        elif lang_prefix == 'ja':
            self.lang = 'ja'
        elif lang_prefix.startswith('z'):
            self.lang = 'cmn'
        else:
            self.lang = 'en' # Default fallback
        # --- End Kokoro-TTS Initialization ---

    def synthesize(self, text: str, output_path: str):
        synthesized_text = text

        if not synthesized_text or not synthesized_text.strip():
            print(f"WARNING: No text to synthesize for output file {output_path}. Generating 0.5s of silence.")
            # Changed sample rate to 24000 to match Kokoro's default output
            silence_command = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", "0.5", "-acodec", "libmp3lame", "-q:a", "9", output_path
            ]
            try:
                subprocess.run(silence_command, check=True, capture_output=True)
                return output_path, ""
            except Exception as e:
                raise RuntimeError(f"FFmpeg failed to generate silent audio: {e}")

        # Piper's speed_rate was a length_scale (lower number = faster).
        # Kokoro's speed parameter is a direct multiplier (higher number = faster).
        try:
            length_scale = float(self.speed_rate)
        except ValueError:
            length_scale = 1.0 # Default if conversion fails

        if length_scale <= 0.0:
            length_scale = 1.0 
        
        # Convert length_scale to speed multiplier (1/length_scale)
        kokoro_speed = 1.0 / length_scale
        kokoro_speed = max(0.5, min(kokoro_speed, 2.0)) # Clamp for safety
        
        try:
            print(f"DEBUG: Text sent to Kokoro for {output_path}: '{synthesized_text[:500]}...'")
            
            # --- FIX: Chunking text to prevent IndexError crash ---
            
            # 1. Split text into sentences. This regex keeps the delimiters.
            sentence_parts = re.split(r'([.!?]+|[\.]{3,})', synthesized_text)
            
            # Re-combine text with its delimiter
            sentences = []
            if len(sentence_parts) > 1:
                for i in range(0, len(sentence_parts) - 1, 2):
                    sentence = (sentence_parts[i] + sentence_parts[i+1]).strip()
                    if sentence:
                        sentences.append(sentence)
                if len(sentence_parts) % 2 != 0:
                    trailing = sentence_parts[-1].strip()
                    if trailing:
                        sentences.append(trailing)
            elif len(sentence_parts) == 1:
                sentences = [sentence_parts[0].strip()]
            
            if not sentences:
                print(f"WARNING: Text splitting resulted in 0 sentences for {output_path}. Synthesizing silence.")
                return self.synthesize("", output_path)

            all_samples = []
            current_sample_rate = 24000 # Kokoro's default

            # 2. Synthesize audio for each sentence chunk
            for i, sentence in enumerate(sentences):
                if not sentence or not sentence.strip():
                    continue
                    
                print(f"DEBUG: Synthesizing chunk {i+1}/{len(sentences)} for {output_path}")
                try:
                    samples, sample_rate = self.kokoro.create(
                        text=sentence, 
                        voice=self.voice_data, # Use the loaded tensor or string
                        speed=kokoro_speed, 
                        lang=self.lang
                    )
                    all_samples.append(samples)
                    current_sample_rate = sample_rate
                except Exception as e:
                    print(f"ERROR: Kokoro failed on chunk: '{sentence}'. Error: {e}. Skipping chunk.")
                    all_samples.append(np.zeros(int(0.1 * current_sample_rate)))

            if not all_samples:
                print(f"WARNING: No audio samples were generated for {output_path}. Synthesizing silence.")
                return self.synthesize("", output_path)

            # 3. Concatenate all audio samples into one array
            final_samples = np.concatenate(all_samples)
            # --- END FIX ---

            # 4. Write the concatenated samples to a temporary in-memory WAV file
            temp_wav_io = io.BytesIO()
            sf.write(temp_wav_io, final_samples, current_sample_rate, format='wav') 
            temp_wav_io.seek(0)
            
            # 5. Convert the in-memory WAV data to the final MP3 file using FFmpeg
            ffmpeg_command = [
                "ffmpeg", "-y", 
                "-i", "pipe:",  # -i pipe: reads from stdin
                "-threads", "0", 
                "-acodec", "libmp3lame", 
                "-q:a", "2", # VBR quality setting
                output_path
            ]
            
            print(f"DEBUG: Running FFmpeg conversion to MP3 for {output_path}")

            ffmpeg_process = subprocess.Popen(
                ffmpeg_command, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            _, ffmpeg_err = ffmpeg_process.communicate(input=temp_wav_io.read())
            
            if ffmpeg_process.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding process failed: {ffmpeg_err.decode()}")

        except FileNotFoundError as e:
            if e.filename == 'ffmpeg':
                raise RuntimeError(f"Required command not found: {e.filename}. Please ensure FFmpeg is installed in your Docker image.") from e
            raise RuntimeError(f"A file or command was not found: {e}.") from e
        except Exception as e:
            raise RuntimeError(f"Kokoro or FFmpeg process failed: {e}") from e

        return output_path, synthesized_text

# --- Dynamic voice listing function (NEW) ---
def get_kokoro_voices() -> list[tuple[str, str, str]]:
    """
    Dynamically fetches the list of available Kokoro voices from the VOICES.md file 
    in the Hugging Face repository, as requested.
    Returns: A list of (value, display_name, description) tuples.
    """
    voices = []
    
    # Simple, minimal fallback list if dynamic fetch fails
    fallback_voices = [
        ('af_bella', 'American Female (Bella)', 'Clear, expressive American female voice.'),
        ('am_adam', 'American Male (Adam)', 'A common American male voice.'),
        ('bf_isabella', 'British Female (Isabella)', 'Smooth, British female accent.'),
    ]
    
    try:
        response = requests.get(VOICES_MD_URL, timeout=5)
        response.raise_for_status()
        content = response.text
    except Exception:
        print(f"WARNING: Failed to fetch voice list from {VOICES_MD_URL}. Using fallback list.")
        return fallback_voices

    current_category = "Unknown"
    
    # Parse the Markdown table structure
    for line in content.splitlines():
        # Check for headers to determine language/category
        if line.startswith('# '):
            current_category = line.split('#')[-1].strip()
            continue
        
        # Matches lines starting with a voice name like 'af_bella' followed by '|'
        match = re.match(r'^\s*([a-z]{2}_[a-z]+)\s*\|', line)
        if match:
            voice_name = match.group(1).strip()
            
            # Simple derivation for display name
            parts = voice_name.split('_')
            language = current_category.split('(')[0].strip() if current_category != "Unknown" else "Unknown"
            gender_prefix = parts[0][1]
            gender = 'Male' if gender_prefix == 'm' else 'Female'
            name_part = parts[-1].capitalize() if len(parts) > 1 else 'Unknown'
            
            display_name = f"{language} {gender} ({name_part})"
            description = f"{language} voice: {voice_name}"
            voices.append((voice_name, display_name, description))

    return sorted(voices, key=lambda x: x[1]) if voices else fallback_voices
