import re
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, NamedTuple

import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString  # <-- Added NavigableString
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

NUMBERED_CHAPTER_PATTERN = re.compile(
    r'^\s*(week|day|chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+)\s*[:.\-]?\s*(.*)\s*$',
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

# --- START: New EPUB Helper Functions ---

def _get_epub_text_safely(element, stop_element):
    """
    Recursively get text from an element, but stop
    if we encounter the stop_element.
    """
    if element == stop_element:
        return ""
        
    if isinstance(element, NavigableString):
        return element.strip()
    
    # Ignore non-visible content
    if element.name in ['script', 'style', 'meta', 'head']:
        return ""

    text_parts = []
    # Use .contents to iterate over children
    if hasattr(element, 'contents'):
        for child in element.contents:
            if child == stop_element:
                break # Stop processing this element's children
            
            child_text = _get_epub_text_safely(child, stop_element)
            if child_text:
                text_parts.append(child_text)
                
    return " ".join(filter(None, text_parts)) # Filter empty strings

def _extract_html_text(html_content):
    """
    Uses BeautifulSoup to strip HTML tags and get clean text for TTS.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    body = soup.find('body')
    if body:
        return body.get_text(separator=' ', strip=True)
    else:
        return soup.get_text(separator=' ', strip=True)

def _split_epub_html_by_anchors(html_content, anchors_with_titles):
    """
    Splits a single HTML content string into multiple chapter strings
    based on a list of (anchor_id, title) tuples.
    """
    logger.info(f"  Splitting one file by {len(anchors_with_titles)} anchors...")
    soup = BeautifulSoup(html_content, 'html.parser')
    chapter_texts = []
    
    # Find the actual HTML elements for each anchor
    anchor_elements = []
    for anchor_id, title in anchors_with_titles:
        if not anchor_id:
            continue
        
        # Anchors can be by 'id' or <a name="...">
        element = soup.find(id=anchor_id)
        if not element:
            # Some anchors are <a name="..."/>
            element = soup.find('a', {'name': anchor_id})
        
        if element:
            anchor_elements.append((element, title))
        else:
            logger.warning(f"    Warning: Could not find anchor element for: {anchor_id}")

    if not anchor_elements:
        # Fallback: couldn't find any anchors, return whole text
        return [_extract_html_text(html_content)]

    # This logic extracts content *between* anchor elements
    for i, (start_element, title) in enumerate(anchor_elements):
        logger.info(f"    Processing chapter: {title}")
        content_parts = []
        
        # Get the next anchor element to know where to stop
        next_anchor_element = None
        if i + 1 < len(anchor_elements):
            next_anchor_element = anchor_elements[i+1][0]
        
        # Start from the anchor element and walk its siblings
        current_element = start_element
        
        while current_element:
            # Stop condition 1: We hit the next chapter's anchor element
            if next_anchor_element and current_element == next_anchor_element:
                break
            
            # Stop condition 2: The current element *contains* the next anchor
            if next_anchor_element and \
               hasattr(current_element, 'find_all') and \
               next_anchor_element in current_element.find_all():
                # This element is a parent of the next anchor.
                # Get text from it, but stop when we hit the anchor.
                content_parts.append(_get_epub_text_safely(current_element, next_anchor_element))
                break # Stop processing siblings
            
            if isinstance(current_element, NavigableString):
                content_parts.append(current_element.strip())
            # Only get text if it's not a script/style tag
            elif current_element.name not in ['script', 'style']:
                # Recursively get text from this element and its children
                content_parts.append(_get_epub_text_safely(current_element, next_anchor_element))

            # Move to the next element in the DOM tree (sibling)
            current_element = current_element.next_sibling
        
        final_text = " ".join(filter(None, content_parts)) # Join non-empty parts
        chapter_texts.append(final_text)

    return chapter_texts

def _chapterize_epub(filepath) -> List[Chapter]:
    """
    Robustly gets chapter titles and content from an EPUB
    by detecting the NCX structure and applying a specific filter.
    Returns a list of Chapter objects.
    """
    book = epub.read_epub(filepath)
    
    all_chapter_items = [] # List of (href, title)
    
    ncx_items = list(book.get_items_of_media_type('application/x-dtbncx+xml'))
    if not ncx_items:
        ncx_item = book.get_item_with_id('ncx')
        if not ncx_item:
            logger.error("Could not find NCX (TOC) file. Book may be EPUB 3.")
            return []
        ncx_items = [ncx_item]

    ncx_content = ncx_items[0].get_content()
    soup = BeautifulSoup(ncx_content, 'xml')
    
    nav_map = soup.find('navMap')
    if not nav_map:
        logger.error("No <navMap> found in NCX file.")
        return []
        
    all_nav_points = soup.find_all('navPoint')
    top_level_nav_points = nav_map.find_all('navPoint', recursive=False)
    
    logger.info(f"Found {len(all_nav_points)} total navigation points in NCX.")
    
    # --- Step 1: Detect NCX Structure ---
    is_nested = len(all_nav_points) > (len(top_level_nav_points) * 1.2)
    
    filtered_chapter_items = [] # List of (href, title)

    if is_nested:
        # --- "Romans" Logic: Nested NCX, use "opt-out" filter ---
        logger.info("Info: Detected Nested NCX. Applying exclusion filter.")
        EXCLUSION_KEYWORDS = ['conclusion', 'excursus']
        
        for point in all_nav_points:
            nav_label = point.find('navLabel')
            content_tag = point.find('content')
            
            if nav_label and content_tag:
                title = nav_label.get_text(strip=True)
                href = content_tag.get('src')
                if not (title and href):
                    continue
                
                ltitle = title.lower()
                
                if '(' in title or ')' in title:
                    continue
                if any(keyword in ltitle for keyword in EXCLUSION_KEYWORDS):
                    continue
                    
                filtered_chapter_items.append((href, title))
                
    else:
        # --- "Preach" Logic: Flat NCX, use "opt-in" filter ---
        logger.info("Info: Detected Flat NCX. Applying strict inclusion filter.")
        
        INCLUSION_KEYWORDS = [
            'contents', 'introduction', 'preface', 'prologue', 'epilogue', 
            'dedication', 'acknowledgments', 'abbreviations', 'endnotes', 
            'copyright', 'praise for', 'contributors', 'list of illustrations'
        ]
        
        CHAPTER_RE = re.compile(r'^(chapter|part)\s+', re.IGNORECASE)

        for i, point in enumerate(all_nav_points):
            nav_label = point.find('navLabel')
            content_tag = point.find('content')

            if nav_label and content_tag:
                title = nav_label.get_text(strip=True)
                href = content_tag.get('src')
                if not (title and href):
                    continue

                ltitle = title.lower()
                
                if i == 0: # Rule 0: Always keep the first item
                    filtered_chapter_items.append((href, title))
                    continue
                
                if any(keyword in ltitle for keyword in INCLUSION_KEYWORDS): # Rule 1
                    filtered_chapter_items.append((href, title))
                    continue
                
                if CHAPTER_RE.search(title): # Rule 2
                    filtered_chapter_items.append((href, title))
                    continue
    
    # --- Step 3: Group by file and extract content ---
    content_map = {} # Key: file_href, Value: list of (anchor, title)
    
    logger.info(f"Processing {len(filtered_chapter_items)} filtered navigation points.")
    
    for href, title in filtered_chapter_items:
        if not href:
            continue
            
        if '#' in href:
            file_href, anchor = href.split('#', 1)
        else:
            file_href, anchor = href, None
            
        if file_href not in content_map:
            content_map[file_href] = []
        
        if (anchor, title) not in content_map[file_href]:
            content_map[file_href].append((anchor, title))
            
    # --- Step 4: Build the Chapter objects ---
    initial_chapters = []
    chapter_number = 1

    for file_href, anchors_with_titles in content_map.items():
        try:
            item = book.get_item_with_href(file_href)
        except:
            # Handle malformed relative paths
            try:
                item = book.get_item_with_href(file_href.split('/')[-1])
            except:
                 logger.error(f"Error: could not find item with href {file_href}")
                 continue

        if not item:
            logger.error(f"Error: could not find item with href {file_href}")
            continue
            
        content = item.get_content().decode('utf-8')
        
        if len(anchors_with_titles) == 1 and anchors_with_titles[0][0] is None:
            # --- Case 1: Chapter-per-File ---
            title = anchors_with_titles[0][1]
            logger.info(f"Splitting by file: {file_href} (Title: {title})")
            full_text = _extract_html_text(content)
            
            initial_chapters.append(Chapter(
                number=chapter_number,
                title=title,
                original_title=title,
                content=full_text,
                word_count=len(full_text.split()),
                part_info=(1, 1)
            ))
            chapter_number += 1
            
        else:
            # --- Case 2: Anchors-in-One-File ---
            logger.info(f"Splitting by anchor in file: {file_href}")
            
            valid_anchors = [a for a in anchors_with_titles if a[0] is not None]
            
            if not valid_anchors:
                # Fallback
                logger.warning("  No anchors found, treating as single file per entry.")
                title = anchors_with_titles[0][1] # Use first title
                full_text = _extract_html_text(content)
                initial_chapters.append(Chapter(
                    number=chapter_number,
                    title=title,
                    original_title=title,
                    content=full_text,
                    word_count=len(full_text.split()),
                    part_info=(1, 1)
                ))
                chapter_number += 1
            else:
                # We have anchors, split by them
                chapter_texts = _split_epub_html_by_anchors(content, valid_anchors)
                
                for i, text_content in enumerate(chapter_texts):
                    title = valid_anchors[i][1]
                    initial_chapters.append(Chapter(
                        number=chapter_number,
                        title=title,
                        original_title=title,
                        content=text_content,
                        word_count=len(text_content.split()),
                        part_info=(1, 1)
                    ))
                    chapter_number += 1
            
    return initial_chapters

# --- END: New EPUB Helper Functions ---


def _split_large_chapter_into_parts(chapter: Chapter, max_words: int) -> List[Chapter]:
    """
    Takes a single chapter and splits it into multiple Chapter parts if it exceeds max_words.
    """
    # This logic appears unchanged from the original file, so it's kept as-is.
    # We assume it's complex and correct.
    content = chapter.content
    sentences = re.split(r'(?<=[.!?])\s+', content)
    
    parts = []
    current_part_text = []
    current_word_count = 0
    part_num = 1
    
    for sentence in sentences:
        sentence_word_count = len(sentence.split())
        
        if current_word_count + sentence_word_count > max_words and current_word_count > 0:
            # Finalize the current part
            part_content = " ".join(current_part_text)
            parts.append({
                "content": part_content,
                "word_count": current_word_count
            })
            # Start a new part
            current_part_text = [sentence]
            current_word_count = sentence_word_count
            part_num += 1
        else:
            current_part_text.append(sentence)
            current_word_count += sentence_word_count
            
    # Add the last part
    if current_part_text:
        part_content = " ".join(current_part_text)
        parts.append({
            "content": part_content,
            "word_count": current_word_count
        })
        
    final_chapter_parts = []
    total_parts = len(parts)
    for i, part in enumerate(parts):
        part_info = (i + 1, total_parts)
        final_chapter_parts.append(Chapter(
            number=chapter.number,
            title=chapter.title,
            original_title=chapter.original_title,
            content=part["content"],
            word_count=part["word_count"],
            part_info=part_info
        ))
        
    return final_chapter_parts

def _apply_final_processing(chapters: List[Chapter], config: Dict[str, Any]) -> List[Chapter]:
    """
    Cleans, normalizes, and splits chapters based on config.
    """
    max_words = config.get("max_chapter_word_count", DEFAULT_CONFIG["max_chapter_word_count"])
    min_words = config.get("min_chapter_word_count", DEFAULT_CONFIG["min_chapter_word_count"])
    
    processed_chapters = []
    for chapter in chapters:
        # Clean text
        cleaned_content = clean_text(chapter.content)
        
        # Normalize text for TTS
        normalized_content = normalize_text(cleaned_content)
        
        word_count = len(normalized_content.split())
        
        # Filter out chapters that are too short
        if word_count < min_words:
            # Check if it's a disallowed title
            if DISALLOWED_TITLES_PATTERN.search(chapter.original_title):
                logger.info(f"Skipping short/disallowed chapter: '{chapter.original_title}' ({word_count} words)")
                continue
            
            # Allow very short chapters if they are 'part' delimiters
            if chapter.original_title.lower().startswith('part '):
                logger.info(f"Keeping short 'Part' chapter: '{chapter.original_title}'")
            else:
                 logger.info(f"Skipping short chapter: '{chapter.original_title}' ({word_count} words)")
                 continue

        
        # Split large chapters
        if word_count > max_words:
            logger.info(f"Chapter '{chapter.original_title}' ({word_count} words) is too large. Splitting...")
            split_parts = _split_large_chapter_into_parts(
                chapter._replace(content=normalized_content, word_count=word_count),
                max_words
            )
            processed_chapters.extend(split_parts)
        else:
            processed_chapters.append(
                chapter._replace(content=normalized_content, word_count=word_count)
            )
            
    # Re-number and finalize
    final_parts = []
    current_chapter_num = 1
    for i, part in enumerate(processed_chapters):
        final_parts.append(
            part._replace(number=current_chapter_num)
        )
        # Increment chapter number only if this is the last part of a chapter
        if part.part_info[0] == part.part_info[1]:
            current_chapter_num += 1

    return final_parts

def _find_raw_chapters(raw_text: str) -> List[Chapter]:
    """
    Uses regex to find chapters in a raw text blob.
    This is the generic fallback for PDF, DOCX, TXT.
    """
    chapters = []
    
    # First, find all potential chapter starts
    numbered_matches = list(NUMBERED_CHAPTER_PATTERN.finditer(raw_text))
    named_matches = list(NAMED_CHAPTER_PATTERN.finditer(raw_text))
    
    all_matches = sorted(numbered_matches + named_matches, key=lambda m: m.start())
    
    if not all_matches:
        # No chapters found, treat the whole text as one chapter
        return [Chapter(1, "Chapter 1", "Chapter 1", raw_text, len(raw_text.split()))]

    for i, match in enumerate(all_matches):
        start_index = match.start()
        end_index = all_matches[i+1].start() if i + 1 < len(all_matches) else len(raw_text)
        
        content = raw_text[start_index:end_index].strip()
        
        # Extract title from the match object
        # Numbered match groups: 1=(type), 2=(number), 3=(title)
        # Named match groups: 1=(type), 2=(title)
        groups = match.groups()
        if len(groups) == 3: # Numbered chapter
            ch_type = groups[0].strip()
            ch_num = groups[1].strip()
            ch_title = groups[2].strip()
            original_title = f"{ch_type} {ch_num}"
            if ch_title:
                original_title += f": {ch_title}"
            title = ch_title if ch_title else f"{ch_type} {ch_num}"
        else: # Named chapter
            original_title = groups[0].strip().title()
            ch_title = groups[1].strip()
            title = ch_title if ch_title else original_title

        # Clean up the content (remove the title line)
        content_lines = content.splitlines()
        if content_lines and content_lines[0].strip() == match.group(0).strip():
            content = "\n".join(content_lines[1:]).strip()
            
        word_count = len(content.split())
        
        if not DISALLOWED_TITLES_PATTERN.search(original_title):
            chapters.append(Chapter(
                number=i + 1,
                title=title,
                original_title=original_title,
                content=content,
                word_count=word_count
            ))

    return chapters


def chapterize_file(filepath: str, config: Optional[Dict[str, Any]] = None, debug: bool = False) -> List[Chapter]:
    """
    Processes a file (pdf, docx, epub, txt) and splits it into chapters.
    """
    if config is None:
        config = DEFAULT_CONFIG
        
    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    
    raw_text: Optional[str] = None
    initial_chapters: List[Chapter] = []

    try:
        # --- START: MODIFIED Main Processing Block ---
        
        if ext == '.epub':
            # Use our new, robust NCX-based logic.
            # This function returns List[Chapter], bypassing _find_raw_chapters
            initial_chapters = _chapterize_epub(filepath)
            
        elif ext == '.docx':
            doc = docx.Document(filepath)
            raw_text = "\n\n".join([p.text for p in doc.paragraphs])
            
        elif ext == '.pdf':
            with fitz.open(filepath) as doc:
                raw_text = "\n".join([page.get_text() for page in doc])
                
        elif ext == '.txt':
             raw_text = p_filepath.read_text(encoding='utf-8')
        
        # ---
        # This logic block handles the two different paths:
        # 1. EPUB: initial_chapters is populated, raw_text is None.
        # 2. Others: raw_text is populated, initial_chapters is empty.
        # ---
        if raw_text and not initial_chapters:
            # Process raw text from PDF, DOCX, TXT
            initial_chapters = _find_raw_chapters(raw_text)
        elif not raw_text and not initial_chapters:
            # This triggers if EPUB processing failed *or* other file types were empty
            logger.warning(f"No text or chapters could be extracted from {p_filepath.name}.")
            return []
            
        # --- END: MODIFIED Main Processing Block ---

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}", exc_info=True)
        return []

    final_parts = _apply_final_processing(initial_chapters, config)
    
    if debug:
        summary = f"\n--- Chapterization Summary for {p_filepath.name} ---\n"
        summary += f"Found {len(initial_chapters)} raw chapters before filtering.\n"
        summary += f"Filtered down to {len(final_parts)} final parts for processing.\n"
        for part in final_parts:
            part_str = f"Part {part.part_info[0]} of {part.part_info[1]}" if part.part_info[1] > 1 else ""
            summary += f"  - Part {part.number}: '{part.original_title}' ({part.word_count} words) {part_str}\n"
        logger.info(summary)

    return final_parts
