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
from collections import Counter
import json

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
# Configuration and Data Loading
# ---------------------------------------------------------
DEFAULT_WORD_LIMIT = 8000
MIN_WORD_COUNT = 50
CONVERTED_EPUB_DIR = "/tmp/converted_epub"
NORMALIZATION_PATH = Path(__file__).parent / "normalization.json"

# Load Bible Books List
BIBLE_BOOK_NAMES = []
if NORMALIZATION_PATH.exists():
    try:
        with open(NORMALIZATION_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            BIBLE_BOOK_NAMES = [name.upper() for name in data.get("bible_books", [])]
            logger.info(f"Loaded {len(BIBLE_BOOK_NAMES)} Bible Book names for structural detection.")
    except Exception as e:
        logger.error(f"Failed to load Bible Book names from normalization.json: {e}")

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

KEEP_RE = re.compile('|'.join(DISALLOWED_SECTION_PATTERNS), re.IGNORECASE)
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
    """
    Uses Calibre's ebook-convert to standardize the EPUB and output OEB HTML files 
    for robust processing.
    """
    if shutil.which("ebook-convert") is None:
        logger.error("Calibre's 'ebook-convert' not found in PATH. EPUB splitting WILL FAIL.")
        return None

    logger.info("Calibre's 'ebook-convert' found. Attempting OEB pre-processing.")

    if Path(output_dir).exists():
        shutil.rmtree(output_dir)
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Use the output directory name without an extension to trigger OEB output mode.
    output_oeb_dir = Path(output_dir) / "oeb_output"
    
    try:
        subprocess.run(
            [
                "ebook-convert", 
                epub_path, 
                str(output_oeb_dir), # Output directory name (no extension) triggers OEB output
                # Options for ensuring clean HTML structure
                "--embed-all-fonts", 
                "--no-default-epub-cover",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info(f"Calibre OEB conversion successful. Files written to {output_oeb_dir}")
        
        # Aggregate the HTML content from the OEB directory.
        html_files = sorted(list(output_oeb_dir.glob("*.html")) + list(output_oeb_dir.glob("*.htm")))
        
        if not html_files:
            logger.error(f"Calibre OEB output directory {output_oeb_dir} contains no HTML files.")
            shutil.rmtree(output_dir, ignore_errors=True)
            return None
        
        # Return the single main file (typically the largest or first) for subsequent parsing.
        main_html_path = max(html_files, key=lambda p: p.stat().st_size)
        
        return main_html_path
    
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
    
    raw = []
    
    if standard_html_path:
        logger.info("Calibre standardization successful. Using H1 structural extraction.")
        try:
            # Read the single, standardized HTML file directly
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
            
            if chapters:
                raw = chapters
            
            
        except Exception as e:
            # If Calibre was successful but parsing failed (e.g., unexpected HTML structure)
            logger.error(f"Fatal error during Calibre-assisted parsing: {e}. Cannot reliably split EPUB. Falling through to full text extraction.")
            
        finally:
             shutil.rmtree(CONVERTED_EPUB_DIR, ignore_errors=True)
    
    # 2. DEFAULT EPUBLIB FALLBACK (Only runs if Calibre failed or Calibre parsing yielded 0 sections)
    
    if not raw:
        logger.warning("Attempting full EPUB text extraction for repetitive heading / regex fallback.")
        try:
            book = epub.read_epub(filepath)
            full_text_parts = []
            for item in book.get_items_of_type(ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                text = clean_whitespace(remove_footnote_markers(soup.get_text(separator='\n')))
                if text:
                    full_text_parts.append(text)
            full_text = '\n\n'.join(full_text_parts)
        except Exception as e:
            logger.error(f"Failed to read EPUB for full text fallback: {e}")
            full_text = "ERROR: Could not read file content."
            return [("Full Document", full_text)]

        # --- STEP 2B: Repetitive Heading Split (New Fallback) ---
        repetitive_headings = _find_repetitive_headings(full_text)
        
        if repetitive_headings:
            logger.info(f"EPUB Fallback: Detected repetitive headings ({len(repetitive_headings)} patterns). Splitting structurally.")
            chapters_from_headings = _split_text_by_headings(full_text, repetitive_headings)
            
            if chapters_from_headings and len(chapters_from_headings) > 1:
                logger.debug(f"EPUB Repetitive Heading split yielded {len(chapters_from_headings)} sections.")
                return prune_disallowed_sections(chapters_from_headings)

        # --- STEP 2C: Final Regex Fallback (The original 'Full Document' fallback) ---
        logger.warning("EPUB Fallback: Repetitive heading split failed. Falling back to simple regex split.")
        
        # Use existing PDF/TXT regex logic on the full text
        pre_toc_words = len(full_text.split())
        full_text = _remove_toc_from_text(full_text)
        post_toc_words = len(full_text.split())
        logger.debug(f"EPUB TOC cleanup: Removed {pre_toc_words - post_toc_words} words.")

        matches = list(CHAPTER_HEADING_RE.finditer(full_text)) + list(NAMED_SECTION_RE.finditer(full_text))
        matches.sort(key=lambda m: m.start())

        if matches:
            for i, m in enumerate(matches):
                start, end = m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
                chunk = full_text[start:end].strip()
                raw.append((normalize_title(m.group(0).strip()), clean_whitespace(remove_footnote_markers(chunk))))
        else:
            raw.append(("Full Document", clean_whitespace(remove_footnote_markers(full_text))))
        
        logger.info(f"EPUB Final Fallback resulted in {len(raw)} sections.")
        return prune_disallowed_sections(raw)
        
    return prune_disallowed_sections(raw) # Return Calibre results if successful


# --- PDF-Specific Heading Detection ---
def _find_repetitive_headings(text: str) -> list:
    """
    Detects likely section dividers based on repeated heading patterns that
    contain a number (numerical, word, or Roman).

    FINAL FIX: Explicitly searches for "DAY N" or "PART N" patterns first,
    then uses the general, numbered structural pattern as a fallback.
    If only unnumbered text is found (like a running header), it returns empty.
    """
    
    # 1. Primary Structural Pattern: Day/Part/Chapter N, prioritizing the numbered part
    # Matches: DAY 1, DAY 1: Creation, PART I, CHAPTER 1
    structural_heading_pattern = re.compile(
        r'^\s*((DAY|PART|CHAPTER|BOOK|SECTION)\b[\s\.\-:]*[0-9IVXLCDM]+(?:\b.*)?)$',
        re.IGNORECASE | re.MULTILINE
    )
    
    # 2. Look for matches that are strongly structured (Day/Part/Chapter N)
    structural_candidates = structural_heading_pattern.findall(text)
    
    if structural_candidates:
        # Tally the structural part (e.g., 'DAY' or 'CHAPTER') from the matched lines
        structural_bases = [re.match(r'^(DAY|PART|CHAPTER|BOOK|SECTION)', c[0].strip(), re.I).group(1).upper() 
                            for c in structural_candidates if re.match(r'^(DAY|PART|CHAPTER|BOOK|SECTION)', c[0].strip(), re.I)]
        
        if structural_bases:
            # Return the single most frequent structural base (e.g., 'DAY') for splitting
            most_common_base = Counter(structural_bases).most_common(1)[0][0]
            logger.debug(f"Identified primary structural base: '{most_common_base}' from matches.")
            return [most_common_base]

    # 3. Fallback to general chapter heading pattern if no Day/Part is found
    # This captures generic 'Chapter 1' patterns not caught above.
    generic_heading_candidates = CHAPTER_HEADING_RE.findall(text) + NAMED_SECTION_RE.findall(text)
    
    if generic_heading_candidates:
        # If the generic or named-section headings are repetitive, use the base structural regex
        bases = [re.match(r'^(chapter|chap|book|part|preface|introduction)', c[0].strip(), re.I).group(1).upper()
                 for c in generic_heading_candidates if re.match(r'^(chapter|chap|book|part|preface|introduction)', c[0].strip(), re.I)]
        
        if bases and Counter(bases).most_common(1)[0][1] > 1:
            most_common_base = Counter(bases).most_common(1)[0][0]
            logger.debug(f"Identified secondary structural base: '{most_common_base}' from generic/named matches.")
            return [most_common_base]

    logger.debug("No reliable, repetitive structural headings found (Day/Part/Chapter N).")
    return []

def _split_text_by_headings(text: str, repetitive_headings: list) -> List[Tuple[str, str]]:
    """
    Splits the text using the detected repetitive heading pattern, 
    ensuring the heading line is included in the content block.
    """
    if not repetitive_headings:
        # If no repetitive headings, fall back to default regex splitting on the whole document
        logger.debug("Repetitive heading splitting skipped. Falling through to full regex match.")
        matches = list(CHAPTER_HEADING_RE.finditer(text)) + list(NAMED_SECTION_RE.finditer(text))
        matches.sort(key=lambda m: m.start())
        
        sections = []
        if matches:
            for i, m in enumerate(matches):
                start, end = m.start(), matches[i + 1].start() if i + 1 < len(matches) else len(text)
                chunk = text[start:end].strip()
                sections.append((normalize_title(m.group(0).strip()), clean_whitespace(chunk)))
        
        return sections if sections else [("Full Document", text)]


    # Use the single, highest-confidence base pattern found
    main_pattern_base = repetitive_headings[0] 
    
    # Pattern to match the base keyword followed by a number/word, 
    # ensuring it's a structural break (start of line)
    number_pattern = r'(\s+([0-9IVXLCDM]+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY).*?)?'
    
    # Final splitting pattern: ensures start of line (\n|^)\s* and captures the full heading line
    # NOTE: The outer regex is designed to capture the entire line that starts with the base.
    pattern = re.compile(
        # Group 2 is the actual title line we want (e.g., 'DAY 1' or 'DAY 1: Creation')
        rf"(\n|^)\s*({re.escape(main_pattern_base)}{number_pattern})\n", 
        re.IGNORECASE
    )

    # Use finditer to find all split points and extract content between them
    matches = list(pattern.finditer(text))
    sections = []
    
    # Handle content before the first match (Front Matter/Intro)
    first_match_start = matches[0].start() if matches else len(text)
    front_matter = text[:first_match_start].strip()
    
    # Add Front Matter/Intro before the first structural match, only if it contains content
    if front_matter:
         sections.append(("Front_Matter", clean_whitespace(front_matter)))

    for i, m in enumerate(matches):
        # The content of the heading line is in group 2
        title_line = m.group(2).strip()
        
        # Find the start of the next section's content
        end_of_current = m.end()
        start_of_next = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        
        content = text[end_of_current:start_of_next].strip()

        if title_line:
            # Clean title: remove special characters that break filenames
            current_title = normalize_title(re.sub(r'[\\/*?:"<>|]', '', title_line)[:60])
            
            # --- DEBUGGING CONFIRMATION ---
            logger.debug(f"DEBUG: Splitting on header: '{title_line}' (Title: '{current_title}')")
            
            # CRITICAL FIX: Ensure the title line is explicitly included in the content block.
            chunk_content = title_line + '\n\n' + content
            
            sections.append((current_title, clean_whitespace(chunk_content)))
            
    # Remove all sections that matched a DISALLOWED_SECTION_PATTERNS title (e.g., 'Table of Contents')
    return prune_disallowed_sections(sections)


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
    
    # --- STEP 1: Aggressively clean up text before heading detection ---
    text = clean_whitespace(remove_footnote_markers(text)) # Clean footnotes and normalize whitespace
    text = re.sub(r'\n+\s*\d+\s*\n+', '\n\n', text) # Remove page numbers/junk lines
    
    # --- STEP 2: Use Repetitive Heading Detection ---
    repetitive_headings = _find_repetitive_headings(text)
    
    if repetitive_headings:
        logger.info(f"PDF: Detected repetitive headings ({len(repetitive_headings)} patterns). Splitting structurally.")
        chapters = _split_text_by_headings(text, repetitive_headings)
        
        # If structural split worked, return it after cleanup
        if chapters and len(chapters) > 1:
            logger.debug(f"PDF Structural split yielded {len(chapters)} sections.")
            return prune_disallowed_sections(chapters)

    # --- STEP 3 (Fallback): Use Regex Detection ---
    logger.info("PDF: Falling back to default structural regex splitting.")
    
    # Run TOC removal only if structural split failed/wasn't used
    pre_toc_words = len(text.split())
    text = _remove_toc_from_text(text)
    post_toc_words = len(text.split())
    logger.debug(f"PDF TOC cleanup: Removed {pre_toc_words - post_toc_words} words.")

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
    
    # --- STEP 1: Use Repetitive Heading Detection ---
    repetitive_headings = _find_repetitive_headings(text)
    
    if repetitive_headings:
        logger.info(f"TXT: Detected repetitive headings ({len(repetitive_headings)} patterns). Splitting structurally.")
        chapters = _split_text_by_headings(text, repetitive_headings)
        
        # If structural split worked, return it after cleanup
        if chapters and len(chapters) > 1:
            logger.debug(f"TXT Structural split yielded {len(chapters)} sections.")
            return prune_disallowed_sections(chapters)

    # --- STEP 2 (Fallback): Default Structural Regex ---
    logger.info("TXT: Falling back to default structural regex splitting.")
    
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
