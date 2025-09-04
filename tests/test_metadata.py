import pytest
from pathlib import Path
import base64
import requests
import os
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC

from app import tag_mp3_file, generate_placeholder_cover
from tts_service import TTSService

TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)

@pytest.fixture
def setup_test_mp3(tmp_path: Path) -> Path:
    """
    Dynamically creates a valid MP3 file by downloading the required voice model
    on-demand, ensuring the test is self-contained.
    """
    voice_file = "en_US-ryan-high.onnx"
    onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/{voice_file}"
    json_url = f"{onnx_url}.json"

    temp_voices_dir = tmp_path / "voices"
    temp_voices_dir.mkdir()

    try:
        r_onnx = requests.get(onnx_url)
        r_onnx.raise_for_status()
        (temp_voices_dir / voice_file).write_bytes(r_onnx.content)

        r_json = requests.get(json_url)
        r_json.raise_for_status()
        (temp_voices_dir / f"{voice_file}.json").write_bytes(r_json.content)
    except requests.RequestException as e:
        pytest.fail(f"Failed to download voice model for test: {e}")

    mp3_path = tmp_path / "test.mp3"
    test_text = "This is a test."
    original_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        tts = TTSService(voice=voice_file)
        tts.synthesize(test_text, str(mp3_path))
    except Exception as e:
        pytest.fail(f"Failed to generate test MP3 file using TTSService: {e}")
    finally:
        os.chdir(original_cwd)

    assert mp3_path.exists() and mp3_path.stat().st_size > 0
    yield mp3_path

def test_id3_tagging_with_cover_art(setup_test_mp3):
    mp3_path = setup_test_mp3
    metadata = {'title': 'My Test Audiobook', 'author': 'An Author'}
    image_data = TINY_PNG_BYTES

    tag_mp3_file(mp3_path, metadata, image_data)
    audio = MP3(mp3_path)

    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'My Test Audiobook'
    assert audio.tags['TPE1'].text[0] == 'An Author'
    assert audio.tags.getall('APIC')
    apic_frame = audio.tags.getall('APIC')[0]
    assert apic_frame.mime == 'image/png'
    assert apic_frame.data == TINY_PNG_BYTES

def test_id3_tagging_without_cover_art(setup_test_mp3):
    mp3_path = setup_test_mp3
    metadata = {'title': 'No Cover Art Book', 'author': 'Another Author'}

    tag_mp3_file(mp3_path, metadata, image_data=None)
    audio = MP3(mp3_path)
    
    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'No Cover Art Book'
    assert audio.tags['TPE1'].text[0] == 'Another Author'
    assert not audio.tags.getall('APIC')

def test_placeholder_cover_generation(setup_test_mp3):
    mp3_path = setup_test_mp3
    metadata = {'title': 'A Generated Cover', 'author': 'Pillow & Co.'}

    generated_image_data = generate_placeholder_cover(
        title=metadata['title'],
        author=metadata['author']
    )
    assert isinstance(generated_image_data, bytes)

    tag_mp3_file(mp3_path, metadata, generated_image_data)

    audio = MP3(mp3_path)
    assert audio.tags.getall('APIC')
    apic_frame = audio.tags.getall('APIC')[0]
    assert apic_frame.data == generated_image_data
