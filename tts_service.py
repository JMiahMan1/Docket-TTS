import re
import json
import inflect
import subprocess
import unicodedata
import yaml
import time
from pathlib import Path
import os
import io
import soundfile as sf
import requests
import numpy as np
import torch
from kokoro_onnx import Kokoro

VOICES_MD_URL = "https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md"

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
    try:
        from argostranslate import translate
        import argostranslate.package
    except ImportError as e:
        print(f"Warning: Argos Translate not installed. Cannot initialize Hebrew translation model: {e}")
        return
        
    global HEBREW_TO_ENGLISH
    if HEBREW_TO_ENGLISH:
        return

    try:
        HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
        if HEBREW_TO_ENGLISH:
            return
    except Exception:
        pass
    
    if LOCK_FILE.exists():
        print("Lock file found. Waiting for other process to finish Argos installation...")
        for _ in range(20):
            time.sleep(1)
            try:
                HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
                if HEBREW_TO_ENGLISH:
                    print("Successfully loaded Argos model after waiting.")
                    return
            except:
                continue
        
        print("Warning: Timed out waiting for Argos installation lock.")
        return

    try:
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
            if not getattr(package_to_install, 'installed', False): 
                print(f"Downloading and installing Argos Translate package: {package_to_install}")
                package_to_install.install()
            
            HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
        else:
            print("Warning: Hebrew to English translation package not found in Argos Translate index.")
            
    except Exception as e:
        print(f"Warning: Could not initialize Hebrew translation model: {e}")
        HEBREW_TO_ENGLISH = None
    finally:
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
    
    common_words_to_exclude = {'i', 'a', 'v', 'x', 'l', 'c', 'd', 'm', 'did', 'mix', 'civil', 'mid', 'dim', 'lid', 'ill', 'di', 'si', 'mi'}

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
    ref_pattern = re.compile(r'\b(?:(' + book_pattern_str + r')\s+)?(\d+)[:\s]([\d\w\s,.\-–]+(?:ff|f)?)', re.IGNORECASE)
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
        
        verse_abbr_match = re.match(r'^\s*v{1,2}\.\s*([\d\w\s,.\-–]+)\s*$', inner_text, re.IGNORECASE)
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
    period_words = " ".join(list(period.lower()))
    
    if minutes == "00":
        return f"{hour_words} {period_words}"
    else:
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

class TTSService:
    """
    Text-to-Speech service using Kokoro-TTS (ONNX) for synthesis
    and FFmpeg for final MP3 encoding. Supports Dual-Voice (Narrator/Dialogue)
    and Natural Pausing.
    """
    def __init__(self, voice_name: str, voice_data: any, speed_rate: str = "1.0", 
                 secondary_voice_name: str = None, secondary_voice_data: any = None):
        """
        Initializes the TTS service.
        :param voice_name: The string name of the primary voice.
        :param voice_data: The data/embedding for the primary voice.
        :param speed_rate: The speed rate.
        :param secondary_voice_name: Optional name for the dialogue voice.
        :param secondary_voice_data: Optional data/embedding for the dialogue voice.
        """
        self.speed_rate = speed_rate
        self.voice_name = voice_name
        self.voice_data = voice_data
        self.secondary_voice_name = secondary_voice_name
        self.secondary_voice_data = secondary_voice_data
        
        self.voices_folder = Path(os.environ.get("KOKORO_VOICES_PATH", "/app/voices"))
        self.model_path = self.voices_folder / os.environ.get("KOKORO_MODEL_FILE", "kokoro-v1.0.onnx")
        self.voices_file_path = self.voices_folder / os.environ.get("KOKORO_VOICES_FILE", "voices-v1.0.bin")

        if not self.model_path.exists():
            raise FileNotFoundError(f"Kokoro model not found at: {self.model_path}")
        if not self.voices_file_path.exists():
            raise FileNotFoundError(f"Kokoro voices file not found at: {self.voices_file_path}")

        print(f"DEBUG: Initializing Kokoro TTS with model: {self.model_path}")
        
        self.kokoro = Kokoro(
            model_path=str(self.model_path), 
            voices_path=str(self.voices_file_path)
        )
        
        self.lang = self._get_lang_code(voice_name)
        self.secondary_lang = self._get_lang_code(secondary_voice_name) if secondary_voice_name else self.lang

        # Gender Detection
        self.primary_gender = self._detect_voice_gender(voice_name)
        self.secondary_gender = self._detect_voice_gender(secondary_voice_name) if secondary_voice_name else None
        print(f"DEBUG: Voice Genders - Primary: {self.primary_gender}, Secondary: {self.secondary_gender}")

    def _get_lang_code(self, v_name):
        if not v_name: return 'en'
        lang_prefix = v_name.split('_')[0]
        if lang_prefix.startswith('a'): return 'en-us'
        if lang_prefix.startswith('b'): return 'en-gb'
        if lang_prefix == 'ja': return 'ja'
        if lang_prefix.startswith('z'): return 'cmn'
        return 'en'

    def _detect_voice_gender(self, v_name: str) -> str:
        """
        Detects gender from voice code (e.g. 'af' -> female, 'am' -> male).
        Returns 'f', 'm', or 'u' (unknown).
        """
        if not v_name: return 'u'
        prefix = v_name.split('_')[0]
        # af, bf, zf, jf -> Female
        if 'f' in prefix: return 'f'
        # am, bm, zm, jm -> Male
        if 'm' in prefix: return 'm'
        return 'u'

    def _segment_dialogue(self, text):
        """
        Splits text into chunks of (text, is_dialogue).
        Detects text between double quotes as dialogue.
        """
        # Regex to capture text inside double quotes, handling common punctuation inside
        # Note: This is a basic heuristic.
        pattern = r'(".*?")'
        parts = re.split(pattern, text, flags=re.DOTALL)
        
        segments = []
        for part in parts:
            if not part.strip():
                continue
            # Check if this part is a quoted string
            if part.startswith('"') and part.endswith('"'):
                # It's dialogue. Strip quotes for synthesis? 
                # Ideally, we keep them or remove them based on preference. 
                # Let's clean the quotes for the TTS engine to avoid saying "quote".
                clean_content = part[1:-1].strip()
                if clean_content:
                    segments.append((clean_content, True))
            else:
                segments.append((part, False))
        return segments

    def _infer_speaker_gender(self, context_text: str) -> str:
        """
        Analyzes the preceding text to guess the gender of the speaker.
        Returns 'f' (Female), 'm' (Male), or None (Unknown).
        """
        if not context_text: return None
        
        # Look at the last ~150 characters
        recent_text = context_text[-150:].lower()
        
        # Regex patterns for attribution
        # "She said", "said she", "Her mother asked"
        female_pattern = r'\b(she|her|hers|mom|mother|woman|girl|lady|aunt|sister|wife)\b.{0,40}(said|asked|replied|shouted|whispered|cried|muttered|thought|explained|continued)'
        male_pattern =   r'\b(he|him|his|dad|father|man|boy|guy|uncle|brother|husband)\b.{0,40}(said|asked|replied|shouted|whispered|cried|muttered|thought|explained|continued)'
        
        # Check patterns (reversed to find closest to the quote)
        # Note: This is a robust simplification.
        
        f_match = re.search(female_pattern, recent_text)
        m_match = re.search(male_pattern, recent_text)
        
        # If both found, checking which is closer is hard with just 'search'.
        # Let's assume the presence is a strong enough signal for now.
        if f_match and not m_match: return 'f'
        if m_match and not f_match: return 'm'
        
        # Tie-breaker or closer inspection could go here
        return None

    def synthesize(self, text: str, output_path: str):
        # Fix unnatural breath artifact caused by " . " (space dot space)
        # Replacing with comma (,) creates a shorter, natural pause instead of the long silence/gasp of ellipsis.
        synthesized_text = text.replace(" . ", ", ")
        
        # Collapse multiple dots and spaces (e.g. from TOCs "Chapter 1 .......... 5") into a single pause
        synthesized_text = re.sub(r'[\. ]{3,}', ', ', synthesized_text)
        
        # Final cleanup: Collapse dense punctuation (e.g., ", , " or ", .")
        synthesized_text = re.sub(r'([,.;:!])\s*([,.;:!])', r'\1', synthesized_text) # Dedup punct
        synthesized_text = re.sub(r'\s+([,.;:!])', r'\1', synthesized_text) # Strip space before punct

        if not synthesized_text or not synthesized_text.strip():
            print(f"WARNING: No text to synthesize for output file {output_path}. Generating 0.5s of silence.")
            # ... (Silence generation fallback) ...
            silence_command = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", "0.5", "-acodec", "libmp3lame", "-q:a", "9", output_path
            ]
            try:
                subprocess.run(silence_command, check=True, capture_output=True)
                return output_path, ""
            except Exception as e:
                raise RuntimeError(f"FFmpeg failed to generate silent audio: {e}")

        try:
            user_speed = float(self.speed_rate)
        except ValueError:
            user_speed = 1.0 
        kokoro_speed = max(0.5, min(1.0 / (user_speed if user_speed > 0 else 1.0), 2.0))
        
        try:
            print(f"DEBUG: Text sent to Kokoro for {output_path}: '{synthesized_text[:100]}...'")
            
            all_samples = []
            
            # 1. Segment into specific voices if secondary voice exists
            if self.secondary_voice_data:
                segments = self._segment_dialogue(synthesized_text)
            else:
                segments = [(synthesized_text, False)]
                
            current_sample_rate = 24000
            previous_narration_context = ""

            for i, (seg_text, is_dialogue) in enumerate(segments):
                if not seg_text.strip():
                    continue
                
                voice_to_use = self.voice_data
                lang_to_use = self.lang
                
                if self.secondary_voice_data and is_dialogue:
                    # DEFAULT: Use secondary voice for dialogue
                    target_voice_data = self.secondary_voice_data
                    target_lang = self.secondary_lang
                    
                    # SMART SWITCHING: Check genders
                    inferred_gender = self._infer_speaker_gender(previous_narration_context)
                    
                    if inferred_gender:
                        # If inferred Female
                        if inferred_gender == 'f':
                            # Prefer Primary if it's female
                            if self.primary_gender == 'f':
                                target_voice_data = self.voice_data
                                target_lang = self.lang
                            # Else use Secondary if it's female
                            elif self.secondary_gender == 'f':
                                target_voice_data = self.secondary_voice_data
                                target_lang = self.secondary_lang
                                
                        # If inferred Male
                        elif inferred_gender == 'm':
                            # Prefer Primary if it's male
                            if self.primary_gender == 'm':
                                target_voice_data = self.voice_data
                                target_lang = self.lang
                            # Else use Secondary if it's male
                            elif self.secondary_gender == 'm':
                                target_voice_data = self.secondary_voice_data
                                target_lang = self.secondary_lang
                                
                    voice_to_use = target_voice_data
                    lang_to_use = target_lang
                
                else:
                    # Is Narration
                    previous_narration_context = seg_text # Update context
                
                # 2. Split into sentences/chunks for natural pausing
                # Improved regex to handle common abbreviations avoids bad splits
                # e.g. "Mr.", "Dr." etc. should already be normalized, but good to be safe.
                # Splitting by punctuation for pausing: . ? ! : ; 
                
                # We split by punctuation that implies a pause.
                chunk_parts = re.split(r'([.?!:;]+|[".]{3,})', seg_text)
                
                chunks = []
                # Re-assemble split list into [text, punct, text, punct...]
                if len(chunk_parts) > 1:
                    for j in range(0, len(chunk_parts) - 1, 2):
                        c_text = chunk_parts[j].strip()
                        c_punct = chunk_parts[j+1].strip()
                        if c_text or c_punct:
                            chunks.append((c_text, c_punct))
                    if len(chunk_parts) % 2 != 0:
                        trailing = chunk_parts[-1].strip()
                        if trailing:
                            chunks.append((trailing, ""))
                else:
                    chunks = [(chunk_parts[0].strip(), "")]

                for chunk_text, punctuation in chunks:
                    if not chunk_text and not punctuation:
                        continue
                        
                    # Determine pause length based on punctuation
                    pause_duration = 0.0
                    if '.' in punctuation or '?' in punctuation or '!' in punctuation:
                        pause_duration = 0.5
                    elif ';' in punctuation or ':' in punctuation:
                        pause_duration = 0.3
                    elif ',' in punctuation or '—' in punctuation: # Comma handling limits
                        pause_duration = 0.2
                    
                    full_chunk = chunk_text + punctuation
                    
                    # Synthesize
                    if full_chunk.strip():
                        print(f"DEBUG: Synthesizing chunk... '{full_chunk[:30]}...' (Voice: {'Multiple' if self.secondary_voice_data else 'Primary'})")
                        samples, sample_rate = self.kokoro.create(
                            text=full_chunk, 
                            voice=voice_to_use,
                            speed=kokoro_speed, 
                            lang=lang_to_use
                        )
                        all_samples.append(samples)
                        current_sample_rate = sample_rate # Assoc. with last generated
                    
                    # Add pause
                    if pause_duration > 0:
                        pause_samples = np.zeros(int(pause_duration * current_sample_rate))
                        all_samples.append(pause_samples)

            if not all_samples:
                # Fallback
                 return self.synthesize("", output_path)

            final_samples = np.concatenate(all_samples)

            temp_wav_io = io.BytesIO()
            sf.write(temp_wav_io, final_samples, current_sample_rate, format='wav') 
            temp_wav_io.seek(0)
            
            ffmpeg_command = [
                "ffmpeg", "-y", 
                "-i", "pipe:",
                "-threads", "0", 
                "-acodec", "libmp3lame", 
                "-q:a", "2",
                output_path
            ]
            
            ffmpeg_process = subprocess.Popen(
                ffmpeg_command, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            _, ffmpeg_err = ffmpeg_process.communicate(input=temp_wav_io.read())
            
            if ffmpeg_process.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding process failed: {ffmpeg_err.decode()}")

        except Exception as e:
            raise RuntimeError(f"Kokoro or FFmpeg process failed: {e}") from e

        return output_path, synthesized_text

def get_kokoro_voices() -> list[tuple[str, str, str]]:
    """
    Dynamically fetches the list of available Kokoro voices from the VOICES.md file 
    in the Hugging Face repository, as requested.
    Returns: A list of (value, display_name, description) tuples.
    """
    voices = []
    
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
    
    for line in content.splitlines():
        if line.startswith('# '):
            current_category = line.split('#')[-1].strip()
            continue
        
        match = re.match(r'^\s*([a-z]{2}_[a-z]+)\s*\|', line)
        if match:
            voice_name = match.group(1).strip()
            
            parts = voice_name.split('_')
            language = current_category.split('(')[0].strip() if current_category != "Unknown" else "Unknown"
            gender_prefix = parts[0][1]
            gender = 'Male' if gender_prefix == 'm' else 'Female'
            name_part = parts[-1].capitalize() if len(parts) > 1 else 'Unknown'
            
            display_name = f"{language} {gender} ({name_part})"
            description = f"{language} voice: {voice_name}"
            voices.append((voice_name, display_name, description))

    return sorted(voices, key=lambda x: x[1]) if voices else fallback_voices
