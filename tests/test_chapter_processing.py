import pytest
from unittest.mock import patch, ANY
from io import BytesIO
from app import app as flask_app
from chapterizer import Chapter
from pathlib import Path

@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    flask_app.config.update({
        "TESTING": True,
        "SECRET_KEY": "testing",
        "UPLOAD_FOLDER": "/tmp/pytest-uploads",
        "GENERATED_FOLDER": "/tmp/pytest-generated"
    })
    # Ensure test directories exist
    Path(flask_app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True, parents=True)
    Path(flask_app.config['GENERATED_FOLDER']).mkdir(exist_ok=True, parents=True)
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@patch('app.process_chapter_task.delay')
@patch('app.chapterizer.chapterize')
@patch('app.extract_text_and_metadata')
def test_book_mode_upload_creates_multiple_jobs(
    mock_extract_text, mock_chapterize, mock_task_delay, client
):
    """
    Tests that uploading a file in 'Book Mode' correctly splits it into
    chapters and creates a separate Celery task for each one.
    """
    # 1. Mock the chapterizer to return a predictable list of chapters
    mock_chapters = [
        Chapter(number=1, title="The Beginning", content="Once upon a time.", word_count=4),
        Chapter(number=2, title="The Middle", content="Something happened.", word_count=2),
        Chapter(number=3, title="The End", content="They lived happily.", word_count=3),
    ]
    mock_chapterize.return_value = mock_chapters
    
    # Mock text extraction; return a dummy title
    mock_extract_text.return_value = ("dummy text", {'title': 'book'})

    # 2. Prepare the form data for a file upload with the 'book_mode' flag
    file_data = {
        'file': (BytesIO(b"this is a test book"), 'book.txt'),
        'voice': 'en_US-hfc_male-medium.onnx',
        'book_mode': 'true' # This simulates checking the checkbox
    }
    
    # 3. Make the POST request to the upload endpoint
    response = client.post('/', data=file_data, content_type='multipart/form-data')
    
    # 4. Assert the results
    # It should redirect to the jobs page
    assert response.status_code == 302
    assert response.location == '/jobs'
    
    # The chapterizer should have been called once
    mock_chapterize.assert_called_once()
    
    # The key assertion: one task should be created for each chapter
    assert mock_task_delay.call_count == 3
    
    # --- UPDATED ASSERTIONS ---
    # Check that the arguments for the first task call are correct based on the app.py signature:
    # process_chapter_task.delay(chapter.content, book_title, chapter_details, voice_name, speed_rate)
    first_call_args, _ = mock_task_delay.call_args_list[0]
    
    assert first_call_args[0] == "Once upon a time."  # chapter.content
    assert first_call_args[1] == "book"               # book_title
    assert first_call_args[2] == {'number': 1, 'title': 'The Beginning'} # chapter_details dict
    assert first_call_args[3] == 'en_US-hfc_male-medium.onnx' # voice_name
    assert first_call_args[4] == '1.0'                # speed_rate
