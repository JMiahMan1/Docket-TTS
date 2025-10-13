"""
chapterizer.py

Unified multi-format book chapter extractor (EPUB primary, PDF/DOCX/TXT fallback).
Backwards-compatible with old `chapterizer.chapterize()` interface.
"""

from __future__ import annotations
import re
import logging
from typing import List, Dict, Tuple, Optional
from pathlib import Path

# EPUB
from ebooklib import epub
from bs4 import BeautifulSoup

# DOCX
import docx

# PDF
import fitz  # PyMuPDF

# ---------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------
logger = logging.getLogger("chapterizer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
DEFAULT_WORD_LIMIT = 8000
MIN_WORD_COUNT = 50 # Only applies to generic, UNNAMED sections

# --- FINAL KEEP KEYWORDS (Restored 'part' to match major divisions) ---
KEEP_TITLE_KEYWORDS = [
    # Front Matter
    r'\btitle\s*page\b',
    r'\bpreface\b',
    r'\bforeword\b',
    r'\bintroduction\b',
    r'\bprologue\b',
    r'\bepilogue\b', 
    # Major Content Divisions
    r'\bchapter\b',
    r'\bpart\b', 
    r'\bbook\b', 
]
# ------------------------------------------------------------------------

DISALLOWED_SECTION_PATTERNS = [
    r'\btable\s+of\s+contents\b',
    r'\bcontents\b',
    r'\bappendix\b',
    r'\breferences\b',
    r'\bbibliography\b',
    r'\bindex\b',
    r'\bcopyright\b',
    r'\bpermissions\b',
    r'\bglossary\b',
    r'\backnowledg',
    r'\bcolophon\b',
    r'\bdedication\b', 
    r'\babout\s+the\s+(author|publisher)\b', 
]

KEEP_RE = re.compile('|'.join(DISALLOWED_SECTION_PATTERNS), re.IGNORECASE)
DISALLOWED_RE = re.compile('|'.join(DISALLOWED_SECTION_PATTERNS), re.IGNORECASE)

# --- REVISED CHAPTER HEADING REGEX: Includes Chapter/Book/Part + Numbering ---
CHAPTER_HEADING_RE = re.compile(
    r'^(?P<title>(chapter|chap|book|part)\b[\s\.\-:]*[0-9IVXLCDMivxlcdm]+(?:\b.*)?|' 
    r'(?:^|\n)\bchapter\s+[ivxlcdm0-9]+\b.*)',
    re.IGNORECASE | re.MULTILINE
)

NAMED_SECTION_RE = re.compile(
    r'^\s*(?P<title>title\s*page|preface|foreword|introduction|prologue|epilogue)\b[:\s\-]*?(?P<rest>.*)$',
    re.IGNORECASE | re.MULTILINE
)

# ---------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------
def clean_whitespace(text: str) -> str:
    if not text:
        return ''
    t = text.replace('\r\n', '\n').replace('\r', '\n')
    t = re.sub(r'\t+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = '\n'.join(line.rstrip() for line in t.splitlines())
    t = re.sub(r' {2,}', ' ', t)
    return t.strip()


def remove_footnote_markers(text: str) -> str:
    t = re.sub(r'\[\d+\]', '', text)
    t = re.sub(r'\s*\(\d+\)', '', t)
    t = re.sub(r'(?<=\D)[\u00B9\u00B2\u00B3\u2070-\u207F]+', '', t)
    t = re.sub(r'\n\s*\d+\s*\n', '\n\n', t)
    return t


def normalize_title(title: str) -> str:
    if not title:
        return "Untitled"
    t = re.sub(r'\s+', ' ', title.strip())
    t = re.sub(r'^[\-\:\.\s]+|[\-\:\.\s]+$', '', t)
    return t


def prune_disallowed_sections(chapters: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    # Note: Using DISALLOWED_RE to prune undesirable sections
    return [(t, c) for t, c in chapters if not DISALLOWED_RE.search(t)]


# --- FUNCTION FOR AGGRESSIVE TOC REMOVAL ---
def _remove_toc_from_text(text: str) -> str:
    """Removes blocks of text strongly resembling a Table of Contents."""
    TOC_LINE_RE = re.compile(r'^\s*[\w\s\.\-:\(\)]+\s+[\.\-\s]+\s*\d{1,4}\s*$', re.MULTILINE)
    
    lines = text.split('\n')
    clean_lines = []
    in_toc_block = False
    toc_lines_count = 0
    
    for line in lines:
        # We check for DISALLOWED keywords in the line
        if DISALLOWED_RE.search(line):
            logger.debug(f"TOC Removal: Entering potential TOC block based on title: {line.strip()}")
            in_toc_block = True
            toc_lines_count = 0
            continue 
            
        if in_toc_block:
            # Look for lines typical of a TOC entry (dots/numbers/etc.)
            if TOC_LINE_RE.search(line) or (line.strip() and not CHAPTER_HEADING_RE.search(line)):
                toc_lines_count += 1
                logger.debug(f"TOC Removal: Discarding TOC line: {line.strip()}")
                continue
            else:
                if CHAPTER_HEADING_RE.search(line) or not line.strip():
                     if toc_lines_count > 2:
                        in_toc_block = False
                        logger.debug(f"TOC Removal: Exiting TOC block. Retaining subsequent line.")
                
        clean_lines.append(line)
        
    cleaned_text = '\n'.join(clean_lines)
    
    return clean_whitespace(cleaned_text)


def split_into_chunks(chapters: List[Dict], word_limit: int = DEFAULT_WORD_LIMIT) -> List[Dict]:
    logger.debug(f"Starting chunk splitting: {len(chapters)} logical sections found.")
    out = []
    global_chunk_id = 1 # Global ID to maintain task sequence across the entire book
    
    for i, ch in enumerate(chapters, start=1):
        # We now use the original title as the base name for parts
        title = ch.get('title', f"Section {i}")
        text = clean_whitespace(ch.get('text', ''))
        
        words = text.split()
        
        # Determine if this section is a known, required structural element
        # Check against NAMED_SECTION_RE (Preface, Intro) and CHAPTER_HEADING_RE (Chapter X, Part I)
        is_key_structural_element = bool(CHAPTER_HEADING_RE.match(title) or NAMED_SECTION_RE.match(title))
        
        # --- FIX: Only discard short sections if they are NOT structural elements ---
        if len(words) < MIN_WORD_COUNT and not is_key_structural_element:
            logger.warning(f"Discarding short, non-structural section '{title}' with only {len(words)} words (Min: {MIN_WORD_COUNT}).")
            continue
        # -------------------------------------------------------------------------
        
        if not text:
            continue

        # If the logical chapter is small enough, keep it as a single chunk
        if len(words) <= word_limit:
            out.append({'title': title, 'chunk_id': global_chunk_id, 'text': text})
            global_chunk_id += 1
            logger.debug(f"  -> Chunk {global_chunk_id-1}: '{title}' ({len(words)} words) - Single part.")
            continue

        # --- LOGIC FOR SPLITTING A LARGE CHAPTER INTO MULTIPLE CHUNKS ---
        paras = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        buf, count = [], 0
        local_chunk_id = 1 # Local counter for parts within this logical chapter
        
        logger.debug(f"  -> Logical Section '{title}' ({len(words)} words) requires splitting.")
        
        for p in paras:
            w = len(p.split())
            if count + w > word_limit and buf:
                
                # Assign the chunk title with the part number
                chunk_title = f"{title} (Part {local_chunk_id})"
                
                out.append({
                    'title': chunk_title, 
                    'chunk_id': global_chunk_id, # Global ID for Celery sequencing
                    'text': '\n\n'.join(buf).strip()
                })
                
                logger.debug(f"  -> Chunk {global_chunk_id}: '{chunk_title}' ({count} words)")
                
                global_chunk_id += 1
                local_chunk_id += 1 # Increment local counter for the next part
                buf, count = [p], w
            else:
                buf.append(p)
                count += w
                
        if buf:
            # Handle the last buffer
            if local_chunk_id > 1:
                chunk_title = f"{title} (Part {local_chunk_id})"
            else:
                chunk_title = title
            
            out.append({
                'title': chunk_title, 
                'chunk_id': global_chunk_id, 
                'text': '\n\n'.join(buf).strip()
            })
            logger.debug(f"  -> Chunk {global_chunk_id}: '{chunk_title}' ({count} words)")
            global_chunk_id += 1
            
    logger.debug(f"Chunk splitting finished. Total final chunks: {len(out)}.")
    return out

# ---------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------
def _extract_epub(filepath: str) -> List[Tuple[str, str]]:
    from ebooklib import ITEM_DOCUMENT
    logger.debug(f"Extracting EPUB: {filepath}")
    try:
        book = epub.read_epub(filepath)
    except Exception:
        logger.exception("Failed to read EPUB.")
        return []

    chapters = []
    full_text_parts = []
    
    # Pass 1: Extract text and potential section titles from EPUB items
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), 'html.parser')
        # Decompose elements like navigation, footnotes, scripts, and styles
        for s in soup.select('nav, .nav, .toc, .footnote, script, style'):
            s.decompose()
        
        # Try to find a meaningful title from h1/h2 tags, or fall back to item name
        title_el = soup.find(['h1', 'h2'])
        title = title_el.get_text(strip=True) if title_el else item.get_name()
        
        # Get all text from the item
        text = clean_whitespace(remove_footnote_markers(soup.get_text(separator='\n')))
        
        if text:
            normalized_title = normalize_title(title)
            chapters.append((normalized_title, text))
            # Keep track of all text content for combined processing
            full_text_parts.append(text) 
    
    full_text = '\n\n'.join(full_text_parts)
    logger.debug(f"EPUB raw file-based sections: {len(chapters)}. Total text words: {len(full_text.split())}")

    # --- AGGRESSIVE TOC REMOVAL ---
    pre_toc_words = len(full_text.split())
    full_text = _remove_toc_from_text(full_text)
    post_toc_words = len(full_text.split())
    logger.debug(f"EPUB TOC cleanup: Removed {pre_toc_words - post_toc_words} words.")
    # ------------------------------

    # Pass 2: Try to find chapters using regex on the combined text
    matches = list(CHAPTER_HEADING_RE.finditer(full_text)) + list(NAMED_SECTION_RE.finditer(full_text))
    matches.sort(key=lambda m: m.start())

    regex_chapters = []
    if matches:
        logger.info(f"EPUB: Found {len(matches)} chapter-like headers using full-text regex. Prioritizing regex split.")
        for i, m in enumerate(matches):
            start, end = m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
            chunk = full_text[start:end].strip()
            # The title is the full matched header
            regex_chapters.append((normalize_title(m.group(0).strip()), clean_whitespace(remove_footnote_markers(chunk))))
        
        # Return the regex-split chapters if any were found
        return prune_disallowed_sections(regex_chapters)

    # Fallback 1: No strong regex matches found on combined text.
    logger.warning("EPUB: No strong chapter headers found via regex. Falling back to strict per-file processing.")
    
    # Prune disallowed sections
    chapters = prune_disallowed_sections(chapters)
    
    # --- AGGRESSIVE FALLBACK FILTER ---
    # Only keep sections whose titles explicitly match a core structural keyword.
    filtered = [
        (t, c)
        for t, c in chapters
        if KEEP_RE.search(t) or NAMED_SECTION_RE.search(t)
        # We rely on the word count filter in split_into_chunks to remove short junk
    ]
    logger.info(f"EPUB Fallback: {len(filtered)} sections kept after strict title filtering.")
    
    # Fallback 2: If the strict filter yielded nothing, return the whole book as one chunk
    if not filtered and chapters:
        logger.warning("EPUB Fallback: Strict filter yielded no content. Returning content as a single 'Full Document' chunk.")
        # Combine all filtered text (after pruning disallowed sections) back into one entry
        combined_text = '\n\n'.join(c for t, c in chapters)
        return [("Full Document", combined_text)]
        
    return filtered # Return the strictly filtered list

def _extract_docx(filepath: str) -> List[Tuple[str, str]]:
    logger.debug(f"Extracting DOCX: {filepath}")
    try:
        doc = docx.Document(filepath)
    except Exception:
        logger.exception("Failed to read DOCX.")
        return []

    chapters, buf, current_title = [], [], None

    def push():
        nonlocal buf, current_title
        if buf:
            chapters.append((normalize_title(current_title or "Untitled"), clean_whitespace('\n\n'.join(buf))))
            buf = []

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = getattr(p.style, 'name', '').lower()
        # Use explicit heading style or explicit title matches
        if 'heading 1' in style or re.match(r'^(chapter|preface|introduction|prologue|part)', text, re.I):
            push()
            current_title = text
        else:
            buf.append(text)
    push()
    result = prune_disallowed_sections(chapters) or [("Full Document", "\n\n".join(p.text for p in doc.paragraphs))]
    logger.info(f"DOCX extraction resulted in {len(result)} sections.")
    return result


def _extract_pdf(filepath: str) -> List[Tuple[str, str]]:
    logger.debug(f"Extracting PDF: {filepath}")
    try:
        doc = fitz.open(filepath)
    except Exception:
        logger.exception("Failed to open PDF.")
        return []

    text = "\n\n".join(clean_whitespace(page.get_text("text")) for page in doc)
    text = re.sub(r'\n+\s*\d+\s*\n+', '\n\n', text)
    
    # --- AGGRESSIVE TOC REMOVAL ---
    pre_toc_words = len(text.split())
    text = _remove_toc_from_text(text)
    post_toc_words = len(text.split())
    logger.debug(f"PDF TOC cleanup: Removed {pre_toc_words - post_toc_words} words.")
    # ------------------------------

    matches = list(CHAPTER_HEADING_RE.finditer(text)) + list(NAMED_SECTION_RE.finditer(text))
    matches.sort(key=lambda m: m.start())

    chapters = []
    if matches:
        for i, m in enumerate(matches):
            start, end = m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()
            chapters.append((normalize_title(m.group(0).strip()), clean_whitespace(remove_footnote_markers(chunk))))
    else:
        chapters.append(("Full Document", clean_whitespace(remove_footnote_markers(text))))
    
    result = prune_disallowed_sections(chapters)
    logger.info(f"PDF extraction resulted in {len(result)} sections.")
    return result


def _extract_txt(filepath: str) -> List[Tuple[str, str]]:
    logger.debug(f"Extracting TXT: {filepath}")
    try:
        text = Path(filepath).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        text = Path(filepath).read_text(encoding='latin-1')

    text = clean_whitespace(text)
    
    # --- AGGRESSIVE TOC REMOVAL ---
    pre_toc_words = len(text.split())
    text = _remove_toc_from_text(text)
    post_toc_words = len(text.split())
    logger.debug(f"TXT TOC cleanup: Removed {pre_toc_words - post_toc_words} words.")
    # ------------------------------
    
    matches = list(CHAPTER_HEADING_RE.finditer(text)) + list(NAMED_SECTION_RE.finditer(text))
    matches.sort(key=lambda m: m.start())

    chapters = []
    if matches:
        for i, m in enumerate(matches):
            start, end = m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()
            chapters.append((normalize_title(m.group(0).strip()), chunk))
    else:
        chapters.append(("Full Document", text))
    
    result = prune_disallowed_sections(chapters)
    logger.info(f"TXT extraction resulted in {len(result)} sections.")
    return result

# ---------------------------------------------------------
# Unified main extraction logic
# ---------------------------------------------------------
def extract_book_sections(file_path: str, word_limit: int = DEFAULT_WORD_LIMIT, verbose: bool = False) -> List[Dict]:
    p = Path(file_path)
    ext = p.suffix.lower()
    raw = []

    if ext == '.epub':
        raw = _extract_epub(str(p))
    elif ext == '.docx':
        raw = _extract_docx(str(p))
    elif ext == '.pdf':
        raw = _extract_pdf(str(p))
    elif ext == '.txt':
        raw = _extract_txt(str(p))
    else:
        logger.warning(f"Unknown extension {ext}, treating as plain text.")
        raw = _extract_txt(str(p))

    structured = [
        {'title': normalize_title(t), 'text': clean_whitespace(remove_footnote_markers(c))}
        for t, c in raw if c.strip()
    ]
    
    # Log the result before final chunking
    logger.info(f"Preprocessing complete. Starting chunking on {len(structured)} logical sections.")

    chunks = split_into_chunks(structured, word_limit)

    if verbose:
        logger.debug(f"Detected {len(structured)} sections before chunking:")
        for t, c in raw:
            logger.debug(f"  - {normalize_title(t)} ({len(c.split())} words)")
        logger.info(f"Generated {len(chunks)} total chunks.")
    return chunks

# ---------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------
def chapterize(filepath: str, text_content: Optional[str] = None, debug_level: Optional[str] = None):
    """
    API entry point matching the original signature.
    debug_level may be: 'quiet', 'info', or 'verbose'.
    """
    level = (debug_level or "info").lower()
    if level == "quiet":
        logger.setLevel(logging.WARNING)
    elif level == "verbose":
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    logger.info(f"[chapterizer] Processing file: {filepath}")
    try:
        chunks = extract_book_sections(filepath, word_limit=DEFAULT_WORD_LIMIT, verbose=(level == "verbose"))
        logger.info(f"[chapterizer] Extraction complete: {len(chunks)} chunks generated.")
        return chunks
    except Exception as e:
        logger.exception(f"[chapterizer] Extraction failed: {e}")
        return []

# ---------------------------------------------------------
# CLI for manual use
# ---------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract structured book sections for LLM processing.")
    parser.add_argument("file", help="Path to input file (.epub, .pdf, .docx, .txt)")
    parser.add_argument("--debug", choices=["quiet", "info", "verbose"], default="info", help="Debug level")
    parser.add_argument("--word-limit", type=int, default=DEFAULT_WORD_LIMIT)
    args = parser.parse_args()

    logger.setLevel(logging.DEBUG if args.debug == "verbose" else logging.INFO)
    chunks = chapterize(args.file, debug_level=args.debug)
    print(f"\nExtracted {len(chunks)} chunks\n")
    for c in chunks[:3]:  # show first 3 chunks for preview
        print(f"[{c['chunk_id']}] {c['title']} — {len(c['text'].split())} words\n{c['text'][:500]}...\n")
