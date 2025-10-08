import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import shutil
import app as main_app
import io
from PIL import Image
from mutagen.mp4 import MP4, MP4Cover
import requests

# Import the logic function directly, not the Celery task
from app import _create_audiobook_logic

@pytest.fixture
def setup_test_files(tmp_path, monkeypatch):
    """A pytest fixture to create a temporary environment for testing."""
    temp_generated_dir = tmp_path / "generated"
    temp_generated_dir.mkdir()

    # Create dummy mp3 and txt files
    dummy_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter3_789.mp3"]
    for fname in dummy_files:
        mp3_path = temp_generated_dir / fname
        txt_path = temp_generated_dir / fname.replace('.mp3', '.txt')
        
        os.system(f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 1 -q:a 9 -acodec libmp3lame {mp3_path} >/dev/null 2>&1")
        txt_path.write_text(f"This is the text for {fname}")

    monkeypatch.setattr(main_app, 'GENERATED_FOLDER', str(temp_generated_dir))
    
    yield temp_generated_dir

@patch('app.subprocess.run')
@patch('app.MP3')
def test_audiobook_merging_handles_duplicates(mock_mp3, mock_subprocess, setup_test_files):
    """
    Ensures the audiobook logic de-duplicates the input file list.
    """
    mock_audio_info = MagicMock()
    mock_audio_info.info.length = 10.0
    mock_mp3.return_value = mock_audio_info
    
    input_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter1_123.mp3", "chapter3_789.mp3"]
    
    build_dir = setup_test_files / "audiobook_build_test"
    build_dir.mkdir()

    _create_audiobook_logic(
        file_list=input_files,
        audiobook_title_from_form="Test Audiobook",
        audiobook_author_from_form="Test Author",
        cover_url=None,
        build_dir=build_dir
    )

    concat_file_path = build_dir / "concat_list.txt"
    
    assert concat_file_path.exists()
    content = concat_file_path.read_text()
    lines = content.strip().split('\n')
    
    assert len(lines) == 3
    assert content.count("chapter1_123.mp3") == 1

@patch('app.requests.get')
def test_audiobook_creation_with_google_books_cover(mock_requests_get, setup_test_files):
    """
    Tests that a cover from Google Books API is downloaded, and the final m4b has it.
    """
    mock_cover_response = MagicMock()
    mock_cover_response.raise_for_status.return_value = None
    dummy_image = Image.new('RGB', (100, 100), color = 'red')
    img_byte_arr = io.BytesIO()
    dummy_image.save(img_byte_arr, format='JPEG')
    mock_cover_response.raw = io.BytesIO(img_byte_arr.getvalue())
    mock_requests_get.return_value = mock_cover_response
    
    input_files = ["chapter1_123.mp3"]
    build_dir = setup_test_files / "audiobook_build_test_google_cover"
    build_dir.mkdir()

    result = _create_audiobook_logic(
        file_list=input_files,
        audiobook_title_from_form="Test_Audiobook_Google_Cover",
        audiobook_author_from_form="Test Author",
        cover_url="http://fake-cover-url.com/cover.jpg",
        build_dir=build_dir
    )

    output_filepath = Path(setup_test_files) / result['filename']
    assert output_filepath.exists()
    audio = MP4(output_filepath)
    assert 'covr' in audio.tags
    assert isinstance(audio.tags['covr'][0], MP4Cover)

@patch('app.requests.get')
def test_audiobook_creation_with_generic_cover_fallback(mock_requests_get, setup_test_files):
    """
    Tests that a generic cover is created and used when Google Books API fails.
    """
    mock_requests_get.side_effect = requests.RequestException("Failed to download")

    input_files = ["chapter1_123.mp3"]
    build_dir = setup_test_files / "audiobook_build_test_generic_cover"
    build_dir.mkdir()

    result = _create_audiobook_logic(
        file_list=input_files,
        audiobook_title_from_form="Test_Audiobook_Generic_Cover",
        audiobook_author_from_form="Test Author",
        cover_url="http://fake-cover-url.com/cover.jpg",
        build_dir=build_dir
    )

    output_filepath = Path(setup_test_files) / result['filename']
    assert output_filepath.exists()
    audio = MP4(output_filepath)
    assert 'covr' in audio.tags
    assert isinstance(audio.tags['covr'][0], MP4Cover)
