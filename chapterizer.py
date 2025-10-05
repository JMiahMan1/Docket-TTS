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
        re.compile(r"^\s*(chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+)\s*$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*(chapter|part|book|section)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s*$", re.IGNORECASE | re.MULTILINE),
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
    """If a chapter is too long, subdivide it into parts."""
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
                number=0, # Placeholder number
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
            number=0, # Placeholder number
            title=f"{chapter.title} - Part {len(parts) + 1}",
            content=content,
            word_count=len(content.split())
        ))
    
    return parts

def _split_text_by_heuristics(text: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits plain text using regex patterns, then subdivides large chapters."""
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
                content = potential_splits[content_index].strip()
                
                if len(content.split()) > config["min_chapter_word_count"]:
                    initial_chapters.append(Chapter(
                        number=0, title=title.title(), content=content,
                        word_count=len(content.split())
                    ))
                content_index += 1
                if pattern.groups > 0:
                    content_index += pattern.groups

            if initial_chapters:
                found_by_pattern = True
                break
    
    if not found_by_pattern:
        # If no patterns match, there are no "real" chapters to split, so we don't apply the two-tier logic.
        # This will be handled by the automatic book mode's word count check.
        logger.warning("No explicit chapter patterns matched in heuristic search.")
        return []

    # Second Tier: Subdivide any chapters that are too long
    final_chapters = []
    for chapter in initial_chapters:
        parts = _split_large_chapter_into_parts(chapter, config["max_chapter_word_count"])
        final_chapters.extend(parts)
        
    return final_chapters


def _split_epub(filepath: str, config: Dict[str, Any]) -> List[Chapter]:
    """Splits an EPUB using a hybrid approach and two-tier splitting."""
    book = epub.read_epub(filepath)
    initial_chapters = []
    
    spine_items = [item for item in book.spine if isinstance(item, tuple)]
    toc_map = {item.href: item.title for item in book.toc}

    for item_tuple in spine_items:
        item_id = item_tuple[0]
        item = book.get_item_with_id(item_id)
        
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
            
        is_skipped_filename = any(pattern.search(item.file_name) for pattern in config["epub_skip_filename_patterns"])
        if is_skipped_filename:
            logger.info(f"Skipping EPUB item due to filename match: {item.file_name}")
            continue

        soup = BeautifulSoup(item.get_content(), 'html.parser')
        raw_content = soup.get_text("\n\n").strip()
        
        cleaned_content = clean_text(raw_content)
        word_count = len(cleaned_content.split())
        
        if word_count < config["min_chapter_word_count"]:
            logger.info(f"Skipping EPUB item {item.file_name} due to low word count ({word_count} words).")
            continue

        # Run the heuristic splitter on the content of this single spine item
        sub_chapters = _split_text_by_heuristics(cleaned_content, config)

        if sub_chapters:
            initial_chapters.extend(sub_chapters)
        else:
            title = toc_map.get(item.file_name, item.file_name)
            initial_chapters.append(Chapter(
                number=0, title=title, content=cleaned_content,
                word_count=word_count
            ))

    # Second Tier: Subdivide any found chapters that are too long
    final_chapters = []
    for chapter in initial_chapters:
        parts = _split_large_chapter_into_parts(chapter, config["max_chapter_word_count"])
        final_chapters.extend(parts)

    return [chap._replace(number=i+1) for i, chap in enumerate(final_chapters)]

def chapterize(
    filepath: str,
    text_content: Optional[str] = None,
    config: Dict[str, Any] = None
) -> List[Chapter]:
    """Main orchestration function to split a file or text into chapters."""
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
        
        cleaned_full_text = clean_text(text_content)
        chapters = _split_text_by_heuristics(cleaned_full_text, config)
        
        # If heuristics found no real chapters, fallback to a simple word count split of the whole doc
        if not chapters:
            logger.warning("No explicit chapters found. Falling back to a simple word count split of the entire document.")
            full_doc_as_chapter = Chapter(number=1, title="Full Document", content=cleaned_full_text, word_count=len(cleaned_full_text.split()))
            chapters = _split_large_chapter_into_parts(full_doc_as_chapter, config["max_chapter_word_count"])
            # Renumber the parts
            chapters = [chap._replace(number=i+1, title=f"Part {i+1}") for i, chap in enumerate(chapters)]

        return chapters
