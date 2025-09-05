from tts_service import normalize_text
import pytest

def test_header_and_verse_separation():
    """
    Tests that a header embedded with a word-based verse is correctly separated.
    Example: '...PEOPLEfive:one'
    """
    # This input text is based on the provided document
    input_text = "Romans chapter five, verse THE MESSIAH KING AND THE PILGRIMAGE OF HIS PEOPLEfive:one, which is a tricky case."
    expected_fragment = "THE MESSIAH KING AND THE PILGRIMAGE OF HIS PEOPLE. verse one"
    
    # Run the normalization function
    result = normalize_text(input_text)
    
    # Assert that the formatted fragment is present in the result
    assert expected_fragment in result
    
    # Assert that the original problematic string is gone
    assert "PEOPLEfive:one" not in result

def test_numeric_verse_and_header_separation():
    """
    Tests that a leading numeric verse is converted correctly and does not merge with a following header.
    Example: ':83 THE GLORY...'
    """
    # This input simulates a line starting with a numeric verse marker followed immediately by a header
    input_text = "\n:83 THE GLORY OF GOD. And we rejoice in the hope of the glory of God."
    expected_fragment = "verse eighty-three THE GLORY OF GOD."
    
    # Run the normalization function
    result = normalize_text(input_text)

    # Assert that the space was correctly added after the converted verse number
    assert expected_fragment in result

    # Assert that the verse and header did not get merged
    assert "eighty-threeTHE GLORY" not in result

def test_biblical_verse_stripping():
    """
    Tests that verse numbers and other artifacts are stripped from Biblical text.
    """
    # This text is sourced from the provided Romans 3:1-20.pdf
    # It includes verse numbers at the start of lines and attached to quotes.
    input_text = """
    "There is none righteous, no, not one;
    11 There is none who understands;
    There is none who seeks after God.
    12 They have all turned aside;
    They have together become unprofitable;
    There is none who does good, no, not one."
    13 "Their throat is an open tomb;
    With their tongues they have practiced deceit";
    "The poison of asps is under their lips";
    14"Whose mouth is full of cursing and bitterness."
    """

    # Run the normalization function
    result = normalize_text(input_text)

    # Assert that the verse numbers have been removed
    assert "11" not in result
    assert "12" not in result
    assert "13" not in result
    assert "14" not in result

    # Assert that the core text remains
    assert "There is none who understands" in result
    assert "They have all turned aside" in result
    assert "Their throat is an open tomb" in result
    assert "Whose mouth is full of cursing and bitterness" in result

def test_leading_verse_marker_normalization():
    """
    Tests that leading chapter:verse markers are correctly parsed without
    affecting the stripping of standalone verse numbers.
    """
    input_text = """
2:5 Moses could have addressed this statement to Pharaoh.
11 Paul has already made his argument regarding the depravity of man.
"""

    expected_text = "chapter two verse five Moses could have addressed this statement to Pharaoh. ... Paul has already made his argument regarding the depravity of man."

    result = normalize_text(input_text)

    # Check that "2:5" was correctly converted
    assert "chapter two verse five" in result
    # Check that the standalone "11" was correctly stripped and not converted
    assert "eleven" not in result
    assert "Paul has already" in result
