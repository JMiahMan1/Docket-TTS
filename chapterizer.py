import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple

import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import fitz  # PyMuPDF for PDF processing

from text_cleaner import clean_text
from tts_service import normalize_text

logger = logging.getLogger(__name__)

class Chapter(NamedTuple):
    number: int
    title: str
    content: str
    word_count: int
    part_info: tuple = (1, 1) # (current_part, total_parts)

DEFAULT_CONFIG = {
    "max_chapter_word_count": 8000,
    "min_chapter_word_count": 50,
    "min_regex_chapter_length": 500
}

# Regex for the fallback method on unstructured text
REGEX_CHAPTER_PATTERN = re.compile(
    r'(?i)(?:^|\n\n|\n)(?:chapter|chap\.?|part|section|book)\s+([0-9]+|[IVXLCDM]+|[a-zA-Z\s]+)[\s:.-]*\n',
    re.MULTILINE
)

# Titles that will be explicitly discarded after chapterization
DISALLOWED_TITLES_PATTERN = re.compile(
    r'^(Table of Contents|Contents|Copyright|Dedication|Index|Bibliography|Glossary|Title Page|Also by)',
    re.IGNORECASE
)

def _split_large_chapter_into_parts(chapter: Chapter, max_words: int) -> List[Chapter]:
    """Splits a single large chapter into multiple parts based on word count."""
    if chapter.word_count <= max_words:
        return [chapter]

    logger.info(f"Chapter '{chapter.title}' is too long ({chapter.word_count} words). Splitting into parts.")
    parts = []
    paragraphs = re.split(r'\n\s*\n', chapter.content)
    current_part_content = []
    current_word_count = 0
    
    for para in paragraphs:
        para_word_count = len(para.split())
        if current_word_count > 0 and (current_word_count + para_word_count) > max_words:
            content = "\n\n".join(current_part_content)
            parts.append(Chapter(
                number=0, title=chapter.title,
                content=content, word_count=len(content.split())
            ))
            current_part_content = []
            current_word_count = 0
        
        current_part_content.append(para)
        current_word_count += para_word_count

    if current_part_content:
        content = "\n\n".join(current_part_content)
        parts.append(Chapter(
            number=0, title=chapter.title,
            content=content, word_count=len(content.split())
        ))
    
    total_parts = len(parts)
    return [p._replace(part_info=(i + 1, total_parts)) for i, p in enumerate(parts)]

def _regex_based_split(text: str, config: Dict[str, Any]) -> List[Chapter]:
    """Fallback chapter detection using regex for unstructured raw text."""
    logger.info("Attempting to split raw text using regex-based heuristics.")
    chapters = []
    matches = list(REGEX_CHAPTER_PATTERN.finditer(text))

    if not matches:
        logger.warning("Regex pattern found no chapter headings.")
        return []

    first_match_start = matches[0].start()
    intro_content = text[:first_match_start].strip()
    if len(intro_content) > config["min_regex_chapter_length"]:
        chapters.append(Chapter(0, "Introduction", intro_content, len(intro_content.split())))

    for i, match in enumerate(matches):
        start_index = match.end()
        end_index = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        
        content = text[start_index:end_index].strip()
        title_text = match.group(0).strip().replace('\n', ' ')
        
        if len(content) > config["min_regex_chapter_length"]:
            chapters.append(Chapter(0, title_text.title(), content, len(content.split())))

    logger.info(f"Regex split found {len(chapters)} potential raw chapters.")
    return chapters

def _extract_epub_chapters(filepath: str) -> List[Chapter]:
    """Primary Method for EPUB: Extracts raw chapters using file structure and h1/h2 tags."""
    logger.info("Extracting raw chapters from EPUB structure.")
    book = epub.read_epub(filepath)
    chapters = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), 'html.parser')
        
        title_tag = soup.find(['h1', 'h2'])
        title = title_tag.get_text().strip() if title_tag else Path(item.get_name()).stem.replace('_', ' ').title()
        
        if title_tag:
            title_tag.extract()
            
        content = soup.get_text(separator='\n\n', strip=True)
        chapters.append(Chapter(0, title, content, len(content.split())))
        
    return chapters

def _extract_docx_chapters(filepath: str) -> List[Chapter]:
    """Primary Method for DOCX: Extracts raw chapters using heading styles."""
    logger.info("Extracting raw chapters from DOCX heading styles.")
    doc = docx.Document(filepath)
    chapters = []
    current_chapter_content = []
    current_title = "Introduction"
    has_headings = any(p.style.name.lower().startswith('heading') for p in doc.paragraphs)

    if not has_headings:
        logger.warning("No heading styles found in DOCX. Will fall back to regex.")
        return [] # Return empty list to trigger regex fallback

    for p in doc.paragraphs:
        if p.style.name.lower().startswith('heading'):
            if current_chapter_content:
                content = '\n\n'.join(current_chapter_content)
                chapters.append(Chapter(0, current_title, content, len(content.split())))
            current_title = p.text.strip()
            current_chapter_content = []
        else:
            current_chapter_content.append(p.text)
    
    if current_chapter_content:
        content = '\n\n'.join(current_chapter_content)
        chapters.append(Chapter(0, current_title, content, len(content.split())))
        
    return chapters

def _apply_final_processing(initial_chapters: List[Chapter], config: Dict[str, Any]) -> List[Chapter]:
    """
    Cleans, normalizes, filters, and splits RAW chapters into final processable parts.
    """
    final_parts = []
    if not initial_chapters:
        return []
    
    logger.info(f"Starting final processing on {len(initial_chapters)} raw chapters found.")

    for raw_chapter in initial_chapters:
        # 1. First, check if the title is explicitly disallowed (e.g., "Table of Contents")
        if DISALLOWED_TITLES_PATTERN.search(raw_chapter.title):
            logger.info(f"Excluding explicitly disallowed chapter: '{raw_chapter.title}'")
            continue

        # 2. CLEAN the raw text content of the chapter.
        cleaned_content = clean_text(raw_chapter.content)
        
        # 3. NORMALIZE the cleaned text.
        normalized_content = normalize_text(cleaned_content)
        normalized_word_count = len(normalized_content.split())
        
        # 4. FILTER out chapters that are empty or too short AFTER all processing.
        if normalized_word_count < config["min_chapter_word_count"]:
            logger.info(f"Excluding chapter '{raw_chapter.title}' (normalized word count {normalized_word_count} is below threshold).")
            continue
            
        processed_chapter = raw_chapter._replace(content=normalized_content, word_count=normalized_word_count)
        
        # 5. SPLIT oversized chapters into smaller parts.
        parts = _split_large_chapter_into_parts(processed_chapter, config["max_chapter_word_count"])
        final_parts.extend(parts)
        
    if initial_chapters and not final_parts:
        logger.warning("All potential chapters were excluded after processing.")
        
    return [part._replace(number=i + 1) for i, part in enumerate(final_parts)]

def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None,
    debug: bool = False
) -> List[Chapter]:
    """
    Top-level function to extract raw chapters using a hierarchical,
    format-specific strategy with fallbacks.
    """
    if config is None:
        config = DEFAULT_CONFIG

    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    initial_chapters = [] # This list will contain raw, uncleaned chapters

    try:
        # STEP 1: Use the best extraction method for the file type.
        if ext == '.epub':
            initial_chapters = _extract_epub_chapters(filepath)
        elif ext == '.docx':
            initial_chapters = _extract_docx_chapters(filepath)
        
        # For text-based formats, or as a fallback, we prepare the full raw text.
        raw_text_for_fallback = text_content
        if not raw_text_for_fallback:
            if ext == '.pdf':
                logger.info("Extracting raw text from PDF for processing.")
                with fitz.open(filepath) as doc:
                    raw_text_for_fallback = "\n".join([page.get_text() for page in doc])
            elif ext == '.txt':
                 raw_text_for_fallback = p_filepath.read_text(encoding='utf-8')
            elif ext in ['.epub', '.docx'] and not initial_chapters:
                # This is the fallback path if primary EPUB/DOCX methods failed.
                logger.warning(f"Primary {ext} extraction failed, extracting raw text for regex fallback.")
                if ext == '.epub':
                    book = epub.read_epub(filepath)
                    full_text_parts = []
                    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                        soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                        full_text_parts.append(soup.get_text(separator='\n\n', strip=True))
                    raw_text_for_fallback = "\n\n".join(full_text_parts)
                else: # docx
                    doc = docx.Document(filepath)
                    raw_text_for_fallback = "\n\n".join([p.text for p in doc.paragraphs])

        # STEP 2: If the primary method found no chapters, use the regex fallback.
        if not initial_chapters and raw_text_for_fallback:
            # The raw text is cleaned once before the final fallback
            cleaned_raw_text = clean_text(raw_text_for_fallback)
            initial_chapters = _regex_based_split(cleaned_raw_text, config)

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}", exc_info=True)
        return []

    # STEP 3: Send the raw chapters to the final processing pipeline.
    final_parts = _apply_final_processing(initial_chapters, config)
    
    if debug:
        summary = f"\n--- Chapterization Summary for {p_filepath.name} ---\n"
        summary += f"Found {len(final_parts)} final parts to be processed.\n"
        for part in final_parts:
            part_str = f"Part {part.part_info[0]} of {part.part_info[1]}" if part.part_info[1] > 1 else ""
            summary += f"  - Part {part.number}: '{part.title}' ({part.word_count} words) {part_str}\n"
        summary += "--------------------------------------------------\n"
        logger.info(summary)
        print(summary)
        
    return final_parts
