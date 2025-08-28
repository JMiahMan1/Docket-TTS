import os
import requests
import time
import pytest

# The base URL for the running web application
# Assumes the app is running on localhost:8000 as per docker-compose.yml
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")
POLL_INTERVAL = 3  # seconds
TIMEOUT = 180      # seconds

def submit_and_poll_task(title, text_content):
    """Helper function to submit a task and poll for its completion."""
    # Step 1: Submit the text for conversion
    payload = {
        'text_title': title,
        'text_input': text_content,
        'voice': 'en_US-hfc_male-medium.onnx' # Use a default voice for testing
    }
    submit_response = requests.post(f"{BASE_URL}/", data=payload)
    assert submit_response.status_code == 200, f"Failed to submit task. Status: {submit_response.status_code}"
    
    # Extract task ID from the response HTML
    task_id_line = [line for line in submit_response.text.split('\n') if "const taskId = " in line]
    assert task_id_line, "Could not find task ID in the response."
    task_id = task_id_line[0].split('"')[1]
    assert task_id, "Task ID is empty."

    # Step 2: Poll the status endpoint until the task is complete
    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        status_response = requests.get(f"{BASE_URL}/status/{task_id}")
        assert status_response.status_code == 200, f"Status check failed for task {task_id}"
        status_data = status_response.json()
        if status_data.get('state') == 'SUCCESS':
            # Step 3: Verify the normalized text output
            result = status_data['status']
            assert result['status'] == 'Success', f"Task {task_id} completed but reported failure."
            txt_filename = result.get('textfile')
            assert txt_filename, "No text file was generated."
            
            txt_response = requests.get(f"{BASE_URL}/generated/{txt_filename}")
            assert txt_response.status_code == 200, f"Failed to download generated text file {txt_filename}"
            
            return txt_response.text
            
        elif status_data.get('state') == 'FAILURE':
            pytest.fail(f"Task {task_id} failed with message: {status_data.get('status')}")
            
        time.sleep(POLL_INTERVAL)
    
    pytest.fail(f"Task {task_id} timed out after {TIMEOUT} seconds.")

def test_multi_book_references():
    """
    Tests normalization of a string with multiple book, chapter, and verse references.
    Sourced from '05 Romans - The Divine Marriage...pdf'.
    """
    title = "Multi-Book Test"
    text = "many scholars believe both the Genesis narratives of the birth of Isaac (Gen 17:17; 18:1-15; 21:1-7) and the offering of Isaac as a sacrifice (Gen 22:15-17) show additional occasions"
    
    normalized_text = submit_and_poll_task(title, text).lower()

    assert "genesis chapter seventeen, verse seventeen" in normalized_text
    assert "genesis chapter eighteen, verses one through fifteen" in normalized_text
    assert "genesis chapter twenty-one, verses one through seven" in normalized_text
    assert "genesis chapter twenty-two, verses fifteen through seventeen" in normalized_text

def test_f_and_ff_suffixes():
    """
    Tests normalization of verse references with 'f' and 'ff' suffixes.
    """
    title = "F and FF Suffix Test"
    text = "Paul discusses the sacrifice of Jesus (Rom 3:21ff) where we found pointers to the death of Jesus, and also the Passover (1 Cor 5:7f)."
    
    normalized_text = submit_and_poll_task(title, text).lower()
    
    assert "romans chapter three, verse twenty-one and following" in normalized_text
    assert "first corinthians chapter five, verse seven and the following verse" in normalized_text

def test_partial_verses():
    """
    Tests normalization of partial verses like '19a' and '19b'.
    Sourced from '05 Romans - The Divine Marriage...pdf'.
    """
    title = "Partial Verse Test"
    text = "This term speaks of lawlessness [Rom 6:19a; 1 John 3:4], producing lawless deeds [Matt 13:41; Rom 6:19b; Heb 10:17]."

    normalized_text = submit_and_poll_task(title, text).lower()

    assert "romans chapter six, verse nineteen a" in normalized_text
    assert "romans chapter six, verse nineteen b" in normalized_text
    assert "first john chapter three, verse four" in normalized_text
    assert "matthew chapter thirteen, verse forty-one" in normalized_text

def test_roman_numeral_expansion():
    """
    Tests the expansion of Roman numerals.
    """
    title = "Roman Numeral Test"
    text = "The council in Acts XV was a pivotal moment. The events of chapter VI are also important, see section IV."
    
    normalized_text = submit_and_poll_task(title, text)
    
    assert "Acts Roman Numeral fifteen" in normalized_text
    assert "chapter Roman Numeral six" in normalized_text
    assert "section Roman Numeral four" in normalized_text

def test_greek_transliteration():
    """
    Tests the transliteration of Greek words into English characters.
    Sourced from '05 Romans - The Divine Marriage...pdf'.
    """
    title = "Greek Transliteration Test"
    text = "The first word is άνομίαι anomiai. The second Greek word is ἁμαρτίαν 'amartiai."

    normalized_text = submit_and_poll_task(title, text)

    assert "anomiai" in normalized_text
    assert "amartiai" in normalized_text

def test_latin_phrase_expansion():
    """
    Tests the expansion of common Latin abbreviations.
    """
    title = "Latin Phrase Test"
    text = "We must consider other factors, e.g., the historical context. This is different from the previous point, i.e., the textual context, cf. the primary sources."
    
    normalized_text = submit_and_poll_task(title, text)
    
    assert "for example" in normalized_text
    assert "that is" in normalized_text
    assert "compare" in normalized_text

def test_year_pronunciation():
    """
    Tests the special normalization logic for pronouncing years.
    """
    title = "Year Pronunciation Test"
    text = "The text was published in 1984. A revision was made in the year 2005. The original manuscript from 999 AD is lost."

    normalized_text = submit_and_poll_task(title, text).lower()
    
    assert "nineteen eighty-four" in normalized_text
    assert "two thousand five" in normalized_text
    assert "nine hundred ninety-nine" in normalized_text
