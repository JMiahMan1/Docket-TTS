import os
import subprocess
import uuid
from flask import Flask, request, render_template, render_template_string, send_from_directory, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from celery import Celery, Task

# --- Configuration ---
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'}
MODEL_PATH = '/app/en_US-hfc_male-medium.onnx'

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
    # (Text extraction functions remain the same as before)
    # ...
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


# --- Celery Background Task ---
@celery.task
def convert_to_speech_task(input_filepath, original_filename):
    """This function runs in the background, handled by a Celery worker."""
    text_content = extract_text(input_filepath)
    if not text_content:
        # Return an error state
        return {'status': 'Error', 'message': 'Could not extract text from file.'}

    unique_id = str(uuid.uuid4())
    output_filename = f"{os.path.splitext(original_filename)[0]}_{unique_id}.mp3"
    output_filepath = os.path.join(GENERATED_FOLDER, output_filename)

    try:
        command = (
            f"piper --model {MODEL_PATH} --output-raw | "
            f"ffmpeg -f s16le -ar 22050 -ac 1 -i - -f mp3 -q:a 0 {output_filepath}"
        )
        subprocess.run(
            command, shell=True, input=text_content.encode('utf-8'),
            check=True, stderr=subprocess.PIPE
        )
        # Return the successful result
        return {'status': 'Success', 'filename': output_filename}
    except subprocess.CalledProcessError as e:
        return {'status': 'Error', 'message': e.stderr.decode()}
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

        # Start the background task
        task = convert_to_speech_task.delay(input_filepath, original_filename)
        
        # Redirect to a results page with the task ID
        return redirect(url_for('task_result', task_id=task.id))

    return render_template('index.html')

@app.route('/result/<task_id>')
def task_result(task_id):
    """Renders the page that will poll for the task's status."""
    return render_template('result.html', task_id=task_id)

@app.route('/status/<task_id>')
def task_status(task_id):
    """Provides the status of a background task to the frontend."""
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
