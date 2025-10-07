import re
import logging
from collections import Counter
from typing import Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "section_markers": {
        # This list is now more targeted to avoid removing legitimate chapter headings.
        # It primarily targets the Table of Contents and back matter.
        r"^(Contents|Table of Contents)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface|Appendix|One|1)\s+",),
        r"^(Index|Bibliography|Works Cited|References|Glossary|About the Author|Author Bio)$": (None,),
        r"^(Copyright|Also by)$": (r"^\s*(Chapter|Part|Book|One|1)\s+",),
        r"^(List of Figures|List of Tables|List of Illustrations)$": (r"^\s*(Chapter|Part|Book|Introduction|Prologue|Preface)\s+",)
    },
    "paragraph_disallow_patterns": [
        # More aggressive patterns to remove publisher/copyright lines
        re.compile(r'ISBN|Library of Congress|All rights reserved|Printed in the|copyright ©|www\..*\.com', re.IGNORECASE),
        re.compile(r'^\s*(A division of|Published by|Manufactured in the United States of America)', re.IGNORECASE),
        re.compile(r'\.{5,}'),  # Matches dot leaders in a ToC
    ],
    "header_footer_config": {
        "min_line_len": 3,
        "max_line_len": 75,
        "min_occurrence": 4,
        "max_word_count": 10
    }
}

def clean_text(text: str, config: Dict[str, Any] = None) -> str:
    if config is None:
        config = DEFAULT_CONFIG

    cleaned_text = text

    for start_pattern, end_patterns in config["section_markers"].items():
        try:
            matches = list(re.finditer(start_pattern, cleaned_text, re.IGNORECASE | re.MULTILINE))
            for start_match in reversed(matches):
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

    paragraphs = cleaned_text.split('\n')
    kept_paragraphs = []
    for para in paragraphs:
        if para.strip():
            is_disallowed = any(pattern.search(para) for pattern in config["paragraph_disallow_patterns"])
            if not is_disallowed:
                kept_paragraphs.append(para)
    cleaned_text = "\n".join(kept_paragraphs)

    h_config = config["header_footer_config"]
    lines = cleaned_text.split('\n')
    
    potential_headers = []
    line_counts = Counter(line.strip() for line in lines if line.strip())
    for line, count in line_counts.items():
        line_len = len(line)
        word_count = len(line.split())
        
        is_just_number = line.isdigit() and count > 10
        is_short_and_common = word_count <= h_config["max_word_count"] and count >= h_config["min_occurrence"]

        if (is_short_and_common or is_just_number) and \
           h_config["min_line_len"] <= line_len <= h_config["max_line_len"] and \
           not re.match(r"^\s*(chapter|part|book)\s+", line, re.IGNORECASE):
            potential_headers.append(re.escape(line))

    if potential_headers:
        logger.info(f"Removing {len(potential_headers)} potential header/footer lines.")
        header_pattern = re.compile(r"^\s*(" + "|".join(potential_headers) + r")\s*$", re.MULTILINE)
        cleaned_text = header_pattern.sub("", cleaned_text)
    
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)

    return cleaned_text.strip()
