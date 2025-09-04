import pytest
from pathlib import Path
import base64
import requests
import os
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC

# Import the application code to be tested
from app import tag_mp3_file
from tts_service import TTSService

# Base64 encoded 1x1 transparent PNG to be used as cover art
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)

@pytest.fixture
def setup_test_mp3(tmp_path: Path) -> Path:
    """
    A pytest fixture that dynamically creates a valid, playable MP3 file.
    It downloads the required voice model on-demand to ensure the test is self-contained.
    """
    # 1. Define voice model URLs (from Dockerfile)
    voice_file = "en_US-hfc_male-medium.onnx"
    onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/{voice_file}"
    json_url = f"{onnx_url}.json"

    # 2. Create a temporary voices directory within the test's temp path
    temp_voices_dir = tmp_path / "voices"
    temp_voices_dir.mkdir()

    # 3. Download the voice model files into the temporary directory
    try:
        r_onnx = requests.get(onnx_url)
        r_onnx.raise_for_status()
        (temp_voices_dir / voice_file).write_bytes(r_onnx.content)

        r_json = requests.get(json_url)
        r_json.raise_for_status()
        (temp_voices_dir / f"{voice_file}.json").write_bytes(r_json.content)
    except requests.RequestException as e:
        pytest.fail(f"Failed to download voice model for test: {e}")

    # 4. Generate the MP3 from within the temporary directory context
    mp3_path = tmp_path / "test.mp3"
    test_text = "This is a test of the text to speech system."
    original_cwd = Path.cwd()
    try:
        # Temporarily change the working directory so TTSService finds the 'voices' folder
        os.chdir(tmp_path)
        tts = TTSService()
        tts.synthesize(test_text, str(mp3_path))
    except Exception as e:
        pytest.fail(f"Failed to generate test MP3 file using TTSService: {e}")
    finally:
        # Always change back to the original directory to not affect other tests
        os.chdir(original_cwd)

    assert mp3_path.exists() and mp3_path.stat().st_size > 0, "TTS service failed to create a valid MP3 file for the test."
    
    yield mp3_path

def test_id3_tagging_with_cover_art(setup_test_mp3):
    """
    Tests that tag_mp3_file correctly embeds title, author, and cover art image data.
    """
    mp3_path = setup_test_mp3
    metadata = {
        'title': 'My Test Audiobook',
        'author': 'An Author'
    }
    image_data = TINY_PNG_BYTES

    tag_mp3_file(mp3_path, metadata, image_data)
    audio = MP3(mp3_path)

    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'My Test Audiobook'
    assert audio.tags['TPE1'].text[0] == 'An Author'
    assert 'APIC:' in audio.tags
    apic_frame = audio.tags['APIC:']
    assert apic_frame.mime == 'image/png'
    assert apic_frame.data == TINY_PNG_BYTES

def test_id3_tagging_without_cover_art(setup_test_mp3):
    """
    Tests that tag_mp3_file works correctly when no image data is provided.
    """
    mp3_path = setup_test_mp3
    metadata = {
        'title': 'No Cover Art Book',
        'author': 'Another Author'
    }

    tag_mp3_file(mp3_path, metadata, image_data=None)
    audio = MP3(mp3_path)
    
    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'No Cover Art Book'
    assert audio.tags['TPE1'].text[0] == 'Another Author'
    assert 'APIC:' not in audio.tags
