"""
chapterizer.py

Unified multi-format book chapter extractor (EPUB primary, PDF/DOCX/TXT fallback).
ENFORCES CALIBRE PRE-PROCESSING for EPUBs, as required for reliable splitting.
"""

from __future__ import annotations
import re
import logging
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import warnings
import shutil
import subprocess
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub, ITEM_DOCUMENT
from os import environ

# DOCX
import docx

# PDF
import fitz # PyMuPDF

# Suppress EPUB parsing warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

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
MIN_WORD_COUNT = 50
CONVERTED_EPUB_DIR = "/tmp/converted_epub"

# --- FINAL KEEP KEYWORDS (Structural sections only) ---
KEEP_TITLE_KEYWORDS = [
    r'\btitle\s*page\b', r'\bpreface\b', r'\bforeword\b', r'\bintroduction\b',
    r'\bprologue\b', r'\bepilogue\b', r'\bchapter\b', r'\bpart\b', r'\bbook\b',
]

DISALLOWED_SECTION_PATTERNS = [
    r'\btable\s+of\s+contents\b', r'\bcontents\b', r'\bappendix\b', r'\breferences\b',
    r'\bbibliography\b', r'\bindex\b', r'\bcopyright\b', r'\bpermissions\b',
    r'\bglossary\b', r'\backnowledg', r'\bcolophon\b', r'\bdedication\b',
    r'\babout\s+the\s+(author|publisher)\b',
]

KEEP_RE = re.compile('|'.join(KEEP_TITLE_KEYWORDS), re.IGNORECASE)
DISALLOWED_RE = re.compile('|'.join(DISALLOWED_SECTION_PATTERNS), re.IGNORECASE)

# --- REVISED CHAPTER HEADING REGEX (For confidence check and other file types) ---
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
# Calibre Integration Helper
# ---------------------------------------------------------
def _convert_epub_to_standard_html(epub_path: str, output_dir: str) -> Optional[Path]:
    """Uses Calibre's ebook-convert to standardize the EPUB to a single HTML file path."""
    if shutil.which("ebook-convert") is None:
        logger.error("Calibre's 'ebook-convert' not found in PATH. EPUB splitting WILL FAIL.")
        return None

    logger.info("Calibre's 'ebook-convert' found. Attempting pre-processing.")

    if Path(output_dir).exists():
        shutil.rmtree(output_dir)
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    standard_epub_path = Path(output_dir) / "standard.epub"
    full_content_path = Path(output_dir) / "full_standardized_content.html"
    
    try:
        subprocess.run(
            ["ebook-convert", epub_path, str(standard_epub_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info("Calibre standardization successful (EPUB -> EPUB).")
        
        book = epub.read_epub(str(standard_epub_path))
        
        full_html_parts = []
        # from ebooklib import ITEM_DOCUMENT # Already in module scope
        
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            full_html_parts.append(item.get_body_content().decode('utf-8', errors='ignore'))
        
        full_content_path.write_text('\n'.join(full_html_parts), encoding='utf-8')
        logger.info(f"Standardized HTML content aggregated to {full_content_path}")
        
        return full_content_path
    
    except subprocess.CalledProcessError as e:
        logger.error(f"Calibre conversion failed: {e.stderr.decode('utf-8', errors='ignore')}")
        shutil.rmtree(output_dir, ignore_errors=True)
        return None
    except Exception as e:
        logger.error(f"Error during Calibre processing: {e}")
        shutil.rmtree(output_dir, ignore_errors=True)
        return None

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
    t = re.sub(r'\s*\(\d+\)', '', text)
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
    return [(t, c) for t, c in chapters if not DISALLOWED_RE.search(t)]


# --- FUNCTION FOR AGGRESSIVE TOC REMOVAL (For other file types) ---
def _remove_toc_from_text(text: str) -> str:
    TOC_LINE_RE = re.compile(r'^\s*[\w\s\.\-:\(\)]+\s+[\.\-\s]+\s*\d{1,4}\s*$', re.MULTILINE)
    
    lines = text.split('\n')
    clean_lines = []
    in_toc_block = False
    toc_lines_count = 0
    
    for line in lines:
        if DISALLOWED_RE.search(line):
            logger.debug(f"TOC Removal: Entering potential TOC block based on title: {line.strip()}")
            in_toc_block = True
            toc_lines_count = 0
            continue 
            
        if in_toc_block:
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
        title = ch.get('title', f"Section {i}")
        text = clean_whitespace(ch.get('text', ''))
        
        words = text.split()
        
        is_key_structural_element = bool(CHAPTER_HEADING_RE.match(title) or NAMED_SECTION_RE.match(title))
        
        if len(words) < MIN_WORD_COUNT and not is_key_structural_element:
            logger.warning(f"Discarding short, non-structural section '{title}' with only {len(words)} words (Min: {MIN_WORD_COUNT}).")
            continue
        
        if not text:
            continue

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
                
                chunk_title = f"{title} (Part {local_chunk_id})"
                
                out.append({
                    'title': chunk_title,
                    'chunk_id': global_chunk_id,
                    'text': '\n\n'.join(buf).strip()
                })
                
                logger.debug(f"  -> Chunk {global_chunk_id}: '{chunk_title}' ({count} words)")
                
                global_chunk_id += 1
                local_chunk_id += 1
                buf, count = [p], w
            else:
                buf.append(p)
                count += w
                
        if buf:
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
    logger.debug(f"Extracting EPUB: {filepath}")
    
    # 1. ATTEMPT CALIBRE STANDARDIZATION (Required Step)
    standard_html_path = _convert_epub_to_standard_html(filepath, CONVERTED_EPUB_DIR)
    
    if standard_html_path:
        logger.info("Calibre standardization successful. Using H1 structural extraction.")
        try:
            html_content = standard_html_path.read_text(encoding='utf-8')
            soup = BeautifulSoup(html_content, 'lxml')
            
            # --- FIX: Surgical Cleanup Logic START ---
            
            # 1. Elements to always decompose (nav, toc, script, style, calibre junk)
            always_decompose_selectors = 'nav, .nav, .toc, .footnote, script, style, body > .calibre_toc'
            for s in soup.select(always_decompose_selectors):
                s.decompose()

            # 2. Conditionally decompose header/footer: only remove if they don't contain a title.
            # Get a list of all potential headings (h1, h2) before modifying the DOM.
            potential_headings = soup.find_all(['h1', 'h2'])
            
            for selector in ['header', 'footer']:
                for el in soup.select(selector):
                    # Check if this element contains one of the potential headings
                    contains_title = any(h.parent == el or h in el.descendants for h in potential_headings)
                    
                    # Only decompose if it does NOT contain a heading.
                    if not contains_title:
                        el.decompose()
                    else:
                        logger.debug(f"EPUB Cleanup: Preserving structural element <{el.name}> because it contains a heading.")
            
            # --- FIX: Surgical Cleanup Logic END ---
            
            chapters = []
            current_content_blocks = []
            current_title = "Front_Matter"
            
            def finalize_section():
                nonlocal current_title, current_content_blocks
                if current_content_blocks:
                    text = clean_whitespace("\n\n".join(current_content_blocks))
                    if text:
                         chapters.append((normalize_title(current_title), text))
                    current_content_blocks = []

            # Iterate over structural tags in the standardized output
            content_div = soup.find('body') or soup
            
            # REVISED: Use H1/H2 to split, and ensure the H1/H2 text IS included in the new section's content block.
            for element in content_div.find_all(['h1', 'h2', 'h3', 'p', 'div']):
                text = clean_whitespace(element.get_text())
                if not text:
                    continue
                
                # Use H1/H2 for splitting after cleanup
                if element.name in ['h1', 'h2']:
                    is_structural_heading = not DISALLOWED_RE.search(text)

                    if is_structural_heading:
                        finalize_section()
                        current_title = text
                        logger.debug(f"Calibre Extraction: NEW H1/H2 SECTION START: '{current_title}'")
                        # CRITICAL FIX: The heading text must be the FIRST line of the content block
                        current_content_blocks.append(text) 
                        continue
                
                # Include all other text content (H3, P, DIV) in the current block
                current_content_blocks.append(text)
            
            finalize_section() # Capture the last section
            
            logger.info(f"Calibre-assisted extraction resulted in {len(chapters)} sections.")
            
            if not chapters:
                 logger.warning("Calibre extraction returned 0 structural sections. Returning fallback text method.")
                 raise ValueError("Calibre parsing failed to find structural chapters.")

            return prune_disallowed_sections(chapters)
            
        except Exception as e:
            # If Calibre was successful but parsing failed (e.g., unexpected HTML structure)
            logger.error(f"Fatal error during Calibre-assisted parsing: {e}. Cannot reliably split EPUB.")
            
        finally:
             shutil.rmtree(CONVERTED_EPUB_DIR, ignore_errors=True)
    
    
    # 2. DEFAULT EPUBLIB FALLBACK (Only executes if Calibre failed or was not available)
    
    logger.error("EPUB structural splitting FAILED. Processing as single file. Using fallback content.")
    try:
        # from ebooklib import ITEM_DOCUMENT # Already in module scope
        book = epub.read_epub(filepath)
        full_text_parts = []
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_body_content(), 'html.parser')
            text = clean_whitespace(remove_footnote_markers(soup.get_text(separator='\n')))
            if text:
                full_text_parts.append(text)
        full_text = '\n\n'.join(full_text_parts)
    except Exception as e:
        logger.error(f"Failed to read EPUB for single file fallback: {e}")
        full_text = "ERROR: Could not read file content."
    
    return [("Full Document", full_text)]


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
        logger.exception("Failed to read PDF.")
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
    
    # Convert debug level string to logging constant
    level_map = {
        'off': logging.WARNING,
        'info': logging.INFO,
        'debug': logging.DEBUG,
        'trace': logging.DEBUG
    }
    log_level = level_map.get(level, logging.INFO)
    logger.setLevel(log_level)

    # Use 'verbose' flag for internal functions that need extra detail
    verbose = (level == "debug" or level == "trace")

    logger.info(f"[chapterizer] Processing file: {filepath} (Log Level: {logging.getLevelName(log_level)})")
    try:
        chunks = extract_book_sections(filepath, word_limit=DEFAULT_WORD_LIMIT, verbose=verbose)
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

    # NOTE: CLI execution uses 'verbose' for max detail by default here
    logger.setLevel(logging.DEBUG)
    chunks = extract_book_sections(args.file, word_limit=args.word_limit, verbose=True)
    print(f"\nExtracted {len(chunks)} chunks\n")
    for c in chunks[:3]: # show first 3 chunks for preview
        print(f"[{c['chunk_id']}] {c['title']} — {len(c['text'].split())} words\n{c['text'][:500]}...\n")
