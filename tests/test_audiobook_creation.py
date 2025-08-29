import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import shutil
import app as main_app

# Import the logic function directly, not the Celery task
from app import _create_audiobook_logic

@pytest.fixture
def setup_test_files(tmp_path, monkeypatch):
    """A pytest fixture to create a temporary environment for testing."""
    temp_generated_dir = tmp_path / "generated"
    temp_generated_dir.mkdir()

    dummy_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter3_789.mp3"]
    for fname in dummy_files:
        (temp_generated_dir / fname).touch()
        (temp_generated_dir / fname.replace('.mp3', '.txt')).touch()

    # Use monkeypatch to correctly override the constant inside the app module
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
    
    # Call the logic function directly, no Celery mocking needed
    _create_audiobook_logic(
        file_list=input_files,
        audiobook_title="Test Audiobook",
        audiobook_author="Test Author",
        cover_url=None
    )

    # Find the concat file in the temporary directory
    build_dir = next(setup_test_files.glob("audiobook_build_*"))
    concat_file_path = build_dir / "concat_list.txt"
    
    assert concat_file_path.exists()

    content = concat_file_path.read_text()
    lines = content.strip().split('\n')
    
    assert len(lines) == 3
    assert "chapter1_123.mp3" in content
    assert "chapter2_456.mp3" in content
    assert "chapter3_789.mp3" in content
    assert content.count("chapter1_123.mp3") == 1
