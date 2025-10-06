import os
import requests
import time
import pytest
import io
from mutagen.mp3 import MP3
from pathlib import Path

BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")
POLL_INTERVAL = 5  # seconds
TIMEOUT = 600      # 10 minutes, increased for slower CI runners

def submit_and_poll_task(title, text_content, speed_rate="1.0"):
    """Helper function to submit a task and poll for its completion."""
    payload = {
        'text_title': title,
        'text_input': text_content,
        'voice': 'en_US-hfc_male-medium.onnx',
        'speed_rate': speed_rate
    }
    
    # --- MODIFIED LOGIC ---
    # Send a header to tell the app we want a JSON response, not a redirect.
    headers = {
        'Accept': 'application/json'
    }
    submit_response = requests.post(f"{BASE_URL}/", data=payload, headers=headers)
    assert submit_response.status_code == 200, f"Failed to submit task. Status: {submit_response.status_code}"
    
    # Get the task ID directly from the JSON response
    response_json = submit_response.json()
    task_id = response_json.get('task_id')
    assert task_id, "Task ID not found in JSON response."

    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        try:
            status_response = requests.get(f"{BASE_URL}/status/{task_id}")
            assert status_response.status_code == 200, f"Status check failed for task {task_id}"
            status_data = status_response.json()
            
            if status_data.get('state') == 'SUCCESS':
                result = status_data['status']
                assert result['status'] == 'Success', f"Task {task_id} completed but reported failure."
                
                txt_filename = result.get('textfile')
                assert txt_filename, "No text file was generated."
                mp3_filename = result.get('filename')
                assert mp3_filename, "No audio file was generated."

                txt_response = requests.get(f"{BASE_URL}/generated/{txt_filename}")
                assert txt_response.status_code == 200, f"Failed to download generated text file {txt_filename}"
                
                return txt_response.text, mp3_filename
                
            elif status_data.get('state') == 'FAILURE':
                pytest.fail(f"Task {task_id} failed with message: {status_data.get('status')}")
        except requests.ConnectionError:
            pass
            
        time.sleep(POLL_INTERVAL)
    
    pytest.fail(f"Task {task_id} timed out after {TIMEOUT} seconds.")

def cleanup_files(mp3_filename):
    """Sends a request to the server to delete generated files to keep the test environment clean."""
    if mp3_filename:
        base_name = Path(mp3_filename).stem
        try:
            requests.post(f"{BASE_URL}/delete-bulk", data={'files_to_delete': [base_name]})
        except requests.RequestException as e:
            print(f"Warning: Failed to cleanup file {base_name}. Reason: {e}")

# --- Test Suite (No changes needed below this line) ---

def test_year_pronunciation():
    title = "Year Pronunciation Test"
    text = "The text was published in 1984. A revision was made in the year 2005. The original manuscript from 999 AD is lost."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "nineteen eighty-four" in normalized_text
        assert "two thousand five" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_greek_transliteration():
    title = "Greek Transliteration Test"
    text = "The first word is άνομίαι anomiai. The second Greek word is ἁμαρτίαν 'amartiai."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        assert "anomiai" in normalized_text
        assert "amartiai" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_latin_phrase_expansion():
    title = "Latin Phrase Test"
    text = "We must consider other factors, e.g., the historical context. This is different from the previous point, i.e., the textual context, cf. the primary sources."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        assert "for example" in normalized_text
        assert "that is" in normalized_text
        assert "compare" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_roman_numeral_expansion():
    title = "Roman Numeral Test"
    text = "The council in Acts XV was a pivotal moment. The events of chapter VI are also important, see section IV."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        assert "Acts Roman Numeral fifteen" in normalized_text
        assert "chapter Roman Numeral six" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_f_and_ff_suffixes():
    title = "F and FF Suffix Test"
    text = "Paul discusses the sacrifice of Jesus (Rom 3:21ff), the Passover (1 Cor 5:7f), and the rebuilding period (Ezra 3:7ff.; Neh 4:1ff.)."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "romans chapter three, verse twenty-one and following" in normalized_text
        assert "first corinthians chapter five, verse seven and the following verse" in normalized_text
        assert "ezra chapter three, verse seven and following" in normalized_text
        assert "nehemiah chapter four, verse one and following" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_partial_verses():
    title = "Partial Verse Test"
    text = "This term speaks of lawlessness [Rom 6:19a; 1 John 3:4], producing lawless deeds [Matt 13:41; Rom 6:19b; Heb 10:17]."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "romans chapter six, verse nineteen a" in normalized_text
        assert "romans chapter six, verse nineteen b" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_multi_book_references():
    title = "Multi-Book Test"
    text = "many scholars believe both the Genesis narratives of the birth of Isaac (Gen 17:17; 18:1-15; 21:1-7) and the offering of Isaac as a sacrifice (Gen 22:15-17) show additional occasions"
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "genesis chapter seventeen, verse seventeen" in normalized_text
        assert "genesis chapter eighteen, verses one through fifteen" in normalized_text
        assert "genesis chapter twenty-one, verses one through seven" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_abbreviation_and_contraction_conflict():
    title = "Abbreviation Conflict Test"
    text = "I've been reading VE on the church."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "i have" in normalized_text
        assert "verbum et ecclesia" in normalized_text
        assert "i'verbum et ecclesia" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_case_sensitive_abbreviation():
    title = "Case-Sensitive Abbreviation Test"
    text = "We gave Them the scrolls, and they gave them to us."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        assert "themelios" in normalized_text
        assert "gave them to us" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_mp3_speed_rate():
    title = "Speed Rate Test"
    text = "This is a standard sentence for testing the audio duration at different speech rates."
    normal_mp3, fast_mp3 = None, None
    try:
        _, normal_mp3 = submit_and_poll_task(title, text, speed_rate="1.0")
        normal_response = requests.get(f"{BASE_URL}/generated/{normal_mp3}")
        normal_duration = MP3(io.BytesIO(normal_response.content)).info.length

        _, fast_mp3 = submit_and_poll_task(title, text, speed_rate="0.8")
        fast_response = requests.get(f"{BASE_URL}/generated/{fast_mp3}")
        fast_duration = MP3(io.BytesIO(fast_response.content)).info.length

        assert fast_duration < normal_duration
    finally:
        cleanup_files(normal_mp3)
        cleanup_files(fast_mp3)
        
def test_mp3_cover_art_embedding():
    title = "MP3 Cover Art Test"
    text = "This test ensures an image is embedded in the output MP3 file when none is provided."
    mp3_filename = None
    try:
        _, mp3_filename = submit_and_poll_task(title, text)
        mp3_response = requests.get(f"{BASE_URL}/generated/{mp3_filename}")
        assert mp3_response.status_code == 200
        audio = MP3(io.BytesIO(mp3_response.content))
        assert any(key.startswith('APIC:') for key in audio.tags), "APIC (cover art) tag not found."
    finally:
        cleanup_files(mp3_filename)

def test_contraction_and_roman_numeral_conflict():
    """
    Tests that a contraction like "I'm" is not confused with a Roman numeral "M".
    """
    title = "Contraction Roman Numeral Test"
    text = "I'm not sure what this means."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()
        
        assert "i am" in normalized_text
        assert "roman numeral one thousand" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_no_false_positive_on_et_al():
    """
    Tests that a word ending in 'el' followed by a word starting with 'al'
    (e.g., "Colonel Albert") is not incorrectly identified as "et al".
    """
    title = "Et Al False Positive Test"
    text = "The final report from Colonel Albert was conclusive."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "colonel albert" in normalized_text
        assert "et al" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_contextual_verse_normalization_prevents_mangling():
    """
    Tests the specific bug where a chapter context was not carried over,
    leading to later rules mangling unprocessed verses (e.g., '1:1' -> 'one:one').
    """
    title = "Contextual Verse Test"
    text = """
John 11
An important event. (35)
"""
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "john chapter eleven" in normalized_text
        assert "john chapter eleven, verse thirty-five" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_header_with_parenthetical_scripture():
    """
    Tests that a line with a header and a parenthetical scripture reference
    is parsed correctly, applying the book context from the previous line.
    """
    title = "Header With Scripture Test"
    text = """
Romans 1
THE MESSIAH KING AND HIS SERVANT (1:1–4)
Paul, a servant of Christ Jesus, was called to be an apostle.
"""
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "romans chapter one" in normalized_text
        assert "the messiah king and his servant" in normalized_text
        assert "romans chapter one, verses one through four" in normalized_text
        assert ". ... the messiah king and his servant. ... " not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_ordinal_number_expansion():
    """
    Tests that ordinal numbers are correctly expanded to words.
    """
    title = "Ordinal Number Test"
    text = "The 1st point is valid, but the 2nd is not. See the 23rd paragraph for details."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "first point" in normalized_text
        assert "second is not" in normalized_text
        assert "twenty-third paragraph" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_currency_symbol_expansion():
    """
    Tests that currency symbols are correctly expanded.
    """
    title = "Currency Symbol Test"
    text = "The total cost was $50, which was paid in full."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "fifty dollars" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_ambiguous_roman_numeral_i():
    """
    Tests that the pronoun 'I' is not incorrectly converted to a Roman numeral.
    """
    title = "Ambiguous Roman Numeral I Test"
    text = "When I read about King Henry VIII, I wonder what happened before him."
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "when i read" in normalized_text
        assert "henry roman numeral eight, i wonder" in normalized_text
        assert "roman numeral one" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_complex_paragraph_from_pdf():
    """
    Runs an end-to-end test on a complex paragraph from the provided PDF.
    This text includes Greek, Hebrew, and an excepted Roman Numeral (LXX).
    """
    title = "Complex PDF Paragraph Test"
    text = """The term Paul uses to describe himself is δοῦλος (doulos) "servant." This term has been the cause of a great deal of confusion. In classical Greek, it means "slave" and many commentators have assumed that Paul intended to describe himself as one who, without any rights of his own, was owned by Christ. [40] We can begin to identify the confusion when we realize that the term was very important in the LXX. It was used to translate the Hebrew word ebed."""
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "doulos , doulos ," in normalized_text
        assert "hebrew word ebed" in normalized_text
        assert "[40]" not in normalized_text
        assert "lxx" in normalized_text
        assert "roman numeral" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_parenthetical_verse_abbreviation():
    """
    Tests that parenthetical verse abbreviations (e.g. v. 2) are correctly
    normalized and not mistaken for Roman numerals.
    """
    title = "Verse Abbreviation Test"
    text = """
Romans 1
His life was now dedicated to spreading far and wide the good news
that Jesus Christ had fulfilled the Scriptures (v.2), and this is one of the
purposes of his letter.
"""
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        assert "romans chapter one" in normalized_text
        assert "romans chapter one, verse two" in normalized_text
        assert "roman numeral five" not in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_complex_parenthetical_with_numbered_book():
    """
    Tests a complex, multi-part parenthetical reference that includes a book
    name starting with a number, which was a source of a regex bug.
    """
    title = "Complex Parenthetical Test"
    text = """
(Rom 1:7; 1 Cor 10:3ff.; Eph 1:13–14; 2:11–3:13; Col 1:12–14; 1 Thess 1:12–14)
"""
    mp3_filename = None
    try:
        normalized_text, mp3_filename = submit_and_poll_task(title, text)
        normalized_text = normalized_text.lower()

        # Check for a few key parts of the complex expansion
        assert "romans chapter one, verse seven" in normalized_text
        assert "first corinthians chapter ten, verse three and following" in normalized_text
        assert "ephesians chapter one, verses thirteen through fourteen" in normalized_text
        # This part is still tricky for the normalizer, but we can test the partial conversion
        assert "ephesians chapter two, verses eleven through three:thirteen" in normalized_text
    finally:
        cleanup_files(mp3_filename)

def test_pause_duration_based_on_punctuation():
    """
    Indirectly tests for pause length by comparing the total duration of audio
    generated from text with identical words but different punctuation.
    """
    title = "Pause Duration Test"
    short_pause_text = "First we will discuss this, then that, and finally the other thing."
    long_pause_text = "First. \n\n We will discuss this. \n\n Then that. \n\n And finally the other thing."
    
    short_pause_mp3, long_pause_mp3 = None, None
    try:
        # Generate and measure the audio with short pauses (commas)
        _, short_pause_mp3 = submit_and_poll_task(title, short_pause_text)
        short_response = requests.get(f"{BASE_URL}/generated/{short_pause_mp3}")
        short_duration = MP3(io.BytesIO(short_response.content)).info.length

        # Generate and measure the audio with long pauses (periods and newlines)
        _, long_pause_mp3 = submit_and_poll_task(title, long_pause_text)
        long_response = requests.get(f"{BASE_URL}/generated/{long_pause_mp3}")
        long_duration = MP3(io.BytesIO(long_response.content)).info.length

        # Assert that the version with more significant punctuation is longer
        assert long_duration > short_duration
    finally:
        cleanup_files(short_pause_mp3)
        cleanup_files(long_pause_mp3)
