"""
chapterizer.py

Unified multi-format book chapter extractor (EPUB primary, PDF/DOCX/TXT fallback).
Backwards-compatible with old `chapterizer.chapterize()` interface.

Dependencies:
    pip install ebooklib beautifulsoup4 python-docx PyMuPDF
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

KEEP_TITLE_KEYWORDS = [
    r'\btitle\s*page\b',
    r'\bpreface\b',
    r'\bforeword\b',
    r'\bintroduction\b',
    r'\bprologue\b',
    r'\bchapter\b',
    r'\bpart\b',
]

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
]

KEEP_RE = re.compile('|'.join(KEEP_TITLE_KEYWORDS), re.IGNORECASE)
DISALLOWED_RE = re.compile('|'.join(DISALLOWED_SECTION_PATTERNS), re.IGNORECASE)

CHAPTER_HEADING_RE = re.compile(
    r'^(?P<title>(chapter|chap|book|part)\b[\s\.\-:]*[0-9IVXLCDMivxlcdm]+(?:\b.*)?|'
    r'(?:^|\n)\bchapter\s+[ivxlcdm0-9]+\b.*|'
    r'^\s*(?P<num>\d{1,3})\.\s+(?P<rest>.+))',
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
    return [(t, c) for t, c in chapters if not DISALLOWED_RE.search(t)]


def split_into_chunks(chapters: List[Dict], word_limit: int = DEFAULT_WORD_LIMIT) -> List[Dict]:
    out = []
    chunk_id = 1
    for i, ch in enumerate(chapters, start=1):
        title = ch.get('title', f"Section {i}")
        text = clean_whitespace(ch.get('text', ''))
        if not text:
            continue

        words = text.split()
        if len(words) <= word_limit:
            out.append({'title': title, 'chunk_id': chunk_id, 'text': text})
            chunk_id += 1
            continue

        paras = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
        buf, count = [], 0
        for p in paras:
            w = len(p.split())
            if count + w > word_limit and buf:
                out.append({'title': title, 'chunk_id': chunk_id, 'text': '\n\n'.join(buf).strip()})
                chunk_id += 1
                buf, count = [p], w
            else:
                buf.append(p)
                count += w
        if buf:
            out.append({'title': title, 'chunk_id': chunk_id, 'text': '\n\n'.join(buf).strip()})
            chunk_id += 1
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
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), 'html.parser')
        for s in soup.select('nav, .nav, .toc, .footnote, script, style'):
            s.decompose()
        title_el = soup.find(['h1', 'h2'])
        title = title_el.get_text(strip=True) if title_el else item.get_name()
        text = clean_whitespace(remove_footnote_markers(soup.get_text(separator='\n')))
        if text:
            chapters.append((normalize_title(title), text))

    chapters = prune_disallowed_sections(chapters)
    filtered = [
        (t, c)
        for t, c in chapters
        if KEEP_RE.search(t) or CHAPTER_HEADING_RE.search(t) or NAMED_SECTION_RE.search(t)
    ]
    return filtered or chapters


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
        if 'heading 1' in style or re.match(r'^(chapter|preface|introduction|prologue)', text, re.I):
            push()
            current_title = text
        else:
            buf.append(text)
    push()
    return prune_disallowed_sections(chapters) or [("Full Document", "\n\n".join(p.text for p in doc.paragraphs))]


def _extract_pdf(filepath: str) -> List[Tuple[str, str]]:
    logger.debug(f"Extracting PDF: {filepath}")
    try:
        doc = fitz.open(filepath)
    except Exception:
        logger.exception("Failed to open PDF.")
        return []

    text = "\n\n".join(clean_whitespace(page.get_text("text")) for page in doc)
    text = re.sub(r'\n+\s*\d+\s*\n+', '\n\n', text)
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
    return prune_disallowed_sections(chapters)


def _extract_txt(filepath: str) -> List[Tuple[str, str]]:
    logger.debug(f"Extracting TXT: {filepath}")
    try:
        text = Path(filepath).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        text = Path(filepath).read_text(encoding='latin-1')

    text = clean_whitespace(text)
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
    return prune_disallowed_sections(chapters)

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
    Legacy wrapper matching the old API signature.
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

