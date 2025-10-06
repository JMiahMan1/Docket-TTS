# /app/test_chapterizer.py
import pytest
from chapterizer import chapterize, Chapter, DEFAULT_CONFIG

# Use a config with a low word count for easier testing
TEST_CONFIG = DEFAULT_CONFIG.copy()
TEST_CONFIG['min_chapter_word_count'] = 5
TEST_CONFIG['min_regex_chapter_length'] = 10


def test_split_text_with_simple_heuristics():
    """
    Tests the chapterizer's ability to split a simple text file based on
    "CHAPTER X" headings.
    """
    dummy_text_content = """
    This is an introduction that is long enough to be included.

    CHAPTER 1: The First Step
    This is the first part, which is also long enough.

    CHAPTER 2: The Second Step
    This is the second part, also long enough.
    """
    
    # We pass a dummy filepath and the actual text content
    chapters = chapterize(filepath="dummy_book.txt", text_content=dummy_text_content, config=TEST_CONFIG)

    # Assert that the correct number of chapters were found
    assert len(chapters) == 3

    # --- UPDATED ASSERTIONS ---
    # Assert the details of the first chapter (Introduction)
    assert chapters[0].title == "Introduction"
    assert "This is an introduction" in chapters[0].content
    assert chapters[0].number == 1 # Numbering now starts at 1

    # Assert the details of the second chapter
    assert chapters[1].title == "Chapter 1: The First Step"
    assert "This is the first part" in chapters[1].content
    assert chapters[1].number == 2

    # Assert the details of the third chapter
    assert chapters[2].title == "Chapter 2: The Second Step"
    assert "This is the second part" in chapters[2].content
    assert chapters[2].number == 3
