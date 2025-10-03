import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from text_cleaner import clean_text

logger = logging.getLogger(__name__)

class Chapter(NamedTuple):
    number: int
    title: str
    content: str
    word_count: int

DEFAULT_CONFIG = {
    "chapter_patterns": [
        re.compile(r"^\s*(chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+|[a-zA-Z]+)\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*[IVXLCDM]+\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*\d+\.\s+.*$", re.MULTILINE)
    ],
    "fallback_split_words": {
        "target_size": 6000,
        "min_size": 2500,
    },
    "min_chapter_word_count": 100, # Increased to better filter out small non-narrative sections
    "epub_skip_filename_patterns": [
        re.compile(r'cover|toc|nav|copyright|title|dedication', re.IGNORECASE),
    ]
}

def _split_text_by_heuristics(text: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits plain text using regex patterns or word count fallback."""
    chapters = []
    
    for pattern in config["chapter_patterns"]:
        potential_splits = pattern.split(text)
        if len(potential_splits) > 2:
            logger.info(f"Splitting text using pattern: {pattern.pattern}")
            headings = pattern.findall(text)
            
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
                
                title = " ".join(headings[i]).strip() if i < len(headings) else f"Chapter {len(chapters) + 1}"
                
                chapters.append(Chapter(
                    number=len(chapters) + 1, title=title.title(), content=content,
                    word_count=len(content.split())
                ))

            if chapters:
                return chapters

    logger.warning("No chapter patterns matched. Falling back to word count split.")
    fallback_conf = config["fallback_split_words"]
    paragraphs = re.split(r'\n\s*\n', text)
    current_chapter_content = []
    current_word_count = 0
    
    for para in paragraphs:
        para_word_count = len(para.split())
        if current_word_count > fallback_conf["min_size"] and (current_word_count + para_word_count) > fallback_conf["target_size"]:
            content = "\n\n".join(current_chapter_content)
            chapters.append(Chapter(
                number=len(chapters) + 1, title=f"Part {len(chapters) + 1}", content=content,
                word_count=len(content.split())
            ))
            current_chapter_content = []
            current_word_count = 0
        
        current_chapter_content.append(para)
        current_word_count += para_word_count

    if current_chapter_content:
        content = "\n\n".join(current_chapter_content)
        chapters.append(Chapter(
            number=len(chapters) + 1, title=f"Part {len(chapters) + 1}", content=content,
            word_count=len(content.split())
        ))
        
    return chapters


def _split_epub(filepath: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits an EPUB file, pre-filtering and cleaning each section."""
    book = epub.read_epub(filepath)
    chapters = []
    
    spine_ids = [item[0] for item in book.spine]
    toc_map = {item.href: item.title for item in book.toc}

    for item_id in spine_ids:
        item = book.get_item_with_id(item_id)
        
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
            
        # Pre-filter based on common non-narrative filenames
        is_skipped_filename = any(pattern.search(item.file_name) for pattern in config["epub_skip_filename_patterns"])
        if is_skipped_filename:
            logger.info(f"Skipping EPUB item due to filename match: {item.file_name}")
            continue

        soup = BeautifulSoup(item.get_content(), 'html.parser')
        paragraphs = [p.get_text() for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4'])]
        raw_content = "\n\n".join(paragraphs).strip()
        
        # Clean the content of this specific section
        content = clean_text(raw_content)
        word_count = len(content.split())
        
        # Pre-filter based on word count after cleaning
        if word_count < config["min_chapter_word_count"]:
            logger.info(f"Skipping EPUB item {item.file_name} due to low word count after cleaning ({word_count} words).")
            continue

        title = toc_map.get(item.file_name, f"Chapter {len(chapters) + 1}")
        
        chapters.append(Chapter(
            number=len(chapters) + 1, title=title, content=content,
            word_count=word_count
        ))

    return chapters


def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None
) -> List[Chapter]:
    """
    Main orchestration function to split a file or text into chapters.
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
        
        # For plain text, clean the whole document first, then split.
        cleaned_full_text = clean_text(text_content)
        return _split_text_by_heuristics(cleaned_full_text, config)
