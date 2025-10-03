from unittest.mock import patch, MagicMock
import pytest
from app import convert_to_speech_task
import text_cleaner

# This test ensures that the text_cleaner is being called in the single-file conversion task.
@patch('app.TTSService')
@patch('app.ensure_voice_available')
@patch('app.extract_text_and_metadata')
@patch('app.text_cleaner.clean_text')
@patch('app.tag_mp3_file')
@patch('pathlib.Path.write_text')
def test_convert_to_speech_task_cleans_text(
    mock_write_text, mock_tag, mock_clean_text, mock_extract, mock_ensure_voice, mock_tts_service
):
    # --- Setup ---
    # Mock the return values of functions to isolate the one we are testing
    mock_extract.return_value = ("Sample text with Table of Contents", {})
    mock_clean_text.return_value = "Cleaned sample text"
    
    # Mock the TTS service to avoid actual audio generation
    mock_tts_instance = MagicMock()
    mock_tts_instance.synthesize.return_value = ("output_path", "normalized_text")
    mock_tts_service.return_value = mock_tts_instance
    
    # Create a mock Celery task object to pass 'self'
    mock_task = MagicMock()
    mock_task.update_state.return_value = None

    # --- Execution ---
    # Run the task synchronously for testing
    convert_to_speech_task.run(
        self=mock_task,
        input_filepath="/path/to/dummy.txt",
        original_filename="dummy.txt"
    )

    # --- Assertions ---
    # Check that text was extracted
    mock_extract.assert_called_once()
    
    # The most important check: was the text cleaner called with the extracted text?
    mock_clean_text.assert_called_once_with("Sample text with Table of Contents")
    
    # Check that the TTS service was called with the *cleaned* text
    mock_tts_instance.synthesize.assert_called_once_with("Cleaned sample text", "dummy_unique_id.mp3")
