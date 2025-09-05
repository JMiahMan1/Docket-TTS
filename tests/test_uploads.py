import pytest
from unittest.mock import patch
from io import BytesIO
from app import app as flask_app # Import the Flask app instance
from ebooklib import epub # Import for creating a test epub file

@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    flask_app.config.update({
        "TESTING": True,
        "SECRET_KEY": "testing" # A secret key is needed to test flash messages
    })
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@patch('app.convert_to_speech_task.delay')
def test_single_file_upload(mock_task_delay, client):
    """
    Tests that uploading a single valid file creates one Celery task.
    """
    file_data = {
        'file': (BytesIO(b"this is a test"), 'test1.txt'),
        'voice': 'en_US-hfc_male-medium.onnx'
    }
    
    response = client.post('/', data=file_data, content_type='multipart/form-data')
    
    # Check that it redirects to the jobs page
    assert response.status_code == 302
    assert response.location == '/jobs'
    
    # Check that exactly one task was created
    assert mock_task_delay.call_count == 1

@patch('app.convert_to_speech_task.delay')
def test_multiple_file_upload(mock_task_delay, client):
    """
    Tests that uploading multiple valid files creates a task for each file.
    """
    file_data = {
        'file': [
            (BytesIO(b"first file"), 'test1.txt'),
            (BytesIO(b"second file"), 'test2.pdf'),
            (BytesIO(b"third file"), 'test3.docx')
        ],
        'voice': 'en_US-hfc_male-medium.onnx'
    }
    
    response = client.post('/', data=file_data, content_type='multipart/form-data')

    # Check for redirect to the jobs page
    assert response.status_code == 302
    assert response.location == '/jobs'

    # Check that three tasks were created
    assert mock_task_delay.call_count == 3

@patch('app.convert_to_speech_task.delay')
def test_epub_file_upload(mock_task_delay, client):
    """
    Tests that uploading a valid .epub file creates one Celery task.
    """
    # Create a valid EPUB file in memory
    book = epub.EpubBook()
    book.set_identifier('id123456')
    book.set_title('Test Book')
    book.set_language('en')
    c1 = epub.EpubHtml(title='Intro', file_name='chap_01.xhtml', lang='en')
    c1.content = u'<h1>Introduction</h1><p>This is a test book.</p>'
    book.add_item(c1)
    book.toc = (epub.Link('chap_01.xhtml', 'Introduction', 'intro'),)
    book.spine = ['nav', c1]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Write the EPUB to a BytesIO object
    epub_bytes = BytesIO()
    epub.write_epub(epub_bytes, book, {})
    epub_bytes.seek(0) # Rewind the stream to the beginning

    file_data = {
        'file': (epub_bytes, 'test.epub'),
        'voice': 'en_US-hfc_male-medium.onnx'
    }

    response = client.post('/', data=file_data, content_type='multipart/form-data')

    # Check for a successful redirect
    assert response.status_code == 302
    assert response.location == '/jobs'

    # Check that one task was created
    assert mock_task_delay.call_count == 1

@patch('app.convert_to_speech_task.delay')
def test_upload_invalid_extension(mock_task_delay, client):
    """
    Tests that uploading a file with a disallowed extension does not create a task.
    """
    file_data = {
        'file': (BytesIO(b"this is a zip file"), 'test.zip'),
        'voice': 'en_US-hfc_male-medium.onnx'
    }
    
    # Use `with client` to access session data for flash messages
    with client:
        response = client.post('/', data=file_data, content_type='multipart/form-data', follow_redirects=True)
        # Check that the user is redirected back to the upload page and sees an error
        assert response.status_code == 200
        assert b'Invalid file type' in response.data

    # Check that NO task was created
    assert mock_task_delay.call_count == 0

@patch('app.convert_to_speech_task.delay')
def test_upload_no_file_selected(mock_task_delay, client):
    """
    Tests that submitting the form without a file does not create a task.
    """
    with client:
        response = client.post('/', data={}, content_type='multipart/form-data', follow_redirects=True)
        assert response.status_code == 200
        assert b'No files selected' in response.data

    assert mock_task_delay.call_count == 0
