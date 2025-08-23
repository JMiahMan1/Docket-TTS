import re
import json

def load_expansions(filepath: str = "abbreviations.json") -> dict:
    """Loads the abbreviation dictionary from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Warning: Abbreviation file not found at {filepath}. Skipping expansion.")
        return {}
    except json.JSONDecodeError:
        print(f"Warning: Could not decode {filepath}. Skipping expansion.")
        return {}

def expand_abbreviations(text: str, expansions: dict) -> str:
    """Expands abbreviations in a given text using a dictionary."""
    for abbr, full in expansions.items():
        # Using re.IGNORECASE to catch variations like 'gen.' vs 'Gen.'
        text = re.sub(abbr, full, text, flags=re.IGNORECASE)
    return text

def format_for_tts(text: str) -> str:
    """
    Cleans and formats text for better TTS narration.
    Piper supports a subset of SSML (Speech Synthesis Markup Language).
    """
    # 1. Normalize whitespace: multiple spaces/newlines become a single space.
    text = re.sub(r'\s+', ' ', text).strip()

    # 2. Add a pause after sentences.
    # Using a lookbehind `(?<=[.!?])` to add the break after the punctuation.
    text = re.sub(r'(?<=[.!?])\s*', r' <break time="500ms"/> ', text)

    # 3. Add a longer pause for paragraph breaks (if marked by double newlines in original)
    # The initial whitespace normalization removes these, so this step assumes
    # logical paragraph breaks are represented by sentence-ending punctuation.
    # For more complex documents, you might need a different strategy.

    # 4. Handle specific patterns, e.g., ensure "1:1" is read as "one one" or "chapter one verse one"
    # For now, we'll just add a small pause after verse numbers.
    # Example: "Romans 1:16" -> "Romans 1:16 <break.../>"
    text = re.sub(r'(\d+:\d+)', r'\1<break time="250ms"/>', text)

    # 5. Wrap the entire text in SSML <speak> tags.
    # This is good practice and required by some TTS engines.
    ssml_text = f"<speak>{text}</speak>"

    return ssml_text

def process_text(raw_text: str) -> str:
    """Runs the full text processing pipeline."""
    expansions = load_expansions()
    expanded_text = expand_abbreviations(raw_text, expansions)
    formatted_text = format_for_tts(expanded_text)
    return formatted_text
