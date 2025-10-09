import pytest
from unittest.mock import patch, MagicMock
from app import convert_to_speech_task

@patch('app.TTSService')
@patch('app.ensure_voice_available')
@patch('app.extract_text_and_metadata')
@patch('app.text_cleaner.clean_text')
@patch('app.tag_mp3_file')
@patch('pathlib.Path.write_text')
@patch('app.fetch_enhanced_metadata') # Mock the new function
def test_convert_to_speech_task_cleans_text(
    mock_fetch_meta, mock_write_text, mock_tag, mock_clean_text, mock_extract, mock_ensure_voice, mock_tts_service
):
    # --- Setup ---
    mock_extract.return_value = ("Sample text with Table of Contents", {})
    mock_clean_text.return_value = "Cleaned sample text"
    mock_fetch_meta.return_value = {} # Return empty dict for metadata

    mock_tts_instance = MagicMock()
    mock_tts_instance.synthesize.return_value = ("output_path", "normalized_text")
    mock_tts_service.return_value = mock_tts_instance

    mock_task = MagicMock()
    mock_task.update_state.return_value = None

    # --- Execution ---
    # Call the task with the correct positional arguments
    convert_to_speech_task(
        input_filepath="/path/to/dummy.txt",
        original_filename="dummy.txt",
        book_title="Dummy Title",
        book_author="Dummy Author",
        voice_name="dummy_voice",
        speed_rate="1.0"
    )

    # --- Verification ---
    # Verify that clean_text was called with the initial text content
    mock_clean_text.assert_called_once_with("Sample text with Table of Contents")

    # Verify that the TTS service's synthesize method was called with the cleaned text
    # (plus any title page text, which is empty in this mock)
    mock_tts_instance.synthesize.assert_called_once()
    call_args, _ = mock_tts_instance.synthesize.call_args
    synthesized_content = call_args[0]
    assert "Cleaned sample text" in synthesized_content
