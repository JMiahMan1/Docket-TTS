import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from app import convert_to_speech_task

@patch('app.TTSService')
@patch('app.ensure_voice_available')
@patch('app.extract_text_and_metadata')
@patch('app.text_cleaner.clean_text')
@patch('app.tag_mp3_file')
@patch('pathlib.Path.write_text')
@patch('app.fetch_enhanced_metadata')
def test_convert_to_speech_task_cleans_text(
    mock_fetch_meta, mock_write_text, mock_tag, mock_clean_text, mock_extract, mock_ensure_voice, mock_tts_service
):
    # --- Setup ---
    mock_extract.return_value = ("Sample text with Table of Contents", {})
    mock_clean_text.return_value = "Cleaned sample text"
    mock_fetch_meta.return_value = {}

    mock_tts_instance = MagicMock()
    mock_tts_instance.synthesize.return_value = ("output_path", "normalized_text")
    mock_tts_service.return_value = mock_tts_instance

    # FIX: Mock the task object correctly, including the request.id needed by the Celery backend
    mock_task = MagicMock()
    type(mock_task.request).id = PropertyMock(return_value='mock-task-id-123')

    # --- Execution ---
    # FIX: Call the task's .run() method directly, passing the mock_task as the 'self' argument
    convert_to_speech_task.run(
        mock_task,  # This becomes the 'self' argument for the bound task
        input_filepath="/path/to/dummy.txt",
        original_filename="dummy.txt",
        book_title="Dummy Title",
        book_author="Dummy Author",
        voice_name="dummy_voice",
        speed_rate="1.0"
    )

    # --- Verification ---
    mock_clean_text.assert_called_once_with("Sample text with Table of Contents")
    mock_tts_instance.synthesize.assert_called_once()
    call_args, _ = mock_tts_instance.synthesize.call_args
    synthesized_content = call_args[0]
    assert "Cleaned sample text" in synthesized_content
