import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os
import shutil
import app as main_app

# Mock the celery task decorator for unit testing
def mock_task(bind=True):
    def decorator(func):
        # We need to access the original unbound function for testing
        if hasattr(func, 'run'):
             unbound_func = func.run
        else:
             unbound_func = func
        
        def wrapper(*args, **kwargs):
            mock_self = MagicMock()
            mock_self.request.id = "mock_task_id"
            mock_self.update_state = MagicMock()
            return unbound_func(mock_self, *args, **kwargs)
        
        # Store original function for direct access
        wrapper.unbound_func = unbound_func
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
    # Mock os.makedirs to prevent it from trying to write to '/app'
    monkeypatch.setattr(os, 'makedirs', lambda path, exist_ok=False: None)

    mock_audio_info = MagicMock()
    mock_audio_info.info.length = 10.0
    mock_mp3.return_value = mock_audio_info
    
    input_files = ["chapter1_123.mp3", "chapter2_456.mp3", "chapter1_123.mp3", "chapter3_789.mp3"]
    
    # We call the original, unbound function directly for unit testing
    create_audiobook_task.unbound_func(
        MagicMock(), # mock 'self'
        file_list=input_files,
        audiobook_title="Test Audiobook",
        audiobook_author="Test Author"
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
