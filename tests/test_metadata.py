import pytest
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import APIC
from app import tag_mp3_file

# A minimal, valid 1x1 transparent PNG to use as test cover art
TINY_PNG_BYTES = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

@pytest.fixture
def setup_test_mp3(tmp_path):
    """Creates a dummy MP3 file in a temporary directory for testing."""
    mp3_path = tmp_path / "test.mp3"
    # Create a file with a minimal MP3 frame header to be recognized by mutagen
    # This represents a silent frame.
    mp3_path.write_bytes(b'\xff\xfb\x90\x04\x00\x00\x00\x00')
    return mp3_path

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

    # Assert standard metadata
    assert 'TIT2' in audio.tags
    assert audio.tags['TIT2'].text[0] == metadata['title']
    assert 'TPE1' in audio.tags
    assert audio.tags['TPE1'].text[0] == metadata['author']

    # Assert cover art metadata
    assert 'APIC:Cover' in audio.tags
    apic_frame = audio.tags['APIC:Cover']
    assert isinstance(apic_frame, APIC)
    assert apic_frame.mime == 'image/png'
    assert apic_frame.desc == 'Cover'
    assert apic_frame.data == image_data

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

    # Assert standard metadata
    assert audio.tags['TIT2'].text[0] == metadata['title']
    assert audio.tags['TPE1'].text[0] == metadata['author']

    # Assert that no APIC (picture) frame was added
    assert 'APIC:Cover' not in audio.tags
