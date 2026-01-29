
import sys
import os
import re
import json
from pathlib import Path

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tts_service import normalize_text, normalize_scripture

def test_normalization():
    print("--- Testing Normalization Issues ---")

    # Case 1: Pss and Exodus
    # "Pss" was missing. "Exod" might have context issues.
    # User said: "adding the previous book it did know to the wrong reference"
    # Example: "Gen 1:1 ... Pss 23" -> If Pss isn't found, does it think "23" belongs to Genesis?
    text1 = "We read Gen 1:1. Then we look at Pss 23. Also Exod 4:5."
    print(f"\nInput: {text1}")
    print(f"Output: {normalize_text(text1)}")
    
    # Case 2: Chronicles
    # "removed the number before Chron.?" -> "1 Chron." -> "Chronicles" instead of "First Chronicles"
    text2 = "Read 1 Chron. 4:9 and 2 Chron. 7:14."
    print(f"\nInput: {text2}")
    print(f"Output: {normalize_text(text2)}")

    # Case 3: Page Numbers
    # "included the Page number in the end text result"
    text3 = "This is the end of the paragraph.\nPage 45\nNext paragraph."
    print(f"\nInput: {text3}")
    print(f"Output: {normalize_text(text3)}")
    
    # Case 4: Wrong Reference Context (The User's "Zephaniah" bug)
    # User had "Zephaniah" context, then "Exod. 15:11". result was "Exod. Zephaniah chapter fifteen..."
    # This implies "Exod." was ignored, and "15:11" used previous context.
    text_context = "We read Zephaniah 1:1. Then Exod. 15:11. Then Pss. 64:9."
    print(f"\nInput: {text_context}")
    print(f"Output: {normalize_text(text_context)}")

    # Case 5: 1 Chron failure
    # "removed the number before Chron.?" -> "1 Chron. 16:27"
    text_chron = "First Chronicles 1:1. Also 1 Chron. 16:27."
    print(f"\nInput: {text_chron}")
    print(f"Output: {normalize_text(text_chron)}")

    # Case 6: Page Numbers
    # ", , PAGE_52 , ,"
    text_page = "Some text.\n, , PAGE_52 , ,\nMore text."
    print(f"\nInput: {text_page}")
    print(f"Output: {normalize_text(text_page)}")

    # Case 7: Number eating? "Leviticus, times;" (Should be "Leviticus, 5 times")
    text_num = "In Leviticus, 5 times; Exodus, 102 times;"
    print(f"\nInput: {text_num}")
    print(f"Output: {normalize_text(text_num)}")

if __name__ == "__main__":
    test_normalization()
