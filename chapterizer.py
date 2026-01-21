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

class ChapterizationProfile(NamedTuple):
    name: str
    description: str
    patterns: List[re.Pattern]

PROFILES = {
    "auto": ChapterizationProfile(
        "Auto-Detect", 
        "Automatically detect the best matching profile.", 
        []
    ),
    "standard": ChapterizationProfile(
        "Standard Fiction (Ch. 1, One, I...)",
        "Standard chapter headers like 'Chapter 1', 'Part II', 'One', 'Three'.",
        [
            # Numbered: Chapter 1, Part II, Section 3
            re.compile(r'^\s*(week|day|chapter|part|book|section)\s+([0-9]+|[IVXLCDM]+)\s*[:.\-]?\s*(.*)\s*$', re.IGNORECASE | re.MULTILINE),
            # Named: Prologue, Epilogue
            re.compile(r'^\s*(prologue|epilogue|introduction|appendix|acknowledgments|dedication|foreword|preface|title page)\s*[:.\-]?\s*(.*)\s*$', re.IGNORECASE | re.MULTILINE),
            # Standalone: "One", "Two", "I", "II" (Strict, no digits)
            re.compile(r'^\s*([IVXLCDM]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\s*$', re.IGNORECASE | re.MULTILINE)
        ]
    ),
    "journal": ChapterizationProfile(
        "Journal / Devotional (Dates, Days)",
        "Splits by dates (Jan 1), 'Day 123', or 'Entry #'. Best for diaries & devotionals.",
        [
            # Dates (Full): "January 1, 1890", "12th October", "1890-01-01"
            re.compile(r'^\s*(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*)?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\s*$', re.IGNORECASE | re.MULTILINE),
            re.compile(r'^\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\s*$', re.IGNORECASE | re.MULTILINE),
            # Simple Date (Day Month): "12 October"
            re.compile(r'^\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*)\s*$', re.IGNORECASE | re.MULTILINE),
            # Simple Date (Month Day): "October 12", "Jan 1"
            re.compile(r'^\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?)\s*$', re.IGNORECASE | re.MULTILINE),
             # Entry/Day #: "Entry 1", "Day 50", "Devotional 3"
            re.compile(r'^\s*(Entry|Day|Journal|Devotional)\s+(\d+)\s*$', re.IGNORECASE | re.MULTILINE)
        ]
    ),
    "none": ChapterizationProfile(
         "No Chapter Splitting",
         "Treats the entire file as a single chapter.",
         []
    )
}

DISALLOWED_TITLES_PATTERN = re.compile(
    r'^(Table of Contents|Contents|Copyright|Index|Bibliography|Glossary|Also by|List of|Appendix)',
    re.IGNORECASE
)

# --- TOC Detection Functions ---

def _detect_toc_section(text: str) -> tuple:
    """
    Detects the Table of Contents section in text.
    Returns (start_index, end_index, confidence) or (None, None, 0.0) if not found.
    
    TOC detection criteria:
    - TOC header ("Contents", "Table of Contents", etc.)
    - Multiple consecutive lines with chapter titles + page numbers
    - Patterns with leader dots/dashes
    """
    lines = text.split('\n')
    toc_start = None
    toc_end = None
    confidence = 0.0
    
    # TOC header patterns
    toc_header_pattern = re.compile(
        r'^\s*(Table\s+of\s+)?Contents?\s*$',
        re.IGNORECASE
    )
    
    # TOC entry patterns (with page numbers)
    toc_entry_patterns = [
        # "Chapter 1 ........ 5" or "Chapter 1...5"
        re.compile(r'(Chapter|Part|Section)\s+[\dIVXivx]+.*?[\.…\-\s]{3,}\d+\s*$', re.IGNORECASE),
        # "Introduction ........ 1"
        re.compile(r'^[A-Z][^\.]{5,60}[\.…\-\s]{3,}\d+\s*$'),
        # Simple: "Chapter 1 - 5" or "Chapter 1, page 5"
        re.compile(r'(Chapter|Part).*?[\-,]?\s*(?:page\s*)?\d+\s*$', re.IGNORECASE)
    ]
    
    # Find TOC header
    for i, line in enumerate(lines):
        if toc_header_pattern.match(line.strip()):
            toc_start = i
            confidence += 0.5
            logger.info(f"TOC header found at line {i}: {line.strip()[:50]}")
            break
    
    if toc_start is not None:
        # Look for consecutive TOC entries after header
        entry_count = 0
        last_entry_line = toc_start
        
        for i in range(toc_start + 1, min(toc_start + 100, len(lines))):
            line = lines[i].strip()
            
            # Skip empty lines
            if not line:
                if entry_count > 0 and i - last_entry_line > 3:
                    # More than 3 empty lines after entries, likely end of TOC
                    break
                continue
            
            # Check if line matches TOC entry pattern
            is_entry = any(pattern.search(line) for pattern in toc_entry_patterns)
            
            if is_entry:
                entry_count += 1
                last_entry_line = i
            elif entry_count > 0 and i - last_entry_line > 5:
                # Non-entry after several lines of no entries
                break
        
        if entry_count >= 3:  # At least 3 TOC entries found
            toc_end = last_entry_line
            confidence += min(entry_count * 0.1, 0.5)  # Up to 0.5 confidence from entries
            logger.info(f"TOC section detected: lines {toc_start}-{toc_end} with {entry_count} entries (confidence: {confidence:.2f})")
    
    if confidence >= 0.6:  # Require reasonable confidence
        # Convert line numbers to character indices
        start_char = sum(len(lines[i]) + 1 for i in range(toc_start))  # +1 for newline
        end_char = sum(len(lines[i]) + 1 for i in range(toc_end + 1))
        return (start_char, end_char, confidence)
    
    return (None, None, 0.0)

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
                logger.debug(f"    Next anchor found inside current element <{current_element.name}>. Extracting partial text.")
                content_parts.append(_get_epub_text_safely(current_element, next_anchor_element))
                break # Stop processing siblings
            
            if isinstance(current_element, NavigableString):
                if current_element.strip(): logger.debug(f"    Found text node: {current_element.strip()[:30]}...")
                content_parts.append(current_element.strip())
            # Only get text if it's not a script/style tag
            elif current_element.name not in ['script', 'style']:
                # Recursively get text from this element and its children
                text_found = _get_epub_text_safely(current_element, next_anchor_element)
                if text_found: logger.debug(f"    Found text in <{current_element.name}>: {text_found[:30]}...")
                content_parts.append(text_found)
            
            # Move to the next element in the DOM tree
            current_element = current_element.next_sibling

        if not content_parts:
             # Heuristic: If we found no text by walking siblings, the anchor might be 
             # nested deep inside a header (e.g. <h3><a id="foo"></a>Title</h3>).
             # In this case, we might want to start walking from the *parent's* next sibling.
             logger.debug(f"    No text in siblings of {start_element.name}. Checking parent siblings.")
             if start_element.parent:
                 current_element = start_element.parent.next_sibling
                 while current_element:
                    # Stop condition 1: We hit the next chapter's anchor element
                    if next_anchor_element and current_element == next_anchor_element:
                        break
                    
                    # Stop condition 2: The current element *contains* the next anchor
                    if next_anchor_element and \
                    hasattr(current_element, 'find_all') and \
                    next_anchor_element in current_element.find_all():
                        logger.debug(f"    Next anchor found inside current element <{current_element.name}> (Parent Walk). Extracting partial text.")
                        content_parts.append(_get_epub_text_safely(current_element, next_anchor_element))
                        break 
                    
                    if isinstance(current_element, NavigableString):
                        content_parts.append(current_element.strip())
                    elif current_element.name not in ['script', 'style']:
                         text_found = _get_epub_text_safely(current_element, next_anchor_element)
                         if text_found: content_parts.append(text_found)

                    current_element = current_element.next_sibling

            # Move to the next element in the DOM tree (sibling)
            # current_element = current_element.next_sibling # REMOVED: Managed in loops now
        
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
                
                # DEBUG LOGGING
                logger.info(f"    NavPoint: '{title}' (href: {href})")

                if '(' in title or ')' in title:
                    logger.info(f"      Filtered: Parentheses found in '{title}'")
                    continue
                if any(keyword in ltitle for keyword in EXCLUSION_KEYWORDS):
                    logger.info(f"      Filtered: Exclusion keyword in '{ltitle}'")
                    continue
                    
                logger.info(f"      Keep: '{title}'")
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
        
        # NOTE: normalize_text is deferred to the TTS stage to avoid hangs in the web thread
        # We'll use cleaned_content for word count estimations
        
        word_count = len(cleaned_content.split())
        
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
                chapter._replace(content=cleaned_content, word_count=word_count),
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

def _find_raw_chapters(raw_text: str, profile_key: str = "auto") -> List[Chapter]:
    """
    Uses regex to find chapters in a raw text blob based on the selected profile.
    """
    
    # --- Auto-Detection Heuristic ---
    if profile_key == "auto":
        logger.info("Auto-detecting chapter profile...")
        best_score = 0
        best_profile = "standard" # Default
        
        # Check first 5000 chars for matches
        sample_text = raw_text[:5000] 
        
        for key, profile in PROFILES.items():
            if key in ["auto", "none"]: continue
            
            score = 0
            for pattern in profile.patterns:
                 score += len(pattern.findall(sample_text))
            
            # Boost Journal score slightly as false positives are less likely with strict date regex
            if key == 'journal': score *= 1.2
                 
            logger.info(f"  Profile '{key}' score: {score:.1f}")
            
            if score > best_score:
                best_score = score
                best_profile = key
        
        logger.info(f"Auto-selected profile: {best_profile}")
        profile_key = best_profile

    # --- "None" Profile ---
    if profile_key == "none":
        return [Chapter(1, "Full Text", "Full Text", raw_text, len(raw_text.split()))]

    # --- Profile-Based Extraction ---
    selected_profile = PROFILES.get(profile_key, PROFILES["standard"])
    patterns = selected_profile.patterns
    
    chapters = []
    
    # 1. Find all matches from all patterns in the profile
    all_matches = []
    for pattern in patterns:
        all_matches.extend(list(pattern.finditer(raw_text)))
        
    # 2. Sort naturally by position
    all_matches = sorted(all_matches, key=lambda m: m.start())
    
    # 3. Filter overlapping matches (keep the longer/earlier one)
    filtered_matches = []
    if all_matches:
        current_match = all_matches[0]
        for next_match in all_matches[1:]:
            # If next match starts after current ends, it's valid
            if next_match.start() >= current_match.end():
                filtered_matches.append(current_match)
                current_match = next_match
            else:
                # Overlap! Keep the one that starts earlier or is longer
                # If they start at same spot, keep longer
                if next_match.start() == current_match.start():
                     if (next_match.end() - next_match.start()) > (current_match.end() - current_match.start()):
                         current_match = next_match
                # Else: current match started earlier, so keep it (ignore nested/next match)
        filtered_matches.append(current_match)
    
    all_matches = filtered_matches

    if not all_matches:
        # No chapters found, treat the whole text as one chapter
        return [Chapter(1, "Chapter 1", "Chapter 1", raw_text, len(raw_text.split()))]

    for i, match in enumerate(all_matches):
        start_index = match.start()
        end_index = all_matches[i+1].start() if i + 1 < len(all_matches) else len(raw_text)
        
        content = raw_text[start_index:end_index].strip()
        
        # --- Title Extraction (Simplified) ---
        # We just take the whole matched string as the title for now, 
        # cleaned up a bit.
        original_title = match.group(0).strip()
        title = original_title
        
        # Remove extra whitespace/newlines from title
        title = " ".join(title.split())

        # Clean up the content (remove the title line)
        # We verify that the first line of content is indeed our header
        content_lines = content.splitlines()
        # Be loose: if first line contains the title, drop it
        if content_lines and match.group(0).strip() in content_lines[0]:
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


def chapterize(filepath: str, text_content: Optional[str] = None, config: Optional[Dict[str, Any]] = None, profile: str = "auto", toc_strategy: str = "auto", debug: bool = False) -> List[Chapter]:
    """
    Processes a file (pdf, docx, epub, txt) and splits it into chapters.
    If text_content is provided (e.g. from OCR), it is used for PDF/DOCX/TXT
    instead of re-reading the file. EPUBs always use internal structure parsing.
    
    Args:
        toc_strategy: How to handle Table of Contents sections
            - 'auto': Detect and remove TOC if found with high confidence
            - 'remove': Always try to detect and remove TOC
            - 'ignore': Don't attempt TOC detection
    """
    if config is None:
        config = DEFAULT_CONFIG
        
    p_filepath = Path(filepath)
    ext = p_filepath.suffix.lower()
    
    raw_text: Optional[str] = text_content
    initial_chapters: List[Chapter] = []

    try:
        # --- START: MODIFIED Main Processing Block ---
        
        if ext == '.epub':
            # Use our new, robust NCX-based logic.
            # This function returns List[Chapter], bypassing _find_raw_chapters
            initial_chapters = _chapterize_epub(filepath)
            
        elif not raw_text:
            # Only extract if text_content wasn't provided
            if ext == '.docx':
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
            # --- TOC Detection and Removal ---
            toc_removed = False
            if toc_strategy in ['auto', 'remove']:
                toc_start_idx, toc_end_idx, confidence = _detect_toc_section(raw_text)
                
                if toc_start_idx is not None and toc_end_idx is not None:
                    # Decide whether to remove based on strategy and confidence
                    should_remove = (toc_strategy == 'remove') or (toc_strategy == 'auto' and confidence >= 0.7)
                    
                    if should_remove:
                        logger.info(f"Removing TOC section (lines {toc_start_idx}-{toc_end_idx}, confidence {confidence:.2f})")
                        lines = raw_text.split('\n')
                        # Remove lines from toc_start_idx to toc_end_idx inclusive
                        raw_text = '\n'.join(lines[:toc_start_idx] + lines[toc_end_idx+1:])
                        toc_removed = True
                    else:
                        logger.info(f"TOC detected but not removing (confidence {confidence:.2f} below threshold)")

            
            # Process raw text from PDF, DOCX, TXT
            initial_chapters = _find_raw_chapters(raw_text, profile_key=profile)
        elif not raw_text and not initial_chapters:
            # This triggers if EPUB processing failed *or* other file types were empty
            logger.warning(f"No text or chapters could be extracted from {p_filepath.name}.")
            return []
            
        # --- END: MODIFIED Main Processing Block ---

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}", exc_info=True)
        return []

    # --- Handle "No Split" (None) Profile explicitly for all formats ---
    if profile == 'none' and initial_chapters:
        logger.info(f"Profile is 'none'. Merging {len(initial_chapters)} chapters into one.")
        full_content = "\n\n".join([c.content for c in initial_chapters])
        full_word_count = sum(c.word_count for c in initial_chapters)
        # Use metadata from the first chapter/book
        first_chap = initial_chapters[0]
        initial_chapters = [Chapter(
            number=1,
            title="Full Book",
            original_title="Full Book",
            content=full_content,
            word_count=full_word_count,
            part_info=(1, 1)
        )]

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
