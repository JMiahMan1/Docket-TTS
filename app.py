import os
import subprocess
import uuid
from pathlib import Path
from flask import (
    Flask, request, render_template, send_from_directory,
    flash, redirect, url_for, jsonify
)
from werkzeug.utils import secure_filename
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from celery import Celery, Task

from tts_service import TTSService

# --- Configuration ---
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'
VOICES_FOLDER = '/app/voices'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'}

# --- Flask App Initialization ---
app = Flask(__name__)
app.config.from_mapping(
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    GENERATED_FOLDER=GENERATED_FOLDER,
    SECRET_KEY='a-secure-and-random-secret-key'
)

# --- Celery Configuration ---
def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object("celery_config")
    return celery_app

celery = celery_init_app(app)

# --- Ensure Directories Exist ---
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)


# --- Helper Functions ---
def allowed_file(filename):
    """Check if the uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(filepath):
    """Extracts text content from various file types."""
    extension = Path(filepath).suffix.lower()
    text = ""
    try:
        if extension == '.pdf':
            # Use poppler-utils to extract text from PDF
            result = subprocess.run(
                ['pdftotext', filepath, '-'], 
                capture_output=True, text=True, check=True
            )
            text = result.stdout
        elif extension == '.docx':
            doc = docx.Document(filepath)
            text = "\n".join([para.text for para in doc.paragraphs])
        elif extension == '.epub':
            book = epub.read_epub(filepath)
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                text += soup.get_text() + "\n\n"
        elif extension == '.txt':
            text = Path(filepath).read_text(encoding='utf-8')
    except Exception as e:
        app.logger.error(f"Error extracting text from {filepath}: {e}")
        return None
    return text

def list_available_voices():
    """Scans the voices folder and returns a list of available Piper models."""
    voices = []
    voice_dir = Path(VOICES_FOLDER)
    if voice_dir.is_dir():
        for voice_file in voice_dir.glob("*.onnx"):
            # The name is the filename without the extension
            voices.append({"id": voice_file.name, "name": voice_file.stem})
    return sorted(voices, key=lambda v: v['name'])


# --- Celery Background Task ---
@celery.task
def convert_to_speech_task(input_filepath, original_filename, voice_name=None):
    """Background task that handles text extraction, normalization, and TTS conversion."""
    text_content = extract_text(input_filepath)
    if not text_content:
        return {'status': 'Error', 'message': 'Could not extract text from the uploaded file.'}

    unique_id = str(uuid.uuid4().hex[:8])
    base_name = Path(original_filename).stem
    output_filename = f"{base_name}_{unique_id}.mp3"
    output_filepath = os.path.join(GENERATED_FOLDER, output_filename)

    try:
        # Initialize the TTS service with the selected voice
        tts = TTSService(voice=voice_name)
        # Synthesize audio and get the normalized text back
        _, normalized_text = tts.synthesize(text_content, output_filepath)

        # Save the processed text for user reference
        text_filename = f"{base_name}_{unique_id}.txt"
        text_filepath = os.path.join(GENERATED_FOLDER, text_filename)
        Path(text_filepath).write_text(normalized_text, encoding="utf-8")

        return {
            'status': 'Success',
            'filename': output_filename,
            'textfile': text_filename
        }
    except Exception as e:
        app.logger.error(f"TTS Conversion failed: {e}")
        return {'status': 'Error', 'message': str(e)}


# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part in the request.', 'error')
            return redirect(request.url)
            
        file = request.files['file']
        if file.filename == '' or not allowed_file(file.filename):
            flash('Invalid file. Please select a TXT, DOCX, EPUB, or PDF file.', 'error')
            return redirect(request.url)

        original_filename = secure_filename(file.filename)
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
        file.save(input_filepath)

        voice_name = request.form.get("voice")
        task = convert_to_speech_task.delay(input_filepath, original_filename, voice_name)
        
        return render_template('result.html', task_id=task.id)

    voices = list_available_voices()
    return render_template('index.html', voices=voices)

@app.route('/speak_sample/<voice_name>')
def speak_sample(voice_name):
    """Generates a short audio sample for the selected voice."""
    sample_text = "This is a sample of my voice."
    filename = f"sample_{Path(voice_name).stem}.mp3"
    filepath = os.path.join(GENERATED_FOLDER, filename)
    
    # Generate a new sample only if it doesn't already exist
    if not os.path.exists(filepath):
        try:
            tts = TTSService(voice=voice_name)
            tts.synthesize(sample_text, filepath)
        except Exception as e:
            return f"Error generating sample: {e}", 500
            
    return send_from_directory(app.config["GENERATED_FOLDER"], filename)

@app.route('/status/<task_id>')
def task_status(task_id):
    """Reports the status of a background task."""
    task = celery.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': 'PENDING', 'status': 'Waiting for the worker to start...'}
    elif task.state != 'FAILURE':
        response = {'state': task.state, 'status': task.info}
    else:
        response = {'state': task.state, 'status': str(task.info)}
    return jsonify(response)

@app.route('/generated/<name>')
def download_file(name):
    """Serves the generated audio or text file for download."""
    return send_from_directory(app.config["GENERATED_FOLDER"], name)
