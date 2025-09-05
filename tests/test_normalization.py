from tts_service import normalize_text

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
