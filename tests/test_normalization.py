from tts_service import normalize_text
import pytest

def test_header_and_verse_separation():
    """
    Tests that a header embedded with a word-based verse is correctly separated.
    Example: '...PEOPLEfive:one'
    """
    input_text = "Romans chapter five, verse THE MESSIAH KING AND THE PILGRIMAGE OF HIS PEOPLEfive:one, which is a tricky case."
    expected_fragment = "THE MESSIAH KING AND THE PILGRIMAGE OF HIS PEOPLE. verse one"
    
    result = normalize_text(input_text)
    
    assert expected_fragment in result
    assert "PEOPLEfive:one" not in result

def test_numeric_verse_and_header_separation():
    """
    Tests that a leading numeric verse is converted correctly and does not merge with a following header.
    Example: ':83 THE GLORY...'
    """
    input_text = "\n:83 THE GLORY OF GOD. And we rejoice in the hope of the glory of God."
    expected_fragment = "verse eighty-three THE GLORY OF GOD."
    
    result = normalize_text(input_text)

    assert expected_fragment in result
    assert "eighty-threeTHE GLORY" not in result

def test_biblical_verse_stripping():
    """
    Tests that verse numbers and other artifacts are stripped from Biblical text.
    """
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

    result = normalize_text(input_text)

    assert "11" not in result
    assert "12" not in result
    assert "13" not in result
    assert "14" not in result
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
    result = normalize_text(input_text)

    assert "chapter two verse five" in result
    assert "eleven" not in result
    assert "Paul has already" in result

def test_footnote_removal():
    """Tests that alphabetic footnotes like [a], [b] are removed."""
    input_text = """
The Lord is my shepherd;
I shall not [a]want.
He makes me to lie down in [b]green pastures;
He leads me beside the [c]still waters.
"""
    result = normalize_text(input_text)

    assert "[a]" not in result
    assert "[b]" not in result
    assert "[c]" not in result
    assert "not want" in result
    assert "in green pastures" in result
    assert "the still waters" in result

def test_pdf_verse_and_footnote_removal():
    """Tests the removal of artifacts from the Romans 3 PDF text."""

    input_text = """³
God’s Judgment Defended
¹What advantage then has the Jew, or what is the profit of circumcision? ²Much in every way! Chiefly because ᵃto them were committed the ¹oracles of God. ³For what if ᵇsome did not believe? ᶜWill their unbelief make the faithfulness of God without effect? ⁴ᵈCertainly not! Indeed, let ᵉGod be ²true but ᶠevery man a liar. As it is written:"""
    
    result = normalize_text(input_text)

    # Check that verse numbers are gone
    assert "¹What" not in result
    
    # Check that footnote letters are gone
    assert "ᵇsome" not in result
    assert "ᵈCertainly" not in result
    assert "ᵃto them" not in result

    # Check that the text is preserved
    assert "What advantage then has the Jew" in result
    assert "Certainly not!" in result
    assert "Indeed, let God be true but every man a liar" in result

def test_bracketed_numeric_footnote_removal():
    """
    Tests that bracketed numeric footnotes, common in academic PDFs, are removed.
    This pattern is found throughout the provided PDF.
    """
    input_text = "This gives Paul the opportunity to establish his message.[39] The term was very important in the LXX.[40]"
    result = normalize_text(input_text)

    assert "[39]" not in result
    assert "[40]" not in result
    assert "his message. The term" in result

def test_hebrew_and_inline_greek_normalization():
    """
    Tests that Hebrew text is translated and Greek text is transliterated correctly.
    """
    input_text = 'The term Paul uses is δοῦλος (doulos) "servant." The Hebrew is מִשִׁיחַ (māsaḥ) "Messiah."'
    result = normalize_text(input_text)
    
    # Check that the Greek is transliterated correctly to 'doulos'
    assert "doulos , doulos" in result

    # Check that the verbose "translation from" text is gone
    assert "translation from Hebrew:" not in result.lower()
    
    # Check that the Hebrew translation is present
    assert "Messiah" in result

def test_hebrew_translation_simple():
    """
    Provides a focused test for Hebrew translation without verbose text.
    """
    input_text = "The Hebrew word is מִשִׁיחַ."
    # The final space-collapsing rule will clean this up
    expected = "The Hebrew word is Messiah ."
    
    result = normalize_text(input_text)
    
    assert result == expected
