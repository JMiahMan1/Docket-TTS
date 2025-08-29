import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import shutil
import app as main_app

# Mock the celery task decorator for unit testing
def mock_task(bind=True):
    def decorator(func):
        # Attach the original function to the mock's 'run' attribute for direct calling
        wrapper = MagicMock()
        wrapper.run = func
        return wrapper
    return decorator

# Apply the mock to the celery object before importing the task
patch('app.celery.task', mock_task).start()
from app import create_audiobook_task

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
def test_audiobook_merging_handles_duplicates(mock_mp3, mock_subprocess, setup_test_files, monkeypatch):
    """
    Ensures the audiobook task de-duplicates the input file list and uses a temporary directory.
    """
    monkeypatch.setattr(os, 'makedirs', lambda path, exist_ok=False: None)

    mock_audio_info = MagicMock()
    mock_audio_info.info.length = 10.0
    mock_mp3.return_value = mock_audio_info
    
    input_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter1_123.mp3", "chapter3_789.mp3"]
    
    # Create a mock for the 'self' task instance
    mock_self = MagicMock()
    # Provide the required task_id attribute
    mock_self.request.id = "test-task-id-123"
    # Also mock the update_state method itself to prevent it from running
    mock_self.update_state = MagicMock()

    # Call the task's 'run' method with the fully configured mock
    create_audiobook_task.run(
        mock_self,
        input_files,
        "Test Audiobook",
        "Test Author"
    )

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
