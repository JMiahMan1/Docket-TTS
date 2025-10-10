import re
import json
import inflect
import subprocess
import unicodedata
import yaml
from pathlib import Path
from argostranslate import translate
import argostranslate.package
import threading
import logging

logger = logging.getLogger('werkzeug')

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
    GREEK_WORDS = NORMALIZATION.get("greek_words", {})
    ARCHAIC_WORDS = NORMALIZATION.get("archaic_words", {})
    GREEK_TRANSLITERATION = NORMALIZATION.get("greek_transliteration", {})
    SUPERSCRIPTS = NORMALIZATION.get("superscripts", [])
    SUPERSCRIPT_MAP = NORMALIZATION.get("SUPERSCRIPT_MAP", {})
else:
    (ABBREVIATIONS, CI_ABBREVIATIONS, BIBLE_BOOKS, CASE_SENSITIVE_ABBRS, ROMAN_EXCEPTIONS,
     BIBLE_REFS, CONTRACTIONS, SYMBOLS, PUNCTUATION, LATIN_PHRASES, GREEK_WORDS, ARCHAIC_WORDS, GREEK_TRANSLITERATION,
     SUPERSCRIPTS, SUPERSCRIPT_MAP) = [{}, {}, [], [], set(), {}, {}, {}, {}, {}, {}, {}, {}, [], {}]

if RULES_PATH.exists():
    RULES = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))['normalization_rules']
else:
    RULES = []

_inflect = inflect.engine()
HEBREW_TO_ENGLISH = None

def ensure_translation_models_are_loaded():
    global HEBREW_TO_ENGLISH
    if HEBREW_TO_ENGLISH: return
    try:
        argostranslate.package.update_package_index()
        available_packages = argostranslate.package.get_available_packages()
        package_to_install = next(filter(lambda x: x.from_code == "he" and x.to_code == "en", available_packages), None)
        if package_to_install:
            if not getattr(package_to_install, 'installed', False):
                logger.info(f"Downloading and installing Argos Translate package: {package_to_install}")
                package_to_install.install()
            HEBREW_TO_ENGLISH = translate.get_translation_from_codes("he", "en")
        else:
            logger.warning("Hebrew to English translation package not found in Argos Translate index.")
    except Exception as e:
        logger.warning(f"Could not initialize Hebrew translation model: {e}")
        HEBREW_TO_ENGLISH = None

ensure_translation_models_are_loaded()

def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize('NFD', text)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def normalize_hebrew(text: str) -> str:
    def translate_match(match):
        hebrew_text = match.group(0)
        ensure_translation_models_are_loaded()
        if HEBREW_TO_ENGLISH:
            try:
                return f" {HEBREW_TO_ENGLISH.translate(hebrew_text)} "
            except Exception as e:
                logger.error(f"Error during Hebrew translation: {e}")
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
    valid_roman_pattern = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.IGNORECASE)
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
        if s.upper() in ROMAN_EXCEPTIONS: return s
        try:
            integer_val = roman_to_int(s)
            return f"Roman Numeral {_inflect.number_to_words(integer_val)}"
        except (KeyError, IndexError): return s

    def replacer(match):
        roman_str = match.group(1)
        if not valid_roman_pattern.match(roman_str): return roman_str
        keywords = {'chapter', 'part', 'book', 'section', 'act', 'unit', 'volume', 'homily'}
        preceding_text = text[:match.start()]
        preceding_words = preceding_text.split()
        
        has_strong_clue = False
        if preceding_words:
            last_word = preceding_words[-1].strip('.,:;()[]')
            if last_word.lower() in keywords or (last_word.istitle() and len(last_word) > 1):
                has_strong_clue = True
        
        if roman_str.lower() in common_words_to_exclude and not has_strong_clue: return roman_str
        return _convert_to_words(roman_str)

    return re.sub(r'\b([IVXLCDMivxlcdm]+)(?!\.)\b', replacer, text)

def _format_ref_segment(book_full, chapter, verses_str):
    chapter_words = _inflect.number_to_words(int(chapter)) if chapter.isdigit() else f"Roman Numeral {chapter}"
    if not verses_str: return f"{book_full} chapter {chapter_words}"
    suffix = ""
    verses_str = verses_str.strip().rstrip(".;,")
    if verses_str.lower().endswith("ff"):
        verses_str, suffix = verses_str[:-2].strip(), f" {BIBLE_REFS.get('ff', 'and following')}"
    elif verses_str.lower().endswith("f"):
        verses_str, suffix = verses_str[:-1].strip(), f" {BIBLE_REFS.get('f', 'and the following verse')}"
    prefix = "verses" if any(c in verses_str for c in ",–-—") else "verse"
    verses_str = re.sub(r"(\d)([a-z])", r"\1 \2", verses_str, flags=re.IGNORECASE)
    verses_str = verses_str.replace("–", "-").replace("—", "-").replace("-", " through ")
    verse_words = re.sub(r"\d+", lambda m: _inflect.number_to_words(int(m.group())), verses_str)
    return f"{book_full} chapter {chapter_words}, {prefix} {verse_words}{suffix}"

def normalize_scripture(text: str) -> str:
    bible_abbr_keys = {re.escape(k) for k, v in ABBREVIATIONS.items() if any(book in v for book in BIBLE_BOOKS) or "Homily" in v}
    full_book_names = {re.escape(book) for book in BIBLE_BOOKS}
    book_keys = sorted(list(bible_abbr_keys.union(full_book_names)), key=len, reverse=True)
    book_pattern_str = '|'.join(book_keys)

    book_chapter_pattern = re.compile(r'^\s*(' + book_pattern_str + r')\s+([IVXLCDM\d]+)\s*$', re.IGNORECASE | re.MULTILINE)
    ref_pattern = re.compile(r'\b(?:(' + book_pattern_str + r')\s+)?([IVXLCDM\d]+)[:\s]([\d\w\s,.\–-—]+(?:ff|f)?)', re.IGNORECASE)
    prose_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+([IVXLCDM\d]+):([\d\w\s,.-—]+(?:ff|f)?)', re.IGNORECASE)
    enclosed_pattern = re.compile(r'([(\[])([^)\]]+)([)\]])')
    shorthand_pattern = re.compile(r'\b(' + book_pattern_str + r')\s+([IVXLCDM\d]+)\b(?!:)', re.IGNORECASE)
    
    last_context = {'book': None, 'chapter': None}
    
    def book_chapter_replacer(match):
        nonlocal last_context
        book_abbr, chapter = match.groups()
        last_context['book'] = book_abbr.strip()
        last_context['chapter'] = chapter.strip()
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return f"{book_full} chapter {_inflect.number_to_words(int(chapter)) if chapter.isdigit() else f'Roman Numeral {chapter}'}"

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
        
    def shorthand_replacer(match):
        nonlocal last_context
        book_abbr, chapter = match.groups()
        last_context['book'] = book_abbr.strip()
        last_context['chapter'] = chapter
        book_full = CI_ABBREVIATIONS.get(book_abbr.replace('.','').lower(), book_abbr)
        return _format_ref_segment(book_full, chapter, "")

    def enclosed_replacer(match):
        nonlocal last_context
        original_match_text, opener, inner_text, closer = match.group(0), match.group(1), match.group(2), match.group(3)
        verse_abbr_match = re.match(r'^\s*v{1,2}\.\s*([\d\w\s,.\–-—]+)\s*$', inner_text, re.IGNORECASE)
        if verse_abbr_match and last_context.get('book') and last_context.get('chapter'):
            book_full = CI_ABBREVIATIONS.get(last_context['book'].lower().replace('.', ''), last_context['book'])
            return _format_ref_segment(book_full, last_context['chapter'], verse_abbr_match.group(1))
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
        if not found_scripture: return original_match_text
        return "".join(final_text_parts)
        
    text = book_chapter_pattern.sub(book_chapter_replacer, text)
    text = enclosed_pattern.sub(enclosed_replacer, text)
    text = prose_pattern.sub(replacer_simple, text)
    text = shorthand_pattern.sub(shorthand_replacer, text)
    return text

def _replace_leading_verse_marker(match):
    chapter, verse = match.groups()
    verse_words = _inflect.number_to_words(verse)
    if chapter:
        return f"chapter {_inflect.number_to_words(chapter)} verse {verse_words} "
    return f"verse {verse_words} "

def number_replacer(match):
    preceding_text = match.string[max(0, match.start()-20):match.start()]
    if re.search(r'[\(,”]\s*$', preceding_text):
        return match.group(0)

    num_str = match.group(0).strip()
    try:
        is_ordinal = any(num_str.lower().endswith(s) for s in ['st', 'nd', 'rd', 'th'])
        if not is_ordinal and len(num_str) == 4 and num_str.isdigit():
            num_int = int(num_str)
            if 2000 <= num_int <= 2099:
                if num_int < 2010: return _inflect.number_to_words(num_str).replace(" and ", " ")
                else: return f"{_inflect.number_to_words(num_str[:2])} {_inflect.number_to_words(num_str[2:])}"
            elif 1100 <= num_int <= 1999:
                first_part = _inflect.number_to_words(num_str[:2])
                last_two_digits = num_str[2:]
                if '00' < last_two_digits < '10':
                    return f"{first_part} oh {_inflect.number_to_words(last_two_digits[1])}"
                else:
                    second_part = _inflect.number_to_words(last_two_digits)
                    if second_part == "zero": second_part = "hundred"
                    return f"{first_part} {second_part}"
        return _inflect.number_to_words(num_str)
    except:
        return num_str

def currency_replacer(match):
    num_str = match.group(1)
    return f"{_inflect.number_to_words(num_str)} dollars"

def time_replacer(match):
    hour, minutes, period = match.groups()
    hour_words = _inflect.number_to_words(int(hour))
    period_words = " ".join(list(period.lower()))
    if minutes == "00": return f"{hour_words} {period_words}"
    else: return f"{hour_words} {_inflect.number_to_words(int(minutes))} {period_words}"

FUNCTION_REGISTRY = {
    "remove_superscripts": remove_superscripts, "normalize_scripture": normalize_scripture,
    "_strip_diacritics": _strip_diacritics, "normalize_hebrew": normalize_hebrew,
    "normalize_greek": normalize_greek, "expand_roman_numerals": expand_roman_numerals,
    "_replace_leading_verse_marker": _replace_leading_verse_marker, "number_replacer": number_replacer,
    "currency_replacer": currency_replacer, "time_replacer": time_replacer, 
}
SYMBOLS.pop('$', None)
DICTIONARY_REGISTRY = {
    "latin_phrases": LATIN_PHRASES,
    "non_bible_abbrs": {k: v for k, v in ABBREVIATIONS.items() if not any(book in v for book in BIBLE_BOOKS)},
    "contractions": CONTRACTIONS, 
    "symbols": SYMBOLS, 
    "punctuation": PUNCTUATION,
    "archaic_words": ARCHAIC_WORDS
}

def normalize_text(text: str, debug_level: str = 'off') -> str:
    """The main normalization function, driven by rules.yaml."""
    if debug_level in ['debug', 'trace']:
        logger.debug(f"Starting normalization. Initial text length: {len(text)}")
    
    ensure_translation_models_are_loaded()
    
    for rule in RULES:
        text_before = text if debug_level == 'trace' else ''
        rule_type = rule.get("type")
        
        try:
            if rule_type == "function":
                func = FUNCTION_REGISTRY.get(rule["function_name"])
                if func: text = func(text)

            elif rule_type == "regex":
                flags = sum(getattr(re, flag_name, 0) for flag_name in rule.get("flags", []))
                text = re.sub(rule["pattern"], rule["replacement"], text, flags=flags)

            elif rule_type == "regex_callback":
                func = FUNCTION_REGISTRY.get(rule["function_name"])
                if func:
                    flags = sum(getattr(re, flag_name, 0) for flag_name in rule.get("flags", []))
                    text = re.sub(rule["pattern"], func, text, flags=flags)

            elif rule_type == "dict_lookup":
                dictionary = DICTIONARY_REGISTRY.get(rule.get("dictionary_name"), {})
                options = rule.get("options", {})
                
                for key, value in sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True):
                    pattern = re.escape(key)
                    if options.get("word_boundary"):
                        pattern = r'\b' + pattern + r'\b'
                    
                    flags = 0
                    if options.get("case_insensitive"):
                        flags |= re.IGNORECASE
                    
                    text = re.sub(pattern, value, text, flags=flags)
        
        except Exception as e:
            rule_name = rule.get('name', rule.get('function_name', rule.get('dictionary_name', 'Unknown Rule')))
            logger.error(f"Error applying normalization rule '{rule_name}': {e}")

        if debug_level == 'trace' and text != text_before:
            rule_name = rule.get('name', rule.get('function_name', rule.get('dictionary_name', 'Unknown Rule')))
            logger.debug(f"  > Trace: Rule '{rule_name}' changed text length from {len(text_before)} to {len(text)}.")

    if debug_level in ['debug', 'trace']:
        logger.debug(f"Finished normalization. Final text length: {len(text)}")
    return text.strip()


def _log_stream(stream, log_prefix):
    """Reads a stream line by line and logs it with a prefix."""
    try:
        for line in iter(stream.readline, b''):
            if line:
                logger.info(f"{log_prefix}: {line.decode('utf-8', errors='replace').strip()}")
    except Exception as e:
        logger.error(f"Error reading stream for {log_prefix}: {e}")
    finally:
        stream.close()

class TTSService:
    def __init__(self, voice_path: str, speed_rate: str = "1.0"):
        self.speed_rate = speed_rate
        self.voice_path = Path(voice_path)
        if not self.voice_path.exists():
            raise FileNotFoundError(f"Voice model file not found at the provided path: {self.voice_path}")

    def synthesize(self, text: str, output_path: str, debug_level: str = 'off'):
        synthesized_text = text

        if not synthesized_text or not synthesized_text.strip():
            logger.warning(f"No text to synthesize for output file {output_path}. Generating 0.5s of silence.")
            silence_command = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.5", "-acodec", "libmp3lame", "-q:a", "9", output_path]
            try:
                subprocess.run(silence_command, check=True, capture_output=True)
                return output_path, ""
            except Exception as e:
                raise RuntimeError(f"FFmpeg failed to generate silent audio: {e}")

        piper_command = ["piper", "--model", str(self.voice_path), "--length_scale", str(self.speed_rate), "--output_file", "-"]
        ffmpeg_command = ["ffmpeg", "-y", "-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "-", "-threads", "0", "-acodec", "libmp3lame", "-q:a", "2", output_path]
        
        try:
            if debug_level in ['debug', 'trace']:
                logger.debug(f"Starting Piper/FFmpeg for {output_path}. Text length: {len(synthesized_text)} chars.")
            if debug_level == 'trace':
                logger.debug(f"  > Trace: Piper command: {' '.join(piper_command)}")
                logger.debug(f"  > Trace: FFmpeg command: {' '.join(ffmpeg_command)}")
                logger.debug(f"  > Trace: Final text snippet sent to Piper:\n---\n{synthesized_text[:500]}\n---")

            piper_process = subprocess.Popen(piper_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=piper_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            piper_stderr_thread = threading.Thread(target=_log_stream, args=(piper_process.stderr, "[Piper stderr]"))
            ffmpeg_stderr_thread = threading.Thread(target=_log_stream, args=(ffmpeg_process.stderr, "[FFmpeg stderr]"))
            piper_stderr_thread.start()
            ffmpeg_stderr_thread.start()

            def write_to_piper():
                try:
                    piper_process.stdin.write(synthesized_text.encode('utf-8'))
                except (IOError, BrokenPipeError) as e:
                    logger.warning(f"Could not write full text to Piper, it may have exited early: {e}")
                finally:
                    if piper_process.stdin:
                        piper_process.stdin.close()
            
            writer_thread = threading.Thread(target=write_to_piper)
            writer_thread.start()
            
            writer_thread.join()
            ffmpeg_process.communicate()
            
            piper_process.wait()
            piper_stderr_thread.join()
            ffmpeg_stderr_thread.join()
            
            if piper_process.returncode != 0:
                raise RuntimeError(f"Piper process failed. Check logs for [Piper stderr].")
            if ffmpeg_process.returncode != 0:
                raise RuntimeError(f"FFmpeg encoding process failed. Check logs for [FFmpeg stderr].")

        except FileNotFoundError as e:
            raise RuntimeError(f"Command not found: {e.filename}. Ensure piper-tts and ffmpeg are installed.")
        
        logger.info(f"Successfully synthesized audio to {output_path}")
        return output_path, synthesized_text
