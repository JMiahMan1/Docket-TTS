import re
import logging
from collections import Counter
from typing import Dict, Any

# Use the Werkzeug logger to integrate with the main Flask app's logging
logger = logging.getLogger('werkzeug')

DEFAULT_CONFIG = {
    "section_markers": {
        # Flexible rule for table of contents
        r"^(Contents|Table of Contents)": (
            r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface|Appendix|One|1)\s+",
        ),
        # Added rule for "Praise for" pages
        r"^\s*Praise for\b": (
            r"^\s*(Contents|Table of Contents|Chapter|Part|Book|Introduction|Prologue|Preface|Appendix|One|1)\s+",
        ),
        # Dedication, Preface, etc. — allow leading whitespace
        r"^\s*(Dedication|Foreword|Preface|Introduction)": (
            r"^\s*(Chapter|Part|Book|One|1)\s+",
        ),
        # Index and similar — allow indentation and capitalization differences
        r"^\s*(Index|Bibliography|Works Cited|References|Glossary|About the Author|Author Bio)\s*$": (
            None,
        ),
        # Copyright and "Also by" sections
        r"^(Copyright|Also by)$": (
            r"^\s*(Chapter|Part|Book|One|1)\s+",
        ),
        # Lists and figure sections
        r"^(List of Figures|List of Tables|List of Illustrations)$": (
            r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface)\s+",
        ),
    },
    "paragraph_disallow_patterns": [
        re.compile(
            r"ISBN|Library of Congress|All rights reserved|Printed in the|copyright ©|www\..*\.com",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(A division of|Published by|Manufactured in the United States of America)",
            re.IGNORECASE,
        ),
        re.compile(r"\.{5,}"),  # dot leaders in ToC
        re.compile(
            r"^\s*\d+\.\s+.*(?:p\.|pp\.|ibid\.|(?:New York|Grand Rapids|London|Chicago):|\b(19|20)\d{2}\b)",
            re.IGNORECASE,
        ),
    ],
    "header_footer_config": {
        "min_line_len": 3,
        "max_line_len": 75,
        "min_occurrence": 4,
        "max_word_count": 10,
    },
}


def clean_text(text: str, config: Dict[str, Any] = None, debug_level: str = 'off') -> str:
    """
    Cleans text extracted from books or documents by:
    - Removing non-narrative sections (Dedication, Index, etc.)
    - Removing headers, footers, and repetitive elements
    - Stripping out page numbers and boilerplate
    """
    if config is None:
        config = DEFAULT_CONFIG

    if debug_level in ['debug', 'trace']:
        logger.debug(f"Starting text cleaning. Initial length: {len(text)} chars.")
    
    original_len = len(text)
    cleaned_text = text

    # --- Remove marked sections (Dedication, Index, etc.) ---
    text_before_step = cleaned_text
    for start_pattern, end_patterns in config["section_markers"].items():
        try:
            matches = list(
                re.finditer(start_pattern, cleaned_text, re.IGNORECASE | re.MULTILINE)
            )
            for start_match in reversed(matches):
                start_index = start_match.start()

                if end_patterns is None or end_patterns == (None,):
                    if debug_level in ['debug', 'trace']:
                        logger.debug(f"Removing section '{start_match.group(0).strip()}' from index {start_index} to end.")
                    if debug_level == 'trace':
                        logger.debug(f"  > Trace: Removed content snippet: '{cleaned_text[start_index:][:100]}...'")
                    cleaned_text = cleaned_text[:start_index]
                    continue

                end_index = -1
                search_area = cleaned_text[start_match.end():]
                for end_pattern in end_patterns:
                    if not isinstance(end_pattern, str):
                        continue
                    end_match = re.search(end_pattern, search_area, re.IGNORECASE | re.MULTILINE)
                    if end_match:
                        end_index = start_match.end() + end_match.start()
                        break

                if end_index != -1:
                    if debug_level in ['debug', 'trace']:
                        logger.debug(f"Removing section '{start_match.group(0).strip()}' from index {start_index} to {end_index}.")
                    if debug_level == 'trace':
                        logger.debug(f"  > Trace: Removed content snippet: '{cleaned_text[start_index:end_index][:100]}...'")
                    cleaned_text = cleaned_text[:start_index] + cleaned_text[end_index:]
                else:
                    if debug_level in ['debug', 'trace']:
                        logger.debug(f"Removing section '{start_match.group(0).strip()}' from index {start_index} to end (no end marker found).")
                    if debug_level == 'trace':
                        logger.debug(f"  > Trace: Removed content snippet: '{cleaned_text[start_index:][:100]}...'")
                    cleaned_text = cleaned_text[:start_index]

        except Exception as e:
            logger.error(f"Error processing section rule for '{start_pattern}': {e}")
    
    if debug_level == 'trace' and len(cleaned_text) != len(text_before_step):
        logger.debug(f"  > Trace: Section removal reduced text by {len(text_before_step) - len(cleaned_text)} chars.")
        text_before_step = cleaned_text

    # --- Filter out disallowed paragraphs ---
    paragraphs = cleaned_text.split("\n")
    kept_paragraphs = []
    removed_para_count = 0
    for para in paragraphs:
        if para.strip():
            is_disallowed = any(
                pattern.search(para) for pattern in config["paragraph_disallow_patterns"]
            )
            if not is_disallowed:
                kept_paragraphs.append(para)
            elif debug_level == 'trace':
                logger.debug(f"  > Trace: Removing disallowed paragraph: '{para[:100]}...'")
                removed_para_count += 1
    
    if debug_level in ['debug', 'trace'] and removed_para_count > 0:
        logger.debug(f"Removed {removed_para_count} disallowed paragraphs.")
    
    cleaned_text = "\n".join(kept_paragraphs)
    text_before_step = cleaned_text

    # --- Detect and remove common headers/footers ---
    h_config = config["header_footer_config"]
    lines = cleaned_text.split("\n")

    potential_headers = []
    line_counts = Counter(line.strip() for line in lines if line.strip())
    for line, count in line_counts.items():
        line_len = len(line)
        word_count = len(line.split())
        is_just_number = line.isdigit() and count > 10
        is_short_and_common = (
            word_count <= h_config["max_word_count"]
            and count >= h_config["min_occurrence"]
        )
        if (
            (is_short_and_common or is_just_number)
            and h_config["min_line_len"] <= line_len <= h_config["max_line_len"]
            and not re.match(r"^\s*(chapter|part|book)\s+", line, re.IGNORECASE)
        ):
            potential_headers.append(re.escape(line))

    if potential_headers:
        if debug_level in ['debug', 'trace']:
            logger.debug(f"Found {len(potential_headers)} potential header/footer lines to remove.")
        if debug_level == 'trace':
            logger.debug(f"  > Trace: Header/footer candidates: {potential_headers[:5]}")
        
        header_pattern = re.compile(
            r"^\s*(" + "|".join(potential_headers) + r")\s*$", re.MULTILINE
        )
        cleaned_text = header_pattern.sub("", cleaned_text)

    # --- Remove single page markers like "Page 3" or "3" ---
    cleaned_text = re.sub(r'^\s*Page\s*\d+\s*$', '', cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r'^\s*\d+\s*$', '', cleaned_text, flags=re.MULTILINE)

    # --- Normalize blank lines ---
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    
    if debug_level in ['debug', 'trace']:
        final_len = len(cleaned_text)
        logger.debug(f"Text cleaning complete. Total characters removed: {original_len - final_len}.")

    return cleaned_text.strip()
