# /app/test_text_cleaner.py

from text_cleaner import clean_text

def test_full_text_cleaning_process():
    """
    Tests the removal of various non-narrative sections, including a dedication,
    an index, and repetitive headers/footers.
    """
    dummy_text = """
    My Awesome Book
    My Awesome Book
    
    Dedication
    To my family.
    
    CHAPTER 1: The Beginning
    
    This is the first paragraph of the story. It is very engaging.
    
    My Awesome Book
    
    This is the second paragraph.
    
    Page 3
    
    CHAPTER 2: The Middle
    
    This is the middle part of the story.
    
    My Awesome Book
    
    Index
    
    Aardvark: 4
    Banana: 12
    """

    cleaned_text = clean_text(dummy_text)

    # --- Assertions to verify the cleaning process ---
    
    # Assert that the narrative content remains
    assert "CHAPTER 1: The Beginning" in cleaned_text
    assert "This is the first paragraph of the story." in cleaned_text
    assert "CHAPTER 2: The Middle" in cleaned_text

    # Assert that the non-narrative sections were removed
    assert "Dedication" not in cleaned_text
    assert "To my family." not in cleaned_text
    assert "Index" not in cleaned_text
    assert "Aardvark: 4" not in cleaned_text
    
    # Assert that repetitive headers and footers were removed
    assert "My Awesome Book" not in cleaned_text
    assert "Page 3" not in cleaned_text
