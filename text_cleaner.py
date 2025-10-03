import re
import logging
from collections import Counter
from typing import Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "section_markers": {
        r"^(Contents|Table of Contents)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface|Appendix)\s+",),
        r"^(Index|Bibliography|Works Cited|References|Glossary)$": (None,),  # None means "remove to the end of the book"
        r"^(Acknowledgments|Dedication|Foreword|Preface|Introduction|Epigraph)$": (r"^\s*(Chapter|Part|Book|One|1)\s+",),
        r"^(About the Author|Author Bio)$": (None,),
        r"^(List of Figures|List of Tables|List of Illustrations)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface)\s+",)
    },
    "paragraph_disallow_patterns": [
        re.compile(r'ISBN|Library of Congress|All rights reserved|Printed in the|copyright ©', re.IGNORECASE),
        re.compile(r'\.{5,}'), # Matches dot leaders in a ToC
    ],
    "header_footer_config": {
        "min_line_len": 3,
        "max_line_len": 75,
        "min_occurrence": 4,
        "max_word_count": 10
    }
}

def clean_text(text: str, config: Dict[str, Any] = None) -> str:
    """
    Cleans a raw text string by removing non-narrative sections using multiple strategies.
    """
    if config is None:
        config = DEFAULT_CONFIG

    cleaned_text = text

    # Strategy 1: Remove major sections based on start/end headers
    for start_pattern, end_patterns in config["section_markers"].items():
        try:
            start_match = re.search(start_pattern, cleaned_text, re.IGNORECASE | re.MULTILINE)
            if not start_match:
                continue

            start_index = start_match.start()
            end_index = len(cleaned_text)

            if end_patterns:
                search_area = cleaned_text[start_match.end():]
                for end_pattern in end_patterns:
                    end_match = re.search(end_pattern, search_area, re.IGNORECASE | re.MULTILINE)
                    if end_match:
                        end_index = start_match.end() + end_match.start()
                        break
            
            logger.info(f"Removing section detected by '{start_pattern}' from index {start_index} to {end_index}.")
            cleaned_text = cleaned_text[:start_index] + cleaned_text[end_index:]
        except Exception as e:
            logger.error(f"Error processing section rule for '{start_pattern}': {e}")

    # Strategy 2: Remove individual paragraphs that match disallowed patterns
    paragraphs = cleaned_text.split('\n')
    kept_paragraphs = []
    for para in paragraphs:
        is_disallowed = any(pattern.search(para) for pattern in config["paragraph_disallow_patterns"])
        if not is_disallowed:
            kept_paragraphs.append(para)
    cleaned_text = "\n".join(kept_paragraphs)

    # Strategy 3: Remove repetitive headers and footers using frequency analysis
    h_config = config["header_footer_config"]
    lines = cleaned_text.split('\n')
    
    potential_headers = []
    line_counts = Counter(line.strip() for line in lines if line.strip())
    for line, count in line_counts.items():
        line_len = len(line)
        word_count = len(line.split())
        
        if (h_config["min_occurrence"] <= count and
            h_config["min_line_len"] <= line_len <= h_config["max_line_len"] and
            word_count <= h_config["max_word_count"] and
            not re.match(r"^\s*(chapter|part|book)\s+", line, re.IGNORECASE)):
            potential_headers.append(re.escape(line))

    if potential_headers:
        logger.info(f"Removing {len(potential_headers)} potential header/footer lines.")
        header_pattern = re.compile(r"^\s*(" + "|".join(potential_headers) + r")\s*$", re.MULTILINE)
        cleaned_text = header_pattern.sub("", cleaned_text)
    
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)

    return cleaned_text.strip()
