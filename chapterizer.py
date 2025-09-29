# /app/chapterizer.py

import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

# Set up logging
logger = logging.getLogger(__name__)

class Chapter(NamedTuple):
    number: int
    title: str
    content: str
    word_count: int

DEFAULT_CONFIG = {
    # Regex patterns to identify chapter headings in plain text.
    # They are tried in order. The first one to produce a split is used.
    "chapter_patterns": [
        # "CHAPTER 1", "Chapter One", "BOOK I", etc.
        re.compile(r"^\s*(chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+|[a-zA-Z]+)\s*$", re.IGNORECASE | re.MULTILINE),
        # Roman numerals on their own line
        re.compile(r"^\s*[IVXLCDM]+\s*$", re.IGNORECASE | re.MULTILINE),
        # Numbered headings
        re.compile(r"^\s*\d+\.\s+.*$", re.MULTILINE)
    ],
    # Configuration for fallback splitting by word count.
    "fallback_split_words": {
        "target_size": 6000,
        "min_size": 2500,
    },
    "min_chapter_word_count": 50
}


def _split_text_by_heuristics(text: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits plain text using regex patterns or word count fallback."""
    chapters = []
    
    # Try patterns first
    for pattern in config["chapter_patterns"]:
        potential_splits = pattern.split(text)
        if len(potential_splits) > 2: # Found more than one chapter
            logger.info(f"Splitting text using pattern: {pattern.pattern}")
            # The pattern itself is a delimiter; we need to find the headings again
            headings = pattern.findall(text)
            
            # First part of split is pre-chapter content (prologue/intro)
            intro_content = potential_splits[0].strip()
            if len(intro_content.split()) > config["min_chapter_word_count"]:
                 chapters.append(Chapter(
                    number=1, title="Introduction", content=intro_content,
                    word_count=len(intro_content.split())
                ))

            for i, content in enumerate(potential_splits[1:]):
                content = content.strip()
                if len(content.split()) < config["min_chapter_word_count"]:
                    continue
                
                # Heuristic title from heading or just a number
                title = " ".join(headings[i]).strip() if i < len(headings) else f"Chapter {len(chapters) + 1}"
                
                chapters.append(Chapter(
                    number=len(chapters) + 1, title=title.title(), content=content,
                    word_count=len(content.split())
                ))

            if chapters:
                return chapters

    # Fallback to word count if no patterns matched
    logger.warning("No chapter patterns matched. Falling back to word count split.")
    fallback_conf = config["fallback_split_words"]
    paragraphs = re.split(r'\n\s*\n', text)
    current_chapter_content = []
    current_word_count = 0
    
    for para in paragraphs:
        para_word_count = len(para.split())
        if current_word_count > 0 and (current_word_count + para_word_count) > fallback_conf["target_size"]:
            content = "\n\n".join(current_chapter_content)
            chapters.append(Chapter(
                number=len(chapters) + 1, title=f"Part {len(chapters) + 1}", content=content,
                word_count=len(content.split())
            ))
            current_chapter_content = []
            current_word_count = 0
        
        current_chapter_content.append(para)
        current_word_count += para_word_count

    # Add the last remaining part
    if current_chapter_content:
        content = "\n\n".join(current_chapter_content)
        chapters.append(Chapter(
            number=len(chapters) + 1, title=f"Part {len(chapters) + 1}", content=content,
            word_count=len(content.split())
        ))
        
    return chapters


def _split_epub(filepath: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits an EPUB file into chapters using its internal structure (spine)."""
    book = epub.read_epub(filepath)
    chapters = []
    
    # Use the book spine for correct chapter order
    # FIX: Handle cases where book.spine contains tuples (id, linear) instead of objects.
    spine_ids = [item[0] for item in book.spine]
    
    # Create a mapping from href to title from the TOC
    toc_map = {item.href: item.title for item in book.toc}

    for item_id in spine_ids:
        item = book.get_item_with_id(item_id)
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            # Extract text, preserving paragraphs
            paragraphs = [p.get_text() for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4'])]
            content = "\n\n".join(paragraphs).strip()
            
            if len(content.split()) < config["min_chapter_word_count"]:
                continue

            # Get title from TOC map or fallback to item ID
            title = toc_map.get(item.file_name, f"Chapter {len(chapters) + 1}")
            
            chapters.append(Chapter(
                number=len(chapters) + 1, title=title, content=content,
                word_count=len(content.split())
            ))

    return chapters


def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None
) -> List[Chapter]:
    """
    Main orchestration function to split a file or text into chapters.

    Args:
        filepath: The path to the source file.
        text_content: Optional pre-extracted text content. If None, it will be read.
        config: A configuration dictionary. Uses DEFAULT_CONFIG if None.

    Returns:
        A list of Chapter named tuples.
    """
    if config is None:
        config = DEFAULT_CONFIG

    p_filepath = Path(filepath)
    
    if p_filepath.suffix.lower() == '.epub':
        logger.info(f"Processing '{p_filepath.name}' as EPUB.")
        return _split_epub(filepath, config)
    else:

        logger.info(f"Processing '{p_filepath.name}' as plain text.")
        if not text_content:
            raise ValueError("text_content must be provided for non-EPUB files.")
        return _split_text_by_heuristics(text_content, config)
