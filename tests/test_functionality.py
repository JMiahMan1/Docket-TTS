import os
import requests
import time
import pytest
import io
from mutagen.mp3 import MP3

BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")
POLL_INTERVAL = 5  # seconds
TIMEOUT = 600      # 10 minutes, increased for slower CI runners

def submit_and_poll_task(title, text_content, speed_rate="1.0"):
    """Helper function to submit a task and poll for its completion."""
    # Step 1: Submit the text for conversion
    payload = {
        'text_title': title,
        'text_input': text_content,
        'voice': 'en_US-hfc_male-medium.onnx', # Use a default voice for testing
        'speed_rate': speed_rate
    }
    submit_response = requests.post(f"{BASE_URL}/", data=payload)
    assert submit_response.status_code == 200, f"Failed to submit task. Status: {submit_response.status_code}"
    
    task_id_line = [line for line in submit_response.text.split('\n') if "const taskId = " in line]
    assert task_id_line, "Could not find task ID in the response."
    task_id = task_id_line[0].split('"')[1]
    assert task_id, "Task ID is empty."

    # Step 2: Poll the status endpoint until the task is complete
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
            # Wait and retry if the server is temporarily unavailable
            pass
            
        time.sleep(POLL_INTERVAL)
    
    pytest.fail(f"Task {task_id} timed out after {TIMEOUT} seconds.")

# --- Lightest Tests First ---

def test_year_pronunciation():
    """
    Tests the special normalization logic for pronouncing years.
    """
    title = "Year Pronunciation Test"
    text = "The text was published in 1984. A revision was made in the year 2005. The original manuscript from 999 AD is lost."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "nineteen eighty-four" in normalized_text
    assert "two thousand five" in normalized_text

def test_greek_transliteration():
    """
    Tests the transliteration of Greek words into English characters.
    """
    title = "Greek Transliteration Test"
    text = "The first word is άνομίαι anomiai. The second Greek word is ἁμαρτίαν 'amartiai."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "anomiai" in normalized_text
    assert "amartiai" in normalized_text

def test_latin_phrase_expansion():
    """
    Tests the expansion of common Latin abbreviations.
    """
    title = "Latin Phrase Test"
    text = "We must consider other factors, e.g., the historical context. This is different from the previous point, i.e., the textual context, cf. the primary sources."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "for example" in normalized_text
    assert "that is" in normalized_text

def test_roman_numeral_expansion():
    """
    Tests the expansion of Roman numerals.
    """
    title = "Roman Numeral Test"
    text = "The council in Acts XV was a pivotal moment. The events of chapter VI are also important, see section IV."
    normalized_text, _ = submit_and_poll_task(title, text)
    assert "Acts Roman Numeral fifteen" in normalized_text
    assert "chapter Roman Numeral six" in normalized_text

# --- Heavier Tests Last ---
def test_f_and_ff_suffixes():
    """
    Tests normalization of verse references with 'f' and 'ff' suffixes, including complex multi-book references.
    """
    title = "F and FF Suffix Test"
    text = "Paul discusses the sacrifice of Jesus (Rom 3:21ff), the Passover (1 Cor 5:7f), and the rebuilding period (Ezra 3:7ff.; Neh 4:1ff.)."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()

    assert "romans chapter three, verse twenty-one and following" in normalized_text
    assert "first corinthians chapter five, verse seven and the following verse" in normalized_text
    assert "ezra chapter three, verse seven and following" in normalized_text
    assert "nehemiah chapter four, verse one and following" in normalized_text

def test_partial_verses():
    """
    Tests normalization of partial verses like '19a' and '19b'.
    """
    title = "Partial Verse Test"
    text = "This term speaks of lawlessness [Rom 6:19a; 1 John 3:4], producing lawless deeds [Matt 13:41; Rom 6:19b; Heb 10:17]."
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "romans chapter six, verse nineteen a" in normalized_text
    assert "romans chapter six, verse nineteen b" in normalized_text

def test_multi_book_references():
    """
    Tests normalization of a string with multiple book, chapter, and verse references.
    """
    title = "Multi-Book Test"
    text = "many scholars believe both the Genesis narratives of the birth of Isaac (Gen 17:17; 18:1-15; 21:1-7) and the offering of Isaac as a sacrifice (Gen 22:15-17) show additional occasions"
    normalized_text, _ = submit_and_poll_task(title, text)
    normalized_text = normalized_text.lower()
    assert "genesis chapter seventeen, verse seventeen" in normalized_text
    assert "genesis chapter eighteen, verses one through fifteen" in normalized_text

def test_mp3_cover_art_embedding():
    """
    Tests that a generated MP3 file contains embedded cover art.
    """
    title = "MP3 Cover Art Test"
    text = "This is a simple test to ensure an image is embedded in the output MP3 file."
    _, mp3_filename = submit_and_poll_task(title, text)
    
    mp3_response = requests.get(f"{BASE_URL}/generated/{mp3_filename}")
    assert mp3_response.status_code == 200
    
    mp3_file = io.BytesIO(mp3_response.content)
    audio = MP3(mp3_file)

    apic_tag_found = any(key.startswith('APIC:') for key in audio.tags)
    assert apic_tag_found, "APIC (cover art) tag not found in the MP3 file."

    cover_art_tag = next((tag for key, tag in audio.tags.items() if key.startswith('APIC:')), None)
    assert cover_art_tag is not None and len(cover_art_tag.data) > 0, "Cover art data is empty."

def get_mp3_duration(mp3_filename):
    """Downloads an MP3 and returns its duration in seconds."""
    mp3_response = requests.get(f"{BASE_URL}/generated/{mp3_filename}")
    assert mp3_response.status_code == 200
    mp3_file = io.BytesIO(mp3_response.content)
    audio = MP3(mp3_file)
    return audio.info.length

def test_speech_speed_rate():
    """Tests that changing speech rate affects audio duration."""
    title = "Speech Speed Test"
    text = "This is a simple sentence used to test the duration of an audio file."

    _, slow_mp3 = submit_and_poll_task(title, text, speed_rate="1.3") # Slower
    _, normal_mp3 = submit_and_poll_task(title, text, speed_rate="1.0") # Normal
    _, fast_mp3 = submit_and_poll_task(title, text, speed_rate="0.7") # Faster

    slow_duration = get_mp3_duration(slow_mp3)
    normal_duration = get_mp3_duration(normal_mp3)
    fast_duration = get_mp3_duration(fast_mp3)

    assert slow_duration > normal_duration
    assert normal_duration > fast_duration
