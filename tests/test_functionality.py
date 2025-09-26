import os
import requests
import time
import pytest
import io
from mutagen.mp3 import MP3
from pathlib import Path
import re

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
    submit_response = requests.post(f"{BASE_URL}/", data=payload)
    assert submit_response.status_code == 200, f"Failed to submit task. Status: {submit_response.status_code}"
    
    task_id_line = [line for line in submit_response.text.split('\n') if "const taskId = " in line]
    assert task_id_line, "Could not find task ID in the response."
    task_id = task_id_line[0].split('"')[1]
    assert task_id, "Task ID is empty."

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

# --- Test Suite ---

def test_year_pronunciation():
    title = "Year Pronunciation Test"
    text = "The text was published in 1984. A revision was made in the year 2005. The original manuscript from 999 AD is lost."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "nineteen eighty-four" in normalized_text
    assert "two thousand five" in normalized_text

def test_greek_transliteration():
    title = "Greek Transliteration Test"
    text = "The first word is άνομίαι anomiai. The second Greek word is ἁμαρτίαν 'amartiai."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "anomiai" in normalized_text
    assert "amartiai" in normalized_text

def test_latin_phrase_expansion():
    title = "Latin Phrase Test"
    text = "We must consider other factors, e.g., the historical context. This is different from the previous point, i.e., the textual context, cf. the primary sources."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "for example" in normalized_text
    assert "that is" in normalized_text
    assert "compare" in normalized_text

def test_roman_numeral_expansion():
    title = "Roman Numeral Test"
    text = "The council in Acts XV was a pivotal moment. The events of chapter VI are also important, see section IV."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "Acts Roman Numeral fifteen" in normalized_text
    assert "chapter Roman Numeral six" in normalized_text

def test_f_and_ff_suffixes():
    title = "F and FF Suffix Test"
    text = "Paul discusses the sacrifice of Jesus (Rom 3:21ff), the Passover (1 Cor 5:7f), and the rebuilding period (Ezra 3:7ff.; Neh 4:1ff.)."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "romans chapter three, verse twenty-one and following" in normalized_text
    assert "first corinthians chapter five, verse seven and the following verse" in normalized_text
    assert "ezra chapter three, verse seven and following" in normalized_text
    assert "nehemiah chapter four, verse one and following" in normalized_text

def test_partial_verses():
    title = "Partial Verse Test"
    text = "This term speaks of lawlessness [Rom 6:19a; 1 John 3:4], producing lawless deeds [Matt 13:41; Rom 6:19b; Heb 10:17]."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "romans chapter six, verse nineteen a" in normalized_text
    assert "romans chapter six, verse nineteen b" in normalized_text

def test_multi_book_references():
    title = "Multi-Book Test"
    text = "many scholars believe both the Genesis narratives of the birth of Isaac (Gen 17:17; 18:1-15; 21:1-7) and the offering of Isaac as a sacrifice (Gen 22:15-17) show additional occasions"
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "genesis chapter seventeen, verse seventeen" in normalized_text
    assert "genesis chapter eighteen, verses one through fifteen" in normalized_text
    assert "genesis chapter twenty-one, verses one through seven" in normalized_text

def test_heading_and_scripture_normalization():
    """
    Tests that headings are correctly identified before scripture references are parsed.
    """
    title = "Heading and Scripture Test"
    text = """Romans 8
THE MESSIAH KING AND HIS BRIDAL GIFT
(ROM 8:1–16)
Therefore, there is now no condemnation for those who are in
Christ Jesus, because through Christ Jesus the law of the Spirit of
life set me free from the law of sin and death."""
    
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    
    # Verify heading is correctly formatted
    assert ". ... the messiah king and his bridal gift. ... " in normalized_text
    
    # Verify scripture is correctly expanded
    assert "romans chapter eight" in normalized_text
    assert "romans chapter eight, verses one through sixteen" in normalized_text
    
    # Verify prose is intact
    assert "therefore, there is now no condemnation" in normalized_text
    
    # Verify the heading was NOT consumed as a verse
    assert "verse the messiah king" not in normalized_text
