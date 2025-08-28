import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import shutil

# Mock the celery task decorator and the main app object for unit testing
def mock_task(bind=True):
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Create a mock 'self' object if bind=True
            mock_self = MagicMock()
            mock_self.request.id = "mock_task_id"
            mock_self.update_state = MagicMock()
            return func(mock_self, *args, **kwargs)
        return wrapper
    return decorator

# Apply the mock to the celery object before importing the task
patch('app.celery.task', mock_task).start()
from app import create_audiobook_task, GENERATED_FOLDER

@pytest.fixture
def setup_test_files(tmp_path):
    """A pytest fixture to create a temporary environment for testing."""
    # Create a temporary 'generated' directory
    temp_generated_dir = tmp_path / "generated"
    temp_generated_dir.mkdir()

    # Create some dummy MP3 and TXT files
    dummy_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter3_789.mp3"]
    for fname in dummy_files:
        (temp_generated_dir / fname).touch()
        (temp_generated_dir / fname.replace('.mp3', '.txt')).touch()

    # Temporarily override the app's GENERATED_FOLDER to our temp dir
    original_folder = GENERATED_FOLDER
    globals()['GENERATED_FOLDER'] = str(temp_generated_dir)
    
    yield temp_generated_dir # This is where the test runs

    # Teardown: restore the original folder path
    globals()['GENERATED_FOLDER'] = original_folder


@patch('app.subprocess.run')
@patch('app.MP3')
def test_audiobook_merging_handles_duplicates(mock_mp3, mock_subprocess, setup_test_files):
    """
    Ensures the audiobook task de-duplicates the input file list before processing.
    """
    # Mock mutagen.MP3 to return a fake audio file length
    mock_audio_info = MagicMock()
    mock_audio_info.info.length = 10.0  # 10 seconds
    mock_mp3.return_value = mock_audio_info
    
    # The input list contains a duplicate entry for chapter1
    input_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter1_123.mp3", "chapter3_789.mp3"]
    
    # Run the audiobook creation task logic
    create_audiobook_task(
        file_list=input_files,
        audiobook_title="Test Audiobook",
        audiobook_author="Test Author"
    )

    # Find the generated build directory
    build_dir = next(setup_test_files.glob("audiobook_build_*"))
    concat_file_path = build_dir / "concat_list.txt"
    
    # Verify the concat file exists
    assert concat_file_path.exists()

    # Read the content and verify that chapter1 appears only once
    content = concat_file_path.read_text()
    lines = content.strip().split('\n')
    
    # There should be 3 unique files in the list
    assert len(lines) == 3
    assert "chapter1_123.mp3" in content
    assert "chapter2_456.mp3" in content
    assert "chapter3_789.mp3" in content
    assert content.count("chapter1_123.mp3") == 1
