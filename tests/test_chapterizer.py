# /app/test_chapterizer.py

from chapterizer import chapterize, Chapter

def test_split_text_with_simple_heuristics():
    """
    Tests the chapterizer's ability to split a simple text file based on
    "CHAPTER X" headings.
    """
    dummy_text_content = """
    Introduction
    This is the intro. It has enough words to be considered a chapter.

    CHAPTER 1
    This is the first part.

    CHAPTER 2
    This is the second part.
    A little bit longer.
    """
    
    # We pass a dummy filepath and the actual text content
    chapters = chapterize(filepath="dummy_book.txt", text_content=dummy_text_content)

    # Assert that the correct number of chapters were found
    assert len(chapters) == 3

    # Assert the details of the first chapter (Introduction)
    assert chapters[0].title == "Introduction"
    assert "This is the intro" in chapters[0].content
    assert chapters[0].number == 0

    # Assert the details of the second chapter
    assert chapters[1].title == "Chapter 1"
    assert "This is the first part" in chapters[1].content
    assert chapters[1].number == 1

    # Assert the details of the third chapter
    assert chapters[2].title == "Chapter 2"
    assert "This is the second part" in chapters[2].content
    assert chapters[2].number == 2
