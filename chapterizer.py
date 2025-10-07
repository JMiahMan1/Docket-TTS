import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple

import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import fitz

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

# This regex is the stable foundation for finding chapter breaks
RAW_CHAPTER_PATTERN = re.compile(
    r'^\s*(chapter|part|book|section|prologue|epilogue|introduction|appendix)\s+([0-9]+|[IVXLCDM]+|[A-Z\s]+)?\s*[:.\-]?\s*(.*)\s*$',
    re.IGNORECASE | re.MULTILINE
)

# Titles that will be explicitly discarded after chapterization
DISALLOWED_TITLES_PATTERN = re.compile(
    r'^(Table of Contents|Contents|Copyright|Dedication|Index|Bibliography|Glossary|Title Page|Also by|Acknowledgments|List of|Front Matter)',
    re.IGNORECASE
)

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
            parts.append(Chapter(
                number=0, title=chapter.title, original_title=chapter.original_title,
                content=content, word_count=len(content.split())
            ))
            current_part_content = []
            current_word_count = 0
        
        current_part_content.append(para)
        current_word_count += para_word_count

    if current_part_content:
        content = "\n\n".join(current_part_content)
        parts.append(Chapter(
            number=0, title=chapter.title, original_title=chapter.original_title,
            content=content, word_count=len(content.split())
        ))
    
    total_parts = len(parts)
    return [p._replace(part_info=(i + 1, total_parts)) for i, p in enumerate(parts)]

def _find_raw_chapters_by_regex(text: str) -> List[Chapter]:
    """Finds potential chapter breaks in raw, unaltered text and correctly splits the content between them."""
    logger.info("Finding raw chapter breaks in text using regex.")
    chapters = []
    matches = list(RAW_CHAPTER_PATTERN.finditer(text))

    if not matches:
        logger.warning("No chapter headings found via regex. Treating entire document as a single chapter.")
        return [Chapter(0, "Full Document", "Full Document", text, len(text.split()))]

    last_end = 0
    if matches[0].start() > 0:
        intro_content = text[:matches[0].start()].strip()
        if intro_content:
            chapters.append(Chapter(0, "Title Page", "Title Page", intro_content, len(intro_content.split())))
    
    for i, match in enumerate(matches):
        start_index = match.end()
        end_index = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        content = text[start_index:end_index].strip()
        
        original_title = match.group(0).strip().replace('\n', ' ')
        cleaned_title = " ".join(filter(None, match.groups())).strip().title()
        if not cleaned_title:
             cleaned_title = f"Section {i+1}"

        if content:
            chapters.append(Chapter(0, cleaned_title, original_title, content, len(content.split())))

    logger.info(f"Found {len(chapters)} potential raw chapters in the correct order.")
    return chapters

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
        
    return [part._replace(number=i + 1) for i, part in enumerate(final_parts)]

def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None,
    debug: bool = False
) -> List[Chapter]:
    if config is None:
        config = DEFAULT_CONFIG

    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    raw_text = text_content

    try:
        # STEP 1: Consolidate the entire book into a single RAW text string.
        if ext == '.epub':
            book = epub.read_epub(filepath)
            full_text_parts = []
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                full_text_parts.append(soup.get_text(separator='\n\n', strip=True))
            raw_text = "\n\n".join(full_text_parts)
        elif ext == '.docx':
            doc = docx.Document(filepath)
            raw_text = "\n\n".join([p.text for p in doc.paragraphs])
        elif ext == '.pdf':
            with fitz.open(filepath) as doc:
                raw_text = "\n".join([page.get_text() for page in doc])
        elif ext == '.txt' and raw_text is None:
             raw_text = p_filepath.read_text(encoding='utf-8')
        
        if not raw_text or not raw_text.strip():
            logger.warning(f"No text could be extracted from {p_filepath.name}.")
            return []

        # STEP 2: Use the stable regex method as the foundation to get chapter breaks.
        initial_chapters = _find_raw_chapters(raw_text)

        # STEP 3 (Enhancement): If it's an EPUB, try to improve titles from the TOC.
        if ext == '.epub' and initial_chapters:
            logger.info("Enhancing chapter titles using EPUB Table of Contents.")
            book = epub.read_epub(filepath)
            toc_titles = [item.title for item in book.toc]
            # Simple 1-to-1 mapping enhancement if counts match
            if len(toc_titles) == len(initial_chapters):
                enhanced_chapters = []
                for i, chapter in enumerate(initial_chapters):
                    enhanced_chapters.append(chapter._replace(title=toc_titles[i], original_title=toc_titles[i]))
                initial_chapters = enhanced_chapters

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}", exc_info=True)
        return []

    # STEP 4: Send the raw chapters to the final processing pipeline.
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
