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
        re.compile(r"^\s*([A-Z][A-Z0-9\s',-]{4,70})\s*$", re.MULTILINE),
    ],
    "max_chapter_word_count": 8000,
    "min_chapter_word_count": 150,
    "epub_skip_filename_patterns": [
        re.compile(r'cover|toc|nav|copyright|title|dedication|imprint|halftitle|prelims', re.IGNORECASE),
    ]
}

def _split_large_chapter_into_parts(chapter: Chapter, max_words: int) -> List[Chapter]:
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
                number=0,
                title=f"{chapter.title} - Part {len(parts) + 1}",
                content=content,
                word_count=len(content.split())
            ))
            current_part_content = []
            current_word_count = 0
        
        current_part_content.append(para)
        current_word_count += para_word_count

    if current_part_content:
        content = "\n\n".join(current_part_content)
        parts.append(Chapter(
            number=0,
            title=f"{chapter.title} - Part {len(parts) + 1}",
            content=content,
            word_count=len(content.split())
        ))
    
    return parts

def _split_text_by_heuristics(text: str, config: Dict[str, Any]) -> List[Chapter]:
    initial_chapters = []
    found_by_pattern = False
    
    for pattern in config["chapter_patterns"]:
        potential_splits = re.split(pattern, text)
        if len(potential_splits) > 1:
            logger.info(f"Splitting text using pattern: {pattern.pattern}")
            headings = re.findall(pattern, text)
            
            intro_content = potential_splits[0].strip()
            if len(intro_content.split()) > config["min_chapter_word_count"]:
                 initial_chapters.append(Chapter(
                    number=0, title="Introduction", content=intro_content,
                    word_count=len(intro_content.split())
                ))

            content_index = 1
            for i, heading_match in enumerate(headings):
                title = heading_match if isinstance(heading_match, str) else ' '.join(filter(None, heading_match)).strip()
                if content_index < len(potential_splits):
                    content = potential_splits[content_index].strip()
                    if len(content.split()) > config["min_chapter_word_count"]:
                        initial_chapters.append(Chapter(
                            number=0, title=title.title(), content=content,
                            word_count=len(content.split())
                        ))
                    content_index += (pattern.groups + 1) if pattern.groups > 0 else 1

            if initial_chapters:
                found_by_pattern = True
                break
    
    return initial_chapters

def _split_epub_by_toc(book: epub.EpubBook, config: Dict[str, Any]) -> List[Chapter]:
    chapters = []
    
    for item in book.toc:
        book_item = book.get_item_with_href(item.href)
        if not book_item or book_item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
            
        soup = BeautifulSoup(book_item.get_content(), 'html.parser')
        content = soup.get_text("\n\n").strip()
        
        if len(content.split()) > config["min_chapter_word_count"]:
            chapters.append(Chapter(
                number=0,
                title=item.title,
                content=content,
                word_count=len(content.split())
            ))
    return chapters

def _apply_final_processing(initial_chapters: List[Chapter], config: Dict[str, Any]) -> List[Chapter]:
    final_chapters = []
    for chapter in initial_chapters:
        cleaned_content = clean_text(chapter.content)
        cleaned_word_count = len(cleaned_content.split())
        
        if cleaned_word_count < config["min_chapter_word_count"]:
            continue
            
        cleaned_chapter = chapter._replace(content=cleaned_content, word_count=cleaned_word_count)
        
        parts = _split_large_chapter_into_parts(cleaned_chapter, config["max_chapter_word_count"])
        final_chapters.extend(parts)
        
    return [chap._replace(number=i+1) for i, chap in enumerate(final_chapters)]

def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None
) -> List[Chapter]:
    if config is None:
        config = DEFAULT_CONFIG

    initial_chapters = []
    p_filepath = Path(filepath)
    
    if p_filepath.suffix.lower() == '.epub':
        logger.info(f"Processing '{p_filepath.name}' as EPUB using ToC.")
        book = epub.read_epub(filepath)
        initial_chapters = _split_epub_by_toc(book, config)
    
    else:
        logger.info(f"Processing '{p_filepath.name}' as plain text.")
        if not text_content:
            raise ValueError("text_content must be provided for non-EPUB files.")
        
        cleaned_full_text = clean_text(text_content)
        initial_chapters = _split_text_by_heuristics(cleaned_full_text, config)
        
        if not initial_chapters:
             logger.warning("No explicit chapters found. Treating document as a single chapter.")
             initial_chapters.append(Chapter(number=0, title="Full Document", content=cleaned_full_text, word_count=len(cleaned_full_text.split())))

    return _apply_final_processing(initial_chapters, config)
