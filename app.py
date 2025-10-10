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
    
    # Create necessary directories
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)

    # Setup logging
    log_file = os.path.join(app.config['GENERATED_FOLDER'], 'app.log')
    handler = RotatingFileHandler(log_file, maxBytes=100000, backupCount=3)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    # Configure Flask's logger
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)

    # Add the same handler to Werkzeug's logger
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addHandler(handler)
    werkzeug_logger.setLevel(logging.INFO)

    return app

app = create_app()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        voice_name = request.form.get('voice')
        speed_rate = request.form.get('speed_rate', '1.0')
        debug_level = session.get('debug_level', 'off')

        if not voice_name:
            flash('Please select a voice.', 'error')
            return redirect(request.url)

        ensure_voice_available(voice_name)

        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            if file and allowed_file(file.filename):
                original_filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
                input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(input_filepath)
                
                task = process_book_task.delay(input_filepath, original_filename, voice_name, speed_rate, debug_level)
                return render_template('result.html', task_id=task.id)
            else:
                flash('Invalid file type. Please upload a txt, pdf, docx, or epub file.', 'error')

        elif 'text_input' in request.form and request.form['text_input'].strip() != '':
            text_content = request.form['text_input']
            text_title = request.form.get('text_title', 'pasted_text').strip()
            if not text_title:
                text_title = "pasted_text"

            original_filename = secure_filename(f"{text_title}.txt")
            unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            Path(input_filepath).write_text(text_content, encoding='utf-8')

            task = process_book_task.delay(input_filepath, original_filename, voice_name, speed_rate, debug_level)
            return render_template('result.html', task_id=task.id)

        else:
            flash('Please select a file or paste some text.', 'warning')
            return redirect(request.url)

    voices = get_piper_voices()
    return render_template('index.html', voices=voices)

@app.route('/files')
def list_files():
    processed_files = []
    generated_path = Path(app.config['GENERATED_FOLDER'])
    all_files = sorted(generated_path.iterdir(), key=os.path.getmtime, reverse=True)

    book_chapters = {}
    
    for f in all_files:
        if f.suffix.lower() in ['.mp3', '.m4b']:
            try:
                # Extract book title from filename like "Chapter_01_-_The_Book_Title.mp3"
                match = re.match(r".* - (.*)", f.stem)
                book_title = match.group(1).replace('_', ' ') if match else "Unknown"
                
                if book_title not in book_chapters:
                    book_chapters[book_title] = {'files': [], 'cover': None}
                
                txt_name = f.stem + ".txt"
                txt_path = generated_path / txt_name
                
                file_info = {
                    'name': f.name,
                    'txt_name': txt_name if txt_path.exists() else None,
                    'size': human_readable_size(f.stat().st_size),
                    'date': datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                    'book_title': book_title,
                }

                if f.suffix.lower() == '.mp3':
                    audio = MP3(f, ID3=ID3)
                    file_info['duration'] = f"{int(audio.info.length // 60)}:{int(audio.info.length % 60):02d}"
                    file_info['title'] = audio.get('TIT2', [f.stem])[0]
                    file_info['author'] = audio.get('TPE1', ['Unknown'])[0]
                    
                book_chapters[book_title]['files'].append(file_info)

            except Exception as e:
                app.logger.error(f"Error processing file {f}: {e}")
                
    # Sort chapters within each book
    for book in book_chapters.values():
        book['files'].sort(key=lambda x: x['name'])
    
    return render_template('files.html', book_chapters=book_chapters)
    
@app.route('/edit_text/<text_filename>', methods=['GET', 'POST'])
def edit_text_page(text_filename):
    """
    Handles both displaying the text for editing (GET) and reprocessing it
    to generate new audio (POST). This is the unified, correct function.
    """
    safe_filename = secure_filename(text_filename)
    filepath = Path(app.config['GENERATED_FOLDER']) / safe_filename

    if not filepath.is_file():
        flash(f"File not found: {safe_filename}", "error")
        return redirect(url_for('list_files'))

    if request.method == 'POST':
        new_content = request.form.get('edited_text')
        voice_name = request.form.get('voice')
        speed_rate = request.form.get('speed_rate', '1.0')
        debug_level = session.get('debug_level', 'off')

        if not new_content or not new_content.strip():
            flash("Content cannot be empty.", "error")
            return redirect(url_for('edit_text_page', text_filename=safe_filename))

        try:
            filepath.write_text(new_content, encoding='utf-8')
        except IOError as e:
            app.logger.error(f"Could not write to file {filepath}: {e}")
            flash(f"Error saving file: {e}", "error")
            return redirect(url_for('edit_text_page', text_filename=safe_filename))
        
        base_name = filepath.stem
        for old_audio_file in Path(app.config['GENERATED_FOLDER']).glob(f"{base_name}.mp*"):
            try:
                old_audio_file.unlink()
            except OSError as e:
                app.logger.error(f"Error deleting old audio file {old_audio_file}: {e}")

        temp_input_filename = f"edited_{uuid.uuid4().hex}.txt"
        temp_input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_input_filename)
        Path(temp_input_filepath).write_text(new_content, encoding='utf-8')

        # Calling the CORRECT, current Celery task
        task = process_book_task.delay(temp_input_filepath, safe_filename, voice_name, speed_rate, debug_level)
        flash(f"Started re-synthesis for '{safe_filename}'.", 'info')
        return render_template('result.html', task_id=task.id)

    try:
        content = filepath.read_text(encoding='utf-8')
        voices = get_piper_voices()
        
        parts = filepath.stem.split(' - ')
        book_title = parts[1].replace('_', ' ') if len(parts) > 1 else "Unknown Book"
        chapter_title = parts[0].replace('_', ' ') if len(parts) > 0 else filepath.stem

        return render_template('edit_text.html', 
                               text_content=content,
                               original_filename=safe_filename,
                               book_title=book_title,
                               chapter_title=chapter_title,
                               voices=voices)
    except Exception as e:
        app.logger.error(f"Error reading file for editing: {e}")
        flash("Could not read the text file for editing.", "error")
        return redirect(url_for('list_files'))

@app.route('/delete_bulk', methods=['POST'])
def delete_bulk():
    files_to_delete = request.form.getlist('delete_files')
    if not files_to_delete:
        flash("No files selected for deletion.", "warning")
        return redirect(url_for('list_files'))

    deleted_count = 0
    for filename in files_to_delete:
        safe_name = secure_filename(filename)
        try:
            # Delete audio file and its corresponding text file
            audio_path = Path(app.config['GENERATED_FOLDER']) / safe_name
            text_path = Path(app.config['GENERATED_FOLDER']) / f"{audio_path.stem}.txt"
            
            if audio_path.is_file():
                audio_path.unlink()
                deleted_count += 1
            if text_path.is_file():
                text_path.unlink()
                
        except Exception as e:
            app.logger.error(f"Error deleting file {safe_name}: {e}")
            flash(f"Error deleting {safe_name}.", "error")

    flash(f"Successfully deleted {deleted_count} file(s).", "success")
    return redirect(url_for('list_files'))

@app.route('/create_audiobook', methods=['POST'])
def create_audiobook():
    files_to_merge = request.form.getlist('files')
    title = request.form.get('title', 'Untitled Audiobook')
    author = request.form.get('author', 'Unknown Author')
    cover_url = request.form.get('cover_url', None)

    if not files_to_merge or len(files_to_merge) < 1:
        flash("Please select at least one MP3 file to merge.", "warning")
        return redirect(url_for('list_files'))

    task = create_audiobook_task.delay(files_to_merge, title, author, cover_url)
    return render_template('result.html', task_id=task.id, is_audiobook=True)

@app.route('/task_status/<task_id>')
def task_status(task_id):
    task = celery.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': 'PENDING', 'status': {'current': 0, 'total': 1, 'status': 'Waiting in queue...'}}
    elif task.state == 'PROGRESS':
        response = {'state': 'PROGRESS', 'status': task.info}
    elif task.state == 'SUCCESS':
        response = {'state': 'SUCCESS', 'status': task.info}
    else: # FAILURE, RETRY, etc.
        response = {'state': task.state, 'status': str(task.info)}
    return jsonify(response)

@app.route('/generated/<name>')
def download_file(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name)

@app.route('/health')
def health_check():
    """A simple health check endpoint."""
    return jsonify({"status": "healthy"}), 200

@app.route('/jobs')
def list_jobs():
    try:
        r = redis.Redis(host='redis', port=6379, db=0, decode_responses=True)
        active_tasks_raw = r.hgetall('celery-tasks')
        active_tasks = [celery.AsyncResult(task_id) for task_id in active_tasks_raw.keys()]
        
        jobs = []
        for task in active_tasks:
            info = task.info if isinstance(task.info, dict) else {}
            jobs.append({
                'id': task.id,
                'state': task.state,
                'status': info.get('status', '...'),
            })
        return render_template('jobs.html', jobs=jobs)
    except redis.exceptions.ConnectionError:
        flash("Could not connect to Redis to fetch job list.", "error")
        return render_template('jobs.html', jobs=[])

@app.route('/get-book-metadata', methods=['POST'])
def get_book_metadata():
    filename = request.form.get('filename')
    if not filename:
        return jsonify({'error': 'Filename not provided'}), 400
    
    # Simple extraction from filename for now
    clean_name = Path(filename).stem.replace('_', ' ').title()
    metadata = fetch_enhanced_metadata(clean_name, None)
    return jsonify(metadata)

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
    return render_template('debug.html', voices=voices, original_text=original_text, normalized_output=normalized_output, log_content=log_content, current_debug_level=current_debug_level)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

