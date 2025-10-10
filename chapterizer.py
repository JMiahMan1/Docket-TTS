import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple
from urllib.parse import unquote

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

from text_cleaner import clean_text
from tts_service import normalize_text

logger = logging.getLogger('werkzeug')

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

NUMBERED_CHAPTER_PATTERN = re.compile(
    r'^\s*(chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+)\s*[:.\-]?\s*(.*)\s*$',
    re.IGNORECASE | re.MULTILINE
)
NAMED_CHAPTER_PATTERN = re.compile(
    r'^\s*(prologue|epilogue|introduction|appendix|acknowledgments|dedication|foreword|preface|title page)\s*[:.\-]?\s*(.*)\s*$',
    re.IGNORECASE | re.MULTILINE
)
DISALLOWED_TITLES_PATTERN = re.compile(
    r'^(Table of Contents|Contents|Copyright|Index|Bibliography|Works Cited|References|Glossary|Also by|List of|Appendix|Endorsements)',
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

def _apply_final_processing(initial_chapters: List[Chapter], config: Dict[str, Any], debug_level: str = 'off') -> List[Chapter]:
    final_parts = []
    if not initial_chapters:
        return []
    logger.debug(f"Starting final processing on {len(initial_chapters)} raw chapters found.")
    for raw_chapter in initial_chapters:
        if DISALLOWED_TITLES_PATTERN.search(raw_chapter.original_title):
            logger.debug(f"Excluding explicitly disallowed chapter title: '{raw_chapter.original_title}'")
            continue
        cleaned_content = clean_text(raw_chapter.content, debug_level=debug_level)
        normalized_content = normalize_text(cleaned_content, debug_level=debug_level)
        normalized_word_count = len(normalized_content.split())
        if normalized_word_count < config["min_chapter_word_count"]:
            logger.debug(f"Excluding chapter '{raw_chapter.original_title}' (final word count {normalized_word_count} is below threshold).")
            continue
        processed_chapter = raw_chapter._replace(content=normalized_content, word_count=normalized_word_count)
        parts = _split_large_chapter_into_parts(processed_chapter, config["max_chapter_word_count"])
        final_parts.extend(parts)
    if initial_chapters and not final_parts:
        logger.warning("All potential chapters were excluded after processing.")
    return [part._replace(number=i + 1) for i, part in enumerate(final_parts)]

# --- NEW/FIXED EPUB-SPECIFIC HELPERS ---

# FIX 1: Update to return list of (title, href) tuples
def _get_epub_toc_references(filepath: str, debug_level: str = 'off') -> List[tuple[str, str]]:
    """Parses an EPUB's TOC recursively to get an accurate list of chapter titles and their file paths."""
    logger.debug("Attempting to get chapter titles and references from EPUB TOC.")
    references = []
    try:
        book = epub.read_epub(filepath)
        def _recursive_parser(toc_items):
            for item in toc_items:
                if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], list):
                    _recursive_parser(item[1])
                elif hasattr(item, 'href') and hasattr(item, 'title'):
                    # The href is the key to fetch the content later
                    references.append((item.title, unquote(item.href)))
        _recursive_parser(book.toc)
    except Exception as e:
        logger.error(f"Failed to parse EPUB TOC references for {filepath}: {e}", exc_info=True)
        return []
    logger.debug(f"Found {len(references)} references via EPUB TOC: {[r[0] for r in references]}")
    return references

# FIX 2: New function to parse EPUB content based on the TOC references
def _chapterize_by_epub_content(filepath: str, references: List[tuple[str, str]], debug_level: str = 'off') -> List[Chapter]:
    """Splits an EPUB into chapters by processing the linked files from the TOC."""
    logger.debug(f"Chapterizing EPUB based on {len(references)} TOC links.")
    initial_chapters = []
    try:
        book = epub.read_epub(filepath)
    except Exception as e:
        logger.error(f"Failed to read EPUB for content extraction: {e}")
        return []
        
    for i, (title, href) in enumerate(references):
        try:
            # Find the item corresponding to the href
            item = book.get_item_with_href(href)
            if item and item.media_type == 'application/xhtml+xml':
                # Use BeautifulSoup to get clean text from the chapter's XHTML
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                content = soup.get_text()
                
                # Simple cleanup of leading/trailing whitespace and excessive newlines
                cleaned_content = re.sub(r'\n{3,}', '\n\n', content).strip()
                word_count = len(cleaned_content.split())
                
                if word_count > 0:
                    # Sanitize the title - remove common chapter markers if present in title for cleaner metadata
                    clean_title_match = re.match(r'^(Chapter|Part|Book|Section)\s+[\dIVXLCDM]+\s*[:.\-]?\s*(.*)\s*$', title.strip(), re.IGNORECASE)
                    final_title = clean_title_match.group(2).strip().title() if clean_title_match and clean_title_match.group(2) else title.strip().title()
                    
                    initial_chapters.append(Chapter(
                        number=0, 
                        title=final_title, 
                        original_title=title.strip(), 
                        content=cleaned_content, 
                        word_count=word_count
                    ))
            elif debug_level == 'trace':
                logger.debug(f"  > Trace: Skipping TOC reference {title} ({href}) as it is not XHTML content.")
        except Exception as e:
            logger.error(f"Error processing EPUB chapter '{title}' ({href}): {e}")
            
    logger.debug(f"Extracted {len(initial_chapters)} chapters directly from EPUB files.")
    return initial_chapters


# --- RESTORED FALLBACK METHODS ---
def _split_text_by_titles(text: str, titles: List[str]) -> List[Chapter]:
    """
    (FALLBACK) Splits a block of text into chapters based on a provided list of titles.
    NOTE: This is the old, less reliable method, kept for consistency with the original plan.
    """
    logger.debug(f"(Fallback) Splitting text based on {len(titles)} found titles.")
    chapters = []
    title_patterns = [re.escape(title) for title in titles]
    # Use word boundary on all-caps titles to prevent overmatching, otherwise only at line start
    title_pattern_str = '|'.join(
        [r"^\s*" + p + r"\s*$" for p in title_patterns if p.upper() != p] + 
        [r"\b" + p + r"\b" for p in title_patterns if p.upper() == p]
    )
    split_pattern = re.compile(r"^\s*(" + title_pattern_str + r")\s*$", re.IGNORECASE | re.MULTILINE)
    
    last_end = 0
    matches = list(split_pattern.finditer(text))

    if not matches:
        return []

    # Handle content before the first matched title as a preface/introduction
    if matches[0].start() > 100: # If there's significant content before the first real chapter
        title = "Introduction"
        content = text[0:matches[0].start()].strip()
        chapters.append(Chapter(0, title, title, content, len(content.split())))

    # Create chapters based on the locations of the titles
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start_index = match.start()
        end_index = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        content = text[start_index:end_index].strip()
        if content:
            chapters.append(Chapter(0, title, title, content, len(content.split())))

    logger.debug(f"(Fallback) Successfully constructed {len(chapters)} chapters from text split.")
    return chapters

def _chapterize_by_pdf_heuristics(filepath: str, full_text: str, debug_level: str = 'off') -> List[Chapter]:
    logger.debug(f"Attempting to find chapters in '{filepath}' using PDF heuristics. (Placeholder)")
    # This is a placeholder for the more complex PDF-specific logic
    return []

def _chapterize_by_text_toc(text: str, debug_level: str = 'off') -> List[Chapter]:
    logger.debug("Attempting to find chapters using text-based TOC parsing. (Placeholder)")
    # This is a placeholder for the text-based TOC logic
    return []

def _find_raw_chapters_by_regex(text: str, debug_level: str = 'off') -> List[Chapter]:
    logger.debug("Finding raw chapter breaks in text using simple regex.")
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
    logger.debug(f"Found {len(chapters)} potential raw chapters via regex.")
    return chapters

# --- MAIN DISPATCHER FUNCTION ---
def chapterize(
    filepath: str,
    text_content: str, # Note: this is the combined text blob, still used for fallbacks
    config: Dict[str, Any] = None,
    debug_level: str = 'off'
) -> List[Chapter]:
    if config is None:
        config = DEFAULT_CONFIG
    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    initial_chapters = []
    references = [] # Store references here for reuse in fallback
    logger.debug(f"Starting chapterization for {p_filepath.name} with debug level '{debug_level}'")
    
    # Step 1: Attempt the most robust EPUB method: by TOC and file processing
    if ext == '.epub':
        # FIX: Get full references (title, href)
        references = _get_epub_toc_references(filepath, debug_level)
        if references:
            # FIX: Use the new content-based parser which is more reliable than text splitting
            initial_chapters = _chapterize_by_epub_content(filepath, references, debug_level)
            
    # Step 2: Fallback to the old methods if EPUB file processing failed or for other formats
    if not initial_chapters and ext in ['.epub', '.pdf', '.docx', '.txt']:
        logger.warning(f"Structured EPUB parsing failed for '{p_filepath.name}'. Falling back to text heuristics.")
        
        # EPUB Fallback: Try splitting the text blob using TOC titles (less reliable)
        if ext == '.epub' and references:
            toc_titles = [r[0] for r in references] # Extract titles from references
            initial_chapters = _split_text_by_titles(text_content, toc_titles)
        
        # PDF Fallback (Placeholder)
        if not initial_chapters and ext == '.pdf':
            initial_chapters = _chapterize_by_pdf_heuristics(filepath, text_content, debug_level)
    
    # Text TOC Fallback (Placeholder)
    if not initial_chapters:
        initial_chapters = _chapterize_by_text_toc(text_content, debug_level)

    # Step 3: If all else fails, use the simple regex method
    if not initial_chapters:
        logger.warning(f"All structured methods failed for '{p_filepath.name}'. Reverting to simple regex fallback.")
        initial_chapters = _find_raw_chapters_by_regex(text_content, debug_level)

    if not initial_chapters:
        logger.error(f"Could not find any chapters in '{p_filepath.name}' by any method.")
        return []

    # Step 4: Final processing for whatever chapters were found
    final_parts = _apply_final_processing(initial_chapters, config, debug_level)
    
    if debug_level in ['debug', 'trace']:
        summary = f"\n--- Chapterization Summary for {p_filepath.name} ---\n"
        summary += f"Found {len(initial_chapters)} raw chapters before filtering.\n"
        summary += f"Filtered down to {len(final_parts)} final parts for processing.\n"
        if debug_level == 'trace':
            for part in final_parts:
                part_str = f"Part {part.part_info[0]} of {part.part_info[1]}" if part.part_info[1] > 1 else ""
                summary += f"  - Part {part.number}: '{part.original_title}' ({part.word_count} words) {part_str}\n"
        summary += "--------------------------------------------------\n"
        logger.debug(summary)
        
    return final_parts
