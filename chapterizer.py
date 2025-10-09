import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple
from urllib.parse import unquote

# Imports for new format-specific parsers
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

# Imports from other application modules
from text_cleaner import clean_text
from tts_service import normalize_text

logger = logging.getLogger(__name__)

class Chapter(NamedTuple):
    number: int
    title: str
    original_title: str
    content: str
    word_count: int
    part_info: tuple = (1, 1)

DEFAULT_CONFIG = {
    "max_chapter_word_count": 8000,
    "min_chapter_word_count": 100,
}

# --- REGEX PATTERNS (for fallback method) ---
NUMBERED_CHAPTER_PATTERN = re.compile(
    r'^\s*(chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+)\s*[:.\-]?\s*(.*)\s*$',
    re.IGNORECASE | re.MULTILINE
)
NAMED_CHAPTER_PATTERN = re.compile(
    r'^\s*(prologue|epilogue|introduction|appendix|acknowledgments|dedication|foreword|preface|title page)\s*[:.\-]?\s*(.*)\s*$',
    re.IGNORECASE | re.MULTILINE
)
DISALLOWED_TITLES_PATTERN = re.compile(
    r'^(Table of Contents|Contents|Copyright|Index|Bibliography|Glossary|Also by|List of|Appendix)',
    re.IGNORECASE
)

# --- HELPER FUNCTIONS ---

def _split_large_chapter_into_parts(chapter: Chapter, max_words: int) -> List[Chapter]:
    if chapter.word_count <= max_words:
        return [chapter]
    logger.info(f"Chapter '{chapter.original_title}' is too long ({chapter.word_count} words). Splitting into parts.")
    parts = []
    paragraphs = re.split(r'\n\s*\n', chapter.content)
    current_part_content = []
    current_word_count = 0
    for para in paragraphs:
        para_word_count = len(para.split())
        if current_word_count > 0 and (current_word_count + para_word_count) > max_words:
            content = "\n\n".join(current_part_content)
            parts.append(Chapter(0, chapter.title, chapter.original_title, content, len(content.split())))
            current_part_content = []
            current_word_count = 0
        current_part_content.append(para)
        current_word_count += para_word_count
    if current_part_content:
        content = "\n\n".join(current_part_content)
        parts.append(Chapter(0, chapter.title, chapter.original_title, content, len(content.split())))
    total_parts = len(parts)
    return [p._replace(part_info=(i + 1, total_parts)) for i, p in enumerate(parts)]


def _apply_final_processing(initial_chapters: List[Chapter], config: Dict[str, Any]) -> List[Chapter]:
    final_parts = []
    if not initial_chapters:
        return []
    logger.info(f"Starting final processing on {len(initial_chapters)} raw chapters found.")
    for raw_chapter in initial_chapters:
        if DISALLOWED_TITLES_PATTERN.search(raw_chapter.original_title):
            logger.info(f"Excluding explicitly disallowed chapter title: '{raw_chapter.original_title}'")
            continue
        cleaned_content = clean_text(raw_chapter.content)
        normalized_content = normalize_text(cleaned_content)
        normalized_word_count = len(normalized_content.split())
        if normalized_word_count < config["min_chapter_word_count"]:
            logger.info(f"Excluding chapter '{raw_chapter.original_title}' (final word count {normalized_word_count} is below threshold).")
            continue
        processed_chapter = raw_chapter._replace(content=normalized_content, word_count=normalized_word_count)
        parts = _split_large_chapter_into_parts(processed_chapter, config["max_chapter_word_count"])
        final_parts.extend(parts)
    if initial_chapters and not final_parts:
        logger.warning("All potential chapters were excluded after processing.")
    return [part._replace(number=i + 1) for i, part in enumerate(parts)]

# --- METHOD 1: EPUB TOC PARSING (RECURSIVE) ---
def _chapterize_by_epub_toc(filepath: str) -> List[Chapter]:
    """
    Finds chapters in an EPUB file by recursively parsing its structured Table of Contents.
    -- VERSION 4: With recursive parsing for nested TOCs. --
    """
    logger.info(f"Attempting to find chapters in '{filepath}' using EPUB TOC.")
    chapters = []
    try:
        book = epub.read_epub(filepath)
        content_map = {Path(item.file_name).name.lower(): item.get_content() for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
        
        # --- NEW RECURSIVE HELPER FUNCTION ---
        def _recursive_toc_parser(toc_items):
            for item in toc_items:
                # If the item is a tuple, it's a section with nested items. Recurse into it.
                if isinstance(item, tuple):
                    _recursive_toc_parser(item)
                    continue

                # If it's a link, process it.
                title = item.title
                href_unquoted = unquote(item.href.split('#')[0])
                href_filename = Path(href_unquoted).name.lower()

                if href_filename in content_map:
                    html_content = content_map[href_filename]
                    soup = BeautifulSoup(html_content, 'html.parser')
                    content = soup.get_text(separator='\n\n', strip=True)
                    
                    if content:
                        word_count = len(content.split())
                        chapters.append(Chapter(0, title, title, content, word_count))
                else:
                    logger.warning(f"TOC item '{title}' with href '{item.href}' not found in content map.")

        # Start the recursive parsing
        _recursive_toc_parser(book.toc)

    except Exception as e:
        logger.error(f"Failed to parse EPUB TOC for {filepath}: {e}", exc_info=True)
        return []
        
    logger.info(f"Found {len(chapters)} chapters via EPUB TOC.")
    return chapters


# --- METHOD 2: PDF HEURISTIC ANALYSIS ---
def _chapterize_by_pdf_heuristics(filepath: str, full_text: str) -> List[Chapter]:
    logger.info(f"Attempting to find chapters in '{filepath}' using PDF heuristics.")
    chapters = []
    try:
        doc = fitz.open(filepath)
        if not doc.page_count: return []
        font_sizes = {}
        for page in doc:
            for b in page.get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        size = round(s["size"])
                        font_sizes[size] = font_sizes.get(size, 0) + len(s["text"])
        if not font_sizes: return []
        body_font_size = max(font_sizes, key=font_sizes.get)
        heading_font_size_threshold = body_font_size + 2
        headings = []
        for page in doc:
            for b in page.get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        is_bold = s["flags"] & (1 << 4)
                        is_large = s["size"] >= heading_font_size_threshold
                        if is_large or is_bold:
                            text = s["text"].strip()
                            if text and len(text.split()) < 10:
                                headings.append(text)
        if not headings: return []
        split_pattern = '|'.join(re.escape(h) for h in headings)
        text_parts = re.split(f'({split_pattern})', full_text)
        if text_parts[0].strip():
             chapters.append(Chapter(0, "Introduction", "Introduction", text_parts[0].strip(), len(text_parts[0].strip().split())))
        for i in range(1, len(text_parts), 2):
            title = text_parts[i]
            content = (title + "\n\n" + text_parts[i+1]).strip()
            chapters.append(Chapter(0, title, title, content, len(content.split())))
    except Exception as e:
        logger.error(f"Failed to analyze PDF with heuristics for {filepath}: {e}")
        return []
    logger.info(f"Found {len(chapters)} potential chapters via PDF heuristics.")
    return chapters


# --- METHOD 3: TEXT-BASED TOC PARSING ---
def _chapterize_by_text_toc(text: str) -> List[Chapter]:
    logger.info("Attempting to find chapters using text-based TOC parsing.")
    toc_heading_match = re.search(r'^\s*(contents|table of contents)\s*$', text, re.IGNORECASE | re.MULTILINE)
    if not toc_heading_match:
        logger.info("No text-based TOC heading found.")
        return []

    end_of_toc_pattern = re.compile(r'^\s*(introduction|preface|foreword|chapter|prologue)', re.IGNORECASE | re.MULTILINE)
    toc_end_match = end_of_toc_pattern.search(text, toc_heading_match.end())
    toc_end_pos = toc_end_match.start() if toc_end_match else toc_heading_match.end() + 4000
    
    toc_block = text[toc_heading_match.end():toc_end_pos]
    toc_line_pattern = re.compile(r'^(?!\s*\d+\s*$)(.+?)(?:[\s.]*?)(\d+)\s*$', re.MULTILINE)
    
    chapter_titles = [match.group(1).strip() for match in toc_line_pattern.finditer(toc_block)]
    if not chapter_titles:
        logger.warning("Found a TOC heading, but could not extract any chapter titles from it.")
        return []
    
    logger.info(f"Extracted {len(chapter_titles)} titles from text-based TOC.")
    chapter_locations = []
    for title in chapter_titles:
        try:
            escaped_title = re.escape(title)
            title_match = re.search(f'^{escaped_title}\\s*$', text, re.IGNORECASE | re.MULTILINE)
            if title_match:
                chapter_locations.append({"title": title, "start": title_match.start()})
        except re.error:
            logger.warning(f"Skipping invalid title for regex search: {title}")

    if not chapter_locations:
        logger.warning("Extracted TOC titles, but could not locate them in the main text.")
        return []
        
    chapters = []
    chapter_locations.sort(key=lambda x: x['start'])
    for i, loc in enumerate(chapter_locations):
        start_index = loc['start']
        end_index = chapter_locations[i + 1]['start'] if (i + 1) < len(chapter_locations) else len(text)
        content = text[start_index:end_index].strip()
        chapters.append(Chapter(0, loc['title'], loc['title'], content, len(content.split())))
        
    logger.info(f"Successfully constructed {len(chapters)} chapters from text-based TOC.")
    return chapters


# --- METHOD 4: REGEX FALLBACK ---
def _find_raw_chapters_by_regex(text: str) -> List[Chapter]:
    logger.info("Finding raw chapter breaks in text using simple regex.")
    chapters = []
    matches = sorted(
        list(NUMBERED_CHAPTER_PATTERN.finditer(text)) + list(NAMED_CHAPTER_PATTERN.finditer(text)),
        key=lambda m: m.start()
    )
    if not matches:
        logger.warning("No chapter headings found via regex. Treating entire document as a single chapter.")
        return [Chapter(0, "Full Document", "Full Document", text, len(text.split()))]
    if matches[0].start() > 50:
        intro_content = text[:matches[0].start()].strip()
        chapters.append(Chapter(0, "Title Page", "Title Page", intro_content, len(intro_content.split())))
    for i, match in enumerate(matches):
        start_index = match.start()
        end_index = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        content = text[start_index:end_index].strip()
        original_title = match.group(0).strip().replace('\n', ' ')
        cleaned_title = " ".join(filter(None, match.groups())).strip().title()
        cleaned_title = re.sub(r"(\w)'(S|T|M|LL|RE|VE)\b", lambda m: m.group(1) + "'" + m.group(2).lower(), cleaned_title, flags=re.IGNORECASE)
        if not cleaned_title: cleaned_title = f"Section {i+1}"
        if content: chapters.append(Chapter(0, cleaned_title, original_title, content, len(content.split())))
    logger.info(f"Found {len(chapters)} potential raw chapters via regex.")
    return chapters


# --- MAIN DISPATCHER FUNCTION ---
def chapterize(
    filepath: str,
    text_content: str,
    config: Dict[str, Any] = None,
    debug: bool = False
) -> List[Chapter]:
    if config is None:
        config = DEFAULT_CONFIG

    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    initial_chapters = []

    # --- Step 1: Attempt format-specific chapterization (Primary) ---
    if ext == '.epub':
        initial_chapters = _chapterize_by_epub_toc(filepath)
    elif ext == '.pdf':
        initial_chapters = _chapterize_by_pdf_heuristics(filepath, text_content)
    
    # --- Step 2: If primary fails, try parsing a text-based TOC (Intermediate Fallback) ---
    if not initial_chapters:
        initial_chapters = _chapterize_by_text_toc(text_content)

    # --- Step 3: If all else fails, use the simple regex method (Final Fallback) ---
    if not initial_chapters:
        logger.warning(f"Primary and text-TOC methods failed for '{p_filepath.name}'. Reverting to simple regex fallback.")
        initial_chapters = _find_raw_chapters_by_regex(text_content)

    if not initial_chapters:
        logger.error(f"Could not find any chapters in '{p_filepath.name}' by any method.")
        return []

    # --- Step 4: Clean, normalize, and split any chapters that were found ---
    final_parts = _apply_final_processing(initial_chapters, config)
    
    if debug:
        summary = f"\n--- Chapterization Summary for {p_filepath.name} ---\n"
        summary += f"Found {len(initial_chapters)} raw chapters before filtering.\n"
        summary += f"Filtered down to {len(final_parts)} final parts for processing.\n"
        for part in final_parts:
            part_str = f"Part {part.part_info[0]} of {part.part_info[1]}" if part.part_info[1] > 1 else ""
            summary += f"  - Part {part.number}: '{part.original_title}' ({part.word_count} words) {part_str}\n"
        summary += "--------------------------------------------------\n"
        logger.info(summary)
        print(summary)
        
    return final_parts
