import pytest
from unittest.mock import patch, MagicMock
from io import BytesIO
from app import app as flask_app
from pathlib import Path

# A word count higher than the threshold in app.py (8000)
LARGE_WORD_COUNT_TEXT = "word " * 9000
SMALL_WORD_COUNT_TEXT = "word " * 100

@pytest.fixture
def app():
    flask_app.config.update({
        "TESTING": True,
        "SECRET_KEY": "testing",
        "UPLOAD_FOLDER": "/tmp/pytest-uploads",
    })
    Path(flask_app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True, parents=True)
    yield flask_app

@pytest.fixture
def client(app):
    return app.test_client()

@patch('app.convert_to_speech_task.delay')
@patch('app.process_chapter_task.delay')
@patch('app.chapterizer.chapterize')
@patch('app.extract_text_and_metadata')
def test_large_file_auto_triggers_book_mode(
    mock_extract, mock_chapterize, mock_process_chapter, mock_convert_single, client
):
    """
    Tests that a large file automatically triggers the chapter-splitting (Book Mode) logic,
    even if the 'book_mode' checkbox is NOT checked.
    """
    mock_extract.return_value = (LARGE_WORD_COUNT_TEXT, {})
    mock_chapterize.return_value = [MagicMock(), MagicMock()] # Simulate finding 2 chapters

    file_data = {
        'file': (BytesIO(b"large file content"), 'large_book.txt'),
        'voice': 'en_US-hfc_male-medium.onnx',
        # Note: 'book_mode' is NOT included in the form data
    }
    
    client.post('/', data=file_data, content_type='multipart/form-data')
    
    # Assert that the chapterizer WAS called
    mock_chapterize.assert_called_once()
    # Assert that the chapter processing task was called (2 times for our 2 mock chapters)
    assert mock_process_chapter.call_count == 2
    # Assert that the single-file conversion task was NOT called
    mock_convert_single.assert_not_called()


@patch('app.convert_to_speech_task.delay')
@patch('app.process_chapter_task.delay')
@patch('app.chapterizer.chapterize')
@patch('app.extract_text_and_metadata')
def test_small_file_uses_single_file_mode(
    mock_extract, mock_chapterize, mock_process_chapter, mock_convert_single, client
):
    """
    Tests that a small file correctly uses the standard single-file conversion logic
    when 'book_mode' is not checked.
    """
    mock_extract.return_value = (SMALL_WORD_COUNT_TEXT, {})

    file_data = {
        'file': (BytesIO(b"small file content"), 'small_article.txt'),
        'voice': 'en_US-hfc_male-medium.onnx',
    }
    
    client.post('/', data=file_data, content_type='multipart/form-data')

    # Assert that the chapterizer was NOT called
    mock_chapterize.assert_not_called()
    # Assert that the chapter processing task was NOT called
    mock_process_chapter.assert_not_called()
    # Assert that the single-file conversion task WAS called
    mock_convert_single.assert_called_once()
