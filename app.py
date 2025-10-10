# /app/app.py

import os
import uuid
import re
from pathlib import Path
from datetime import datetime, timezone
import redis
from flask import (
    Flask, request, render_template, send_from_directory,
    flash, redirect, url_for, jsonify, session
)
from werkzeug.utils import secure_filename
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
import logging
from logging.handlers import RotatingFileHandler

from extensions import celery
from utils import (
    get_piper_voices, ensure_voice_available, human_readable_size, 
    allowed_file, fetch_enhanced_metadata
)
from tasks import process_book_task, create_audiobook_task
from tts_service import TTSService, normalize_text

APP_VERSION = "0.1.0" # Bumping version for refactor
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'

def create_app():
    """Application factory to create and configure the Flask app."""
    app = Flask(__name__)
    app.config.from_mapping(
        UPLOAD_FOLDER=UPLOAD_FOLDER,
        GENERATED_FOLDER=GENERATED_FOLDER,
        SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', 'a-secure-and-random-secret-key-for-dev')
    )

    # Initialize Celery
    celery.conf.update(app.config)
    class FlaskTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    celery.Task = FlaskTask
    app.celery = celery

    # Configure Logging
    try:
        if not app.debug and not app.testing:
            os.makedirs(GENERATED_FOLDER, exist_ok=True)
            log_file = os.path.join(GENERATED_FOLDER, 'app.log')
            file_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024 * 5, backupCount=5)
            file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
            file_handler.setLevel(logging.DEBUG)
            app.logger.addHandler(file_handler)
            app.logger.setLevel(logging.DEBUG)
            app.logger.info('Docket TTS startup')
    except PermissionError:
        app.logger.warning("Could not configure file logger due to a permission error.")
    
    # Connect to Redis
    try:
        app.redis_client = redis.from_url(celery.conf.broker_url)
    except Exception as e:
        app.logger.error(f"Could not create Redis client: {e}")
        app.redis_client = None

    # Ensure directories exist
    if os.environ.get('RUNNING_IN_DOCKER'):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(GENERATED_FOLDER, exist_ok=True)

    # Context Processor to inject app version
    @app.context_processor
    def inject_version():
        return dict(app_version=APP_VERSION)

    return app

app = create_app()

# --- FLASK ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        debug_level = session.get('debug_level', 'off')
        voice_name = request.form.get("voice")
        speed_rate = request.form.get("speed_rate", "1.0")
        
        text_input = request.form.get('text_input')
        if text_input and text_input.strip():
            book_title = request.form.get('text_title')
            if not book_title or not book_title.strip():
                flash('Title is required for pasted text.', 'error')
                return redirect(request.url)
            original_filename = f"{secure_filename(book_title.strip())}.txt"
            unique_internal_filename = f"{uuid.uuid4().hex}.txt"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
            Path(input_filepath).write_text(text_input, encoding='utf-8')
            task = process_book_task.delay(input_filepath, original_filename, voice_name, speed_rate, debug_level)
            return render_template('result.html', task_id=task.id)

        files = request.files.getlist('file')
        if not files or all(f.filename == '' for f in files):
            flash('No files selected.', 'error')
            return redirect(request.url)
        
        task_ids = []
        for file in files:
            if not file or not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}.")
                continue
            original_filename = secure_filename(file.filename)
            unique_internal_filename = f"{uuid.uuid4().hex}{Path(original_filename).suffix}"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
            file.save(input_filepath)
            app.logger.info(f"File uploaded: '{original_filename}' to '{input_filepath}'. Using session debug level: {debug_level}")
            task = process_book_task.delay(input_filepath, original_filename, voice_name, speed_rate, debug_level)
            task_ids.append(task.id)

        if task_ids:
            return render_template('result.html', task_id=task_ids[0])
        else:
            return redirect(request.url)

    voices = get_piper_voices()
    return render_template('index.html', voices=voices)

@app.route('/files')
def list_files():
    file_map = {}
    all_files = sorted(Path(app.config['GENERATED_FOLDER']).iterdir(), key=os.path.getmtime, reverse=True)
    for entry in all_files:
        if not entry.is_file() or entry.name.startswith(('sample_', 'cover_')): continue
        key = entry.stem
        file_data = file_map.setdefault(key, {})
        if entry.suffix in ['.mp3', '.m4b']:
            file_data['audio_name'] = entry.name
            file_data['size'] = human_readable_size(entry.stat().st_size)
            file_data['date'] = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat()
        elif entry.suffix == '.txt':
            file_data['txt_name'] = entry.name
    processed_files = []
    for key, data in file_map.items():
        if 'audio_name' not in data: continue
        data['base_name'] = key
        processed_files.append(data)
    return render_template('files.html', audio_files=processed_files)

@app.route('/get-book-metadata', methods=['POST'])
def get_book_metadata():
    filenames = request.json.get('filenames', [])
    if not filenames: return jsonify({'error': 'No filenames provided'}), 400
    first_mp3_path = Path(app.config['GENERATED_FOLDER']) / secure_filename(filenames[0])
    title_from_tags = "Unknown"; author_from_tags = "Unknown"
    try:
        audio_tags = MP3(first_mp3_path, ID3=ID3)
        title_from_tags = str(audio_tags.get('TALB', [title_from_tags])[0])
        author_from_tags = str(audio_tags.get('TPE1', [author_from_tags])[0])
    except Exception as e:
        app.logger.warning(f"Could not read tags from {first_mp3_path}, falling back to filename parsing. Reason: {e}")
        chapter_match = re.match(r'^\d+\s*-\s*(.*?)\s*-.*$', Path(filenames[0]).stem)
        if chapter_match: title_from_tags = chapter_match.group(1).replace('_', ' ').strip()
    final_title = title_from_tags; final_author = author_from_tags; cover_url = ''
    if final_title and final_title != "Unknown":
        try:
            enhanced_meta = fetch_enhanced_metadata(final_title, final_author)
            return jsonify(enhanced_meta)
        except Exception as e:
             app.logger.error(f"get_book_metadata failed during API call: {e}")
    return jsonify({'title': final_title, 'author': final_author, 'cover_url': cover_url})

@app.route('/create-audiobook', methods=['POST'])
def create_audiobook():
    files_to_merge = request.form.getlist('files_to_merge')
    audiobook_title = request.form.get('title', 'Untitled Audiobook')
    audiobook_author = request.form.get('author', 'Unknown Author')
    cover_url = request.form.get('cover_url', '')
    if not files_to_merge:
        flash("Please select at least one MP3 file.", "warning")
        return redirect(url_for('list_files'))
    task = create_audiobook_task.delay(files_to_merge, audiobook_title, audiobook_author, cover_url)
    return render_template('result.html', task_id=task.id)

@app.route('/jobs')
def jobs_page():
    running_jobs, queued_jobs = [], []
    unassigned_job_count = 0
    try:
        inspector = celery.control.inspect()
        active_tasks = inspector.active() or {}
        for worker, tasks in active_tasks.items():
            for task in tasks:
                original_filename = "N/A"
                if (task_args := task.get('args')) and isinstance(task_args, (list, tuple)):
                    task_name = task.get('name', '')
                    if 'process_chapter_task' in task_name and len(task_args) > 2:
                        original_filename = f"{task_args[1].get('title', 'Book')} - Ch. {task_args[2]['number']}"
                    elif 'process_book_task' in task_name and len(task_args) > 1:
                        original_filename = f"Book: {Path(task_args[1]).name}"
                    elif 'convert_to_speech_task' in task_name and len(task_args) > 1:
                        original_filename = f"Single File: {Path(task_args[1]).name}"
                running_jobs.append({'id': task['id'], 'name': original_filename, 'worker': worker})
        
        reserved_tasks = inspector.reserved() or {}
        for worker, tasks in reserved_tasks.items():
            for task in tasks:
                original_filename = "N/A"
                if (task_args := task.get('args')) and isinstance(task_args, (list, tuple)):
                    task_name = task.get('name', '')
                    if 'process_chapter_task' in task_name and len(task_args) > 2:
                        original_filename = f"{task_args[1].get('title', 'Book')} - Ch. {task_args[2]['number']}"
                    elif 'process_book_task' in task_name and len(task_args) > 1:
                        original_filename = f"Book: {Path(task_args[1]).name}"
                    elif 'convert_to_speech_task' in task_name and len(task_args) > 1:
                        original_filename = f"Single File: {Path(task_args[1]).name}"
                queued_jobs.append({'id': task['id'], 'name': original_filename, 'status': 'Reserved'})

        if app.redis_client:
            try:
                unassigned_job_count = app.redis_client.llen('celery')
            except Exception as e:
                app.logger.error(f"Could not get queue length from Redis: {e}")
    except Exception as e:
        app.logger.error(f"Could not inspect Celery/Redis: {e}")
        flash("Could not connect to the Celery worker or Redis.", "error")
    return render_template('jobs.html', running_jobs=running_jobs, waiting_jobs=queued_jobs, unassigned_job_count=unassigned_job_count)

@app.route('/cancel-job/<task_id>', methods=['POST'])
def cancel_job(task_id):
    if not task_id:
        flash('Invalid task ID.', 'error')
        return redirect(url_for('jobs_page'))
    celery.control.revoke(task_id, terminate=True, signal='SIGKILL')
    flash(f'Cancellation request sent for job {task_id}.', 'success')
    return redirect(url_for('jobs_page'))

@app.route('/delete-bulk', methods=['POST'])
def delete_bulk():
    basenames_to_delete = set(request.form.getlist('files_to_delete'))
    deleted_count = 0
    if not basenames_to_delete:
        flash("No files selected for deletion.", "warning")
        return redirect(url_for('list_files'))
    for base_name in basenames_to_delete:
        safe_base_name = secure_filename(base_name)
        for f in Path(app.config['GENERATED_FOLDER']).glob(f"{safe_base_name}*.*"):
            try:
                f.unlink()
                app.logger.info(f"Successfully deleted {f}")
                deleted_count += 1
            except OSError as e:
                app.logger.error(f"Error deleting file {f}: {e}")
    flash(f"Successfully deleted {deleted_count} file(s).", "success")
    return redirect(url_for('list_files'))

@app.route('/speak_sample/<voice_name>')
def speak_sample(voice_name):
    sample_text = "The Lord is my shepherd; I shall not want."
    speed_rate = request.args.get('speed', '1.0')
    try:
        full_voice_path = ensure_voice_available(voice_name, app.redis_client)
    except Exception as e:
        return f"Error preparing voice sample: {e}", 500
    safe_speed = str(speed_rate).replace('.', 'p')
    safe_voice_name = secure_filename(Path(voice_name).stem)
    filename = f"sample_{safe_voice_name}_speed_{safe_speed}.mp3"
    filepath = os.path.join(app.config["GENERATED_FOLDER"], filename)
    normalized_sample_text = normalize_text(sample_text)
    if not os.path.exists(filepath):
        try:
            tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
            tts.synthesize(normalized_sample_text, filepath)
        except Exception as e:
            return f"Error generating sample: {e}", 500
    return send_from_directory(app.config["GENERATED_FOLDER"], filename)

@app.route('/status/<task_id>')
def task_status(task_id):
    task = celery.AsyncResult(task_id)
    response = {'state': task.state, 'status': task.info or 'Waiting...'}
    return jsonify(response)

@app.route('/generated/<name>')
def download_file(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name)

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

@app.route('/debug', methods=['GET', 'POST'])
def debug_page():
    voices = get_piper_voices()
    normalized_output = ""; original_text = ""
    log_content = "Log file not found."
    log_file = os.path.join(app.config['GENERATED_FOLDER'], 'app.log')
    if request.method == 'POST':
        if 'debug_level' in request.form:
            new_level = request.form.get('debug_level')
            session['debug_level'] = new_level
            app.logger.info(f"Session debug level set to: '{new_level}'")
            flash(f"Debug level for this session has been set to '{new_level}'.", 'success')
            return redirect(url_for('debug_page'))
        elif 'text_to_normalize' in request.form:
            original_text = request.form.get('text_to_normalize', '')
            if original_text:
                debug_level = session.get('debug_level', 'off')
                normalized_output = normalize_text(original_text, debug_level=debug_level)
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            log_content = "".join(lines[-200:])
    except FileNotFoundError:
        app.logger.warning(f"Log file not found at {log_file} for debug page.")
    current_debug_level = session.get('debug_level', 'off')
    return render_template('debug.html', voices=voices, original_text=original_text, 
                           normalized_output=normalized_output, log_content=log_content, 
                           current_debug_level=current_debug_level)
