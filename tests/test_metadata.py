import pytest
from pathlib import Path
import base64
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC

# Assuming app.py is in the root directory and contains tag_mp3_file
from app import tag_mp3_file

# Base64 encoded 1x1 transparent PNG to be used as cover art
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)

# Base64 encoded silent 1-second MP3. This is a structurally valid MP3 file.
SILENT_MP3_B64 = "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4LjQ1LjEwMAAAAAAAAAAAAAAA//tAwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
SILENT_MP3_BYTES = base64.b64decode(SILENT_MP3_B64)

@pytest.fixture
def setup_test_mp3(tmp_path: Path) -> Path:
    """
    A pytest fixture that creates a valid, silent MP3 file in a temporary directory
    for testing the tagging functionality.
    """
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(SILENT_MP3_BYTES)
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

    # Call the function to tag the file
    tag_mp3_file(mp3_path, metadata, image_data)

    # Read the tags back from the file
    audio = MP3(mp3_path)

    # Assert that the tags were written correctly
    assert audio is not None
    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'My Test Audiobook'
    assert audio.tags['TPE1'].text[0] == 'An Author'
    
    # Verify that the cover art was embedded
    assert 'APIC:' in audio.tags
    apic_frame = audio.tags['APIC:']
    assert isinstance(apic_frame, APIC)
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

    # Call the function without image data
    tag_mp3_file(mp3_path, metadata, image_data=None)

    # Read the tags back from the file
    audio = MP3(mp3_path)
    
    # Assert that the tags were written correctly
    assert audio is not None
    assert audio.tags is not None
    assert audio.tags['TIT2'].text[0] == 'No Cover Art Book'
    assert audio.tags['TPE1'].text[0] == 'Another Author'

    # Assert that no cover art was embedded
    assert 'APIC:' not in audio.tags
