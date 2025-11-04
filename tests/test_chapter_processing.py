import pytest
from unittest.mock import patch, ANY
from io import BytesIO
from app import app as flask_app
from chapterizer import Chapter, chapterize
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
    Path(flask_app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True, parents=True)
    Path(flask_app.config['GENERATED_FOLDER']).mkdir(exist_ok=True, parents=True)
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@patch('app.process_chapter_task.delay')
@patch('app.chapterizer.chapterize')
@patch('app.fetch_enhanced_metadata') # Mock the new metadata fetching
@patch('app.extract_text_and_metadata')
def test_book_mode_upload_creates_multiple_jobs(
    mock_extract_text, mock_fetch_meta, mock_chapterize, mock_task_delay, client
):
    """
    Tests that uploading a file in 'Book Mode' correctly splits it into
    chapters and creates a separate Celery task for each one.
    """
    mock_chapters = [
        Chapter(number=1, title="The Beginning", original_title="Chapter 1", content="Once upon a time.", word_count=4, part_info=(1,1)),
        Chapter(number=2, title="The Middle", original_title="Chapter 2", content="Something happened.", word_count=2, part_info=(1,1)),
        Chapter(number=3, title="The End", original_title="Chapter 3", content="They lived happily.", word_count=3, part_info=(1,1)),
    ]
    mock_chapterize.return_value = mock_chapters
    
    mock_extract_text.return_value = ("dummy text", {'title': 'book', 'author': 'author'})
    mock_fetch_meta.return_value = {'title': 'Enhanced Book Title', 'author': 'Enhanced Author'}

    file_data = {
        'file': (BytesIO(b"this is a test book"), 'book.txt'),
        'voice': 'af_bella', # Added voice to prevent error
    }
    
    response = client.post('/', data=file_data, content_type='multipart/form-data')
    
    assert response.status_code == 302
    assert response.location == '/jobs'
    
    mock_chapterize.assert_called_once()
    
    assert mock_task_delay.call_count == 3
    
    first_call_args, _ = mock_task_delay.call_args_list[0]
    
    assert first_call_args[0] == "Once upon a time."
    assert first_call_args[1] == {'title': 'Enhanced Book Title', 'author': 'Enhanced Author'}
    assert first_call_args[2]['title'] == 'The Beginning'


def test_chapterizer_no_headings_avoids_nameerror(tmp_path):
    """
    Tests that chapterizing a document with no chapter headings
    correctly processes the file as one chapter and does not
    raise a NameError. This specifically tests the fix for the
    typo in _apply_final_processing.
    """
    # Create a dummy text file with content, but no chapter headings
    # Ensure it's long enough to pass the min_word_count filter
    content = "This is a test document. " * 50
    p = tmp_path / "test_doc.txt"
    p.write_text(content, encoding="utf-8")

    # Call the real chapterize function
    try:
        chapters = chapterize(filepath=str(p), text_content=content)
    except NameError as e:
        pytest.fail(f"chapterizer.py raised a NameError: {e}")

    # The function should find "Full Document" and process it
    assert len(chapters) == 1
    assert chapters[0].number == 1
    assert chapters[0].title == "Full Document"
    assert chapters[0].word_count > 100
