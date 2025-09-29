# /app/text_cleaner.py

import re
import logging
from collections import Counter
from typing import Dict, Any, List, Tuple

# Set up logging
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    # Patterns to identify the start of sections to be removed.
    # The key is a regex pattern to find the start.
    # The value is a tuple of patterns that signify the END of that section.
    # The cleaning will stop at the first end pattern found.
    # Use re.IGNORECASE | re.MULTILINE for matching.
    "section_markers": {
        r"^(Contents|Table of Contents)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue)\s+",),
        r"^(Index)$": (None,),  # None means "remove to the end of the book"
        r"^(Bibliography|Works Cited|References)$": (None,),
        r"^(Glossary)$": (None,),
        r"^(Acknowledgments|Dedication)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue)\s+",),
        r"^(About the Author|Author Bio)$": (None,),
        r"^(List of Figures|List of Tables)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue)\s+",)
    },
    # Configuration for removing repetitive headers and footers.
    "header_footer_config": {
        "min_line_len": 3,
        "max_line_len": 75,
        "min_occurrence": 4, # Line must appear at least this many times
        "max_word_count": 10
    }
}

def clean_text(text: str, config: Dict[str, Any] = None) -> str:
    """
    Cleans a raw text string by removing non-narrative sections.

    Args:
        text: The raw text extracted from a book.
        config: A configuration dictionary. Uses DEFAULT_CONFIG if None.

    Returns:
        The cleaned narrative text.
    """
    if config is None:
        config = DEFAULT_CONFIG

    cleaned_text = text

    # 1. Remove major sections like ToC, Index, etc.
    for start_pattern, end_patterns in config["section_markers"].items():
        try:
            start_match = re.search(start_pattern, cleaned_text, re.IGNORECASE | re.MULTILINE)
            if not start_match:
                continue

            start_index = start_match.start()
            end_index = len(cleaned_text) # Default to end of text

            if end_patterns:
                # Search for the end pattern *after* the start_match
                search_area = cleaned_text[start_match.end():]
                for end_pattern in end_patterns:
                    end_match = re.search(end_pattern, search_area, re.IGNORECASE | re.MULTILINE)
                    if end_match:
                        # Adjust end_index to be relative to the full text
                        end_index = start_match.end() + end_match.start()
                        break # Stop at the first end pattern that matches
            
            logger.info(f"Removing section detected by '{start_pattern}' from index {start_index} to {end_index}.")
            cleaned_text = cleaned_text[:start_index] + cleaned_text[end_index:]
        except Exception as e:
            logger.error(f"Error processing section rule for '{start_pattern}': {e}")


    # 2. Remove repetitive headers and footers using frequency analysis
    h_config = config["header_footer_config"]
    lines = cleaned_text.split('\n')
    
    # Find potential header/footer lines
    potential_headers = []
    line_counts = Counter(line.strip() for line in lines if line.strip())
    for line, count in line_counts.items():
        line_len = len(line)
        word_count = len(line.split())
        
        # Heuristic: if a line is short, not a chapter heading, and repeats often, it's a header/footer.
        if (h_config["min_occurrence"] <= count and
            h_config["min_line_len"] <= line_len <= h_config["max_line_len"] and
            word_count <= h_config["max_word_count"] and
            not re.match(r"^\s*(chapter|part|book)\s+", line, re.IGNORECASE)):
            potential_headers.append(re.escape(line))

    # Remove the identified lines from the text
    if potential_headers:
        logger.info(f"Removing {len(potential_headers)} potential header/footer lines.")
        header_pattern = re.compile(r"^\s*(" + "|".join(potential_headers) + r")\s*$", re.MULTILINE)
        cleaned_text = header_pattern.sub("", cleaned_text)
    
    # 3. Final cleanup of excessive whitespace
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)

    return cleaned_text.strip()
