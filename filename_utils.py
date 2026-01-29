import re

def sanitize_filename(text: str, max_length: int = 200) -> str:
    """
    Sanitizes a string to be safe for filenames.
    - Replaces non-alphanumeric chars with underscores.
    - Collapses multiple underscores.
    - Truncates to max_length (default 200 to leave room for extensions like .mp3.meta.json).
    """
    # Replace unsafe characters
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '_', text)
    
    # Collapse underscores
    cleaned = re.sub(r'_+', '_', cleaned)
    
    # Strip leading/trailing underscores
    cleaned = cleaned.strip('_')
    
    # Truncate
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
        # Ensure we didn't slice in a way that leaves a trailing underscore (aesthetic)
        cleaned = cleaned.strip('_')
        
    return cleaned
