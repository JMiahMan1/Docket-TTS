import os
import subprocess
import uuid
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

# Import new TTS service
from tts_service import TTSService, normalize_text
import pyttsx3

# --- Configuration ---
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'}

# --- Flask App Initialization ---
app = Flask(__name__)
app.config.from_mapping(
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    GENERATED_FOLDER=GENERATED_FOLDER,
    SECRET_KEY='supersecretkey_for_flash_messages'
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
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text(filepath):
    extension = filepath.rsplit('.', 1)[1].lower()
    text = ""
    try:
        if extension == 'pdf':
            result = subprocess.run(['pdftotext', filepath, '-'], capture_output=True, text=True, check=True)
            text = result.stdout
        elif extension == 'docx':
            doc = docx.Document(filepath)
            text = "\n".join([para.text for para in doc.paragraphs])
        elif extension == 'epub':
            book = epub.read_epub(filepath)
            text_parts = []
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                text_parts.append(soup.get_text())
            text = "\n".join(text_parts)
        elif extension == 'txt':
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
    except Exception as e:
        print(f"Error extracting text from {filepath}: {e}")
        return None
    return text


def list_available_voices():
    """Return a list of available voices from pyttsx3."""
    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    return [{"id": v.id, "name": v.name} for v in voices]


# --- Celery Background Task ---
@celery.task
def convert_to_speech_task(input_filepath, original_filename, voice_id=None):
    """Background task for TTS conversion."""
    text_content = extract_text(input_filepath)
    if not text_content:
        return {'status': 'Error', 'message': 'Could not extract text from file.'}

    unique_id = str(uuid.uuid4())
    base_name = os.path.splitext(original_filename)[0]
    output_filename = f"{base_name}_{unique_id}.mp3"
    output_filepath = os.path.join(GENERATED_FOLDER, output_filename)

    try:
        tts = TTSService(voice=voice_id)
        normalized_text = normalize_text(text_content)
        tts.synthesize(normalized_text, output_filepath)

        # Save normalized text alongside MP3
        text_filename = f"{base_name}_{unique_id}.txt"
        text_filepath = os.path.join(GENERATED_FOLDER, text_filename)
        with open(text_filepath, "w", encoding="utf-8") as f:
            f.write(normalized_text)

        return {
            'status': 'Success',
            'filename': output_filename,
            'textfile': text_filename,
            'normalized_text': normalized_text
        }
    except Exception as e:
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
            flash('Invalid file. Please select a txt, docx, epub, or pdf.', 'error')
            return redirect(request.url)

        original_filename = secure_filename(file.filename)
        input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
        file.save(input_filepath)

        # Get selected voice
        voice_id = request.form.get("voice") or None

        # Start background task
        task = convert_to_speech_task.delay(input_filepath, original_filename, voice_id)
        return redirect(url_for('task_result', task_id=task.id))

    # Show upload page with available voices
    voices = list_available_voices()
    return render_template('index.html', voices=voices)

@app.route('/speak_sample/<voice_id>')
def speak_sample(voice_id):
    """Generate a short sample for the selected voice."""
    sample_text = "This is a sample of my voice."
    unique_id = str(uuid.uuid4())
    filename = f"sample_{unique_id}.mp3"
    filepath = os.path.join(GENERATED_FOLDER, filename)

    try:
        tts = TTSService(voice=voice_id)
        tts.synthesize(sample_text, filepath)
        return send_from_directory(app.config["GENERATED_FOLDER"], filename)
    except Exception as e:
        return f"Error generating sample: {e}", 500

@app.route('/result/<task_id>')
def task_result(task_id):
    return render_template('result.html', task_id=task_id)


@app.route('/status/<task_id>')
def task_status(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': 'PENDING', 'status': 'Waiting for worker...'}
    elif task.state != 'FAILURE':
        response = {'state': task.state, 'status': task.info}
    else:
        response = {'state': task.state, 'status': str(task.info)}
    return jsonify(response)


@app.route('/generated/<name>')
def download_file(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name)


@app.route('/generated_text/<name>')
def download_text(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name, mimetype="text/plain")
