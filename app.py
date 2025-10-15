# /app/app.py

import os
import subprocess
import uuid
import re
from pathlib import Path
from datetime import datetime, timezone
import time
import redis
from flask import (
    Flask, request, render_template, send_from_directory,
    flash, redirect, url_for, jsonify, current_app, session
)
from werkzeug.utils import secure_filename
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from celery import Celery, Task
import fitz
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
import shutil
import base64
import requests
import textwrap
from PIL import Image, ImageDraw, ImageFont
import logging
from logging.handlers import RotatingFileHandler
from difflib import SequenceMatcher

from extensions import celery # Use the imported celery instance
from tts_service import TTSService, normalize_text
import text_cleaner
import chapterizer # Primary import
from chapterizer import CHAPTER_HEADING_RE, NAMED_SECTION_RE 
# This import is now safe because chapterizer.py is defined correctly.

# Import utility functions (must be available in the environment)
from utils import (
    get_piper_voices, ensure_voice_available, human_readable_size, 
    allowed_file, fetch_enhanced_metadata, extract_text_and_metadata
)
from tasks import process_chapter_task, convert_to_speech_task, create_audiobook_task

APP_VERSION = "0.0.4" # Revert to old version
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'
VOICES_FOLDER = '/app/voices'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'} # Defined here for convenience

def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object("celery_config")
    return celery_app

def create_app():
    """Application factory to create and configure the Flask app."""
    app = Flask(__name__)
    app.config.from_mapping(
        UPLOAD_FOLDER=UPLOAD_FOLDER,
        GENERATED_FOLDER=GENERATED_FOLDER,
        # Use FLASK_SECRET_KEY if available, otherwise fallback
        SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', 'a-secure-and-random-secret-key-for-dev')
    )
    
    # Initialize Celery
    celery_app = celery_init_app(app)

    # Setup logging (re-integrated from app_old.py)
    try:
        os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)
        log_file = os.path.join(app.config['GENERATED_FOLDER'], 'app.log')
        file_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=5)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Docket TTS startup')
    except PermissionError:
        app.logger.warning("Could not configure file logger due to a permission error.")
    
    # Initialize Redis client globally on the app object (re-integrated from app_old.py)
    try:
        app.redis_client = redis.from_url(celery_app.conf.broker_url)
    except Exception as e:
        app.logger.error(f"Could not create Redis client: {e}")
        app.redis_client = None

    if os.environ.get('RUNNING_IN_DOCKER'):
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        # Create VOICES_FOLDER as per old logic
        os.makedirs(VOICES_FOLDER, exist_ok=True)

    return app

app = create_app()

@app.context_processor
def inject_version():
    return dict(app_version=APP_VERSION)

def _similar(a, b):
    return SequenceMatcher(None, a, b).ratio() > 0.6

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        voice_name = request.form.get("voice")
        speed_rate = request.form.get("speed_rate", "1.0")
        
        text_input = request.form.get('text_input')
        if text_input and text_input.strip():
            book_title = request.form.get('text_title')
            
            if not book_title or not book_title.strip():
                flash('Title is required for pasted text.', 'error')
                return redirect(request.url)
            
            # For new pasted text, the original filename starts with 01
            original_filename = f"01 - {secure_filename(book_title.strip())}.txt"
            unique_internal_filename = f"{uuid.uuid4().hex}.txt"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
            Path(input_filepath).write_text(text_input, encoding='utf-8')
            
            book_author = 'Unknown'
            
            # Ensure voice is available before queuing the task
            try:
                # FIX: Pass app.redis_client
                ensure_voice_available(voice_name, app.redis_client)
            except Exception as e:
                flash(f"Error checking voice model: {e}", 'error')
                os.remove(input_filepath)
                return redirect(request.url)
            
            task = convert_to_speech_task.delay(input_filepath, original_filename, book_title, book_author, voice_name, speed_rate)
            
            return render_template('result.html', task_id=task.id)

        tasks = []
        
        # --- FIX: Retrieve debug_level from session ---
        debug_level_str = session.get("debug_level", "info").lower() 
        
        files = request.files.getlist('file')
        if not files or all(f.filename == '' for f in files):
            flash('No files selected.', 'error')
            return redirect(request.url)
        
        # Ensure voice is available before starting the main loop
        try:
            # FIX: Pass app.redis_client
            ensure_voice_available(voice_name, app.redis_client)
        except Exception as e:
            flash(f"Error checking voice model: {e}", 'error')
            return redirect(request.url)

        for file in files:
            if not file or not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}. Allowed types are: {', '.join(ALLOWED_EXTENSIONS)}.", 'error')
                continue

            original_filename = secure_filename(file.filename)
            unique_internal_filename = f"{uuid.uuid4().hex}{Path(original_filename).suffix}"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
            file.save(input_filepath)

            text_content, metadata = extract_text_and_metadata(input_filepath)
            
            enhanced_metadata = fetch_enhanced_metadata(metadata.get('title'), metadata.get('author'))

            app.logger.info(f"Processing '{original_filename}'.")
            
            # chapters will be a list of DICTs from the new chapterizer.py: 
            # [{'title': 'Chapter 5 (Part 1)', 'chunk_id': 1, 'text': '...'}, ...]
            chapters = chapterizer.chapterize(filepath=input_filepath, text_content=text_content, debug_level=debug_level_str)
            
            if chapters:
                app.logger.info(f"Chapterizer found {len(chapters)} chapters. Queuing tasks.")
                
                # Access the regex constants directly from the imported chapterizer module
                CHAPTER_HEADING_RE = chapterizer.CHAPTER_HEADING_RE
                NAMED_SECTION_RE = chapterizer.NAMED_SECTION_RE
                
                # FIX: Change access from attribute (chapter.number) to dictionary key (chapter['chunk_id'])
                for chapter in chapters:
                    
                    # --- FILENAME CLEANUP LOGIC START: FINAL REVISION ---
                    # chapter['title'] now contains the compound name (e.g., 'Chapter 5 (Part 1)')
                    full_chapter_title = chapter['title']
                    
                    # 1. Check if it contains the (Part X) notation added by split_into_chunks
                    if re.search(r'\s+\(Part\s+\d+\)$', full_chapter_title):
                        # If it's a split chunk, use the full compound title as the display_title
                        display_title = full_chapter_title
                    else:
                        # If it's a single, unsplit logical chapter (e.g., 'Preface: A Note on the Text'), 
                        # apply the old logic to strip the subtitle for a clean filename.
                        display_title = full_chapter_title
                        
                        simple_match = CHAPTER_HEADING_RE.match(display_title) or \
                                       NAMED_SECTION_RE.match(display_title)

                        if simple_match:
                            display_title = simple_match.group(0).strip()
                            
                            # Further simplify by stripping common separators for subtitles
                            if ':' in display_title:
                                # Keep only the part before the first colon (e.g., "Chapter 1")
                                display_title = display_title.split(':')[0].strip()
                            elif '-' in display_title:
                                # If it looks like 'Chapter 1 - Title', try to keep just 'Chapter 1'
                                parts = display_title.split('-')
                                if len(parts) > 1 and parts[0].strip().lower().startswith(('chapter', 'part', 'book')):
                                    display_title = parts[0].strip()
                            
                            # Ensure we don't end up with an empty string
                            if not display_title:
                                display_title = full_chapter_title
                        
                    # --- FILENAME CLEANUP LOGIC END ---

                    chapter_details = {
                        # Map chunk_id to number (this is the sequential number: 1, 2, 3, ... 66)
                        'number': chapter['chunk_id'], 
                        # Use the appropriate title (compound or simplified)
                        'title': display_title,
                        # Pass the full title (e.g., 'Chapter 5 (Part 1)') for richer metadata tags
                        'original_title': full_chapter_title, 
                        # part_info is no longer a tuple but baked into the title; set a default
                        'part_info': (1, 1) 
                    }
                    # The text is now in chapter['text']
                    task = process_chapter_task.delay(chapter['text'], enhanced_metadata, chapter_details, voice_name, speed_rate)
                    tasks.append(task)
                os.remove(input_filepath)
            else:
                # This executes when chapterizer.chapterize returns an empty list
                flash(f"Could not split '{original_filename}' into chapters. Processing as a single file.", "warning")
                # For single-file books, the original filename should start with 01
                single_file_name = f"01 - {Path(original_filename).stem}.txt"
                task = convert_to_speech_task.delay(input_filepath, single_file_name, enhanced_metadata.get('title'), enhanced_metadata.get('author'), voice_name, speed_rate)
                tasks.append(task)
        if tasks:
            flash(f'Successfully queued {len(tasks)} job(s) for processing.', 'success')
            return redirect(url_for('jobs_page'))
        else:
            flash('No processable content was found in the uploaded file(s).', 'error')
            return redirect(request.url)
    voices = get_piper_voices()
    return render_template('index.html', voices=voices)

# ... (rest of app.py is unchanged) ...
@app.route('/files')
def list_files():
    file_map = {}
    all_files = sorted(Path(app.config['GENERATED_FOLDER']).iterdir(), key=os.path.getmtime, reverse=True)

    for entry in all_files:
        if not entry.is_file() or entry.name.startswith(('sample_', 'cover_')):
            continue
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

@app.route('/edit_text/<text_filename>')
def edit_text_page(text_filename):
    safe_filename = secure_filename(text_filename)
    text_filepath = Path(app.config['GENERATED_FOLDER']) / safe_filename
    
    if not text_filepath.exists():
        flash(f"Text file {safe_filename} not found.", "error")
        return redirect(url_for('list_files'))

    try:
        content = text_filepath.read_text(encoding='utf-8')
        voices = get_piper_voices()
        
        parts = text_filepath.stem.split(' - ')
        chapter_title = parts[0].replace('_', ' ') if len(parts) > 0 else "Unknown"
        book_title = parts[1].replace('_', ' ') if len(parts) > 1 else "Unknown"
        
        # Adjust for single-file processing naming (e.g., 'Book.mp3')
        if len(parts) < 2:
             book_title = text_filepath.stem.replace('_', ' ')
             
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

@app.route('/reprocess_text', methods=['POST'])
def reprocess_text():
    edited_text = request.form.get('edited_text')
    original_txt_filename = request.form.get('original_filename')
    book_title = request.form.get('book_title')
    voice_name = request.form.get('voice')
    speed_rate = request.form.get('speed_rate', '1.0')

    if not all([edited_text, original_txt_filename, book_title, voice_name]):
        flash("Missing data for reprocessing. Please try again.", "error")
        return redirect(url_for('list_files'))

    unique_internal_filename = f"{uuid.uuid4().hex}_edited.txt"
    input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
    Path(input_filepath).write_text(edited_text, encoding='utf-8')
    
    # --- FILENAME FIX: Preserve chapter number and add _edited suffix ---
    original_stem = Path(original_txt_filename).stem.replace('_edited', '')
    new_stem = original_stem + "_edited"
    new_original_filename = f"{new_stem}.txt"
    
    book_author = 'Unknown'

    try:
        # FIX: Pass app.redis_client
        ensure_voice_available(voice_name, app.redis_client)
    except Exception as e:
        flash(f"Error checking voice model: {e}", 'error')
        os.remove(input_filepath)
        return redirect(url_for('edit_text_page', text_filename=original_txt_filename))

    task = convert_to_speech_task.delay(
        input_filepath,
        new_original_filename,
        book_title,
        book_author,
        voice_name,
        speed_rate
    )

    flash("Successfully queued edited text for reprocessing.", "success")
    return render_template('result.html', task_id=task.id)

@app.route('/get-book-metadata', methods=['POST'])
def get_book_metadata():
    filenames = request.json.get('filenames', [])
    if not filenames: return jsonify({'error': 'No filenames provided'}), 400
    
    first_mp3_path = Path(app.config['GENERATED_FOLDER']) / secure_filename(filenames[0])
    title_from_tags = "Unknown"
    author_from_tags = "Unknown"
    try:
        audio_tags = MP3(first_mp3_path, ID3=ID3)
        title_from_tags = str(audio_tags.get('TALB', [title_from_tags])[0])
        author_from_tags = str(audio_tags.get('TPE1', [author_from_tags])[0])
    except Exception as e:
        app.logger.warning(f"Could not read tags from {first_mp3_path}, falling back to filename parsing. Reason: {e}")
        chapter_match = re.match(r'^\d+\s*-\s*(.*?)\s*-.*$', Path(filenames[0]).stem)
        if chapter_match:
            title_from_tags = chapter_match.group(1).replace('_', ' ').strip()
    
    final_title = title_from_tags
    final_author = author_from_tags
    cover_url = ''
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
                if (task_args := task.get('args')) and isinstance(task_args, (list, tuple)) and len(task_args) > 3:
                    if 'process_chapter_task' in task.get('name', ''):
                         # --- FIX: Use the logical chapter title and sequential number for clarity ---
                         chapter_title = task_args[2].get('title', f"Ch. {task_args[2]['number']}")
                         book_title = task_args[1].get('title', 'Book')
                         # New format: [Book Title] - [Chapter Title (Part X)] (ID: Y)
                         original_filename = f"{book_title} - {chapter_title} (ID: {task_args[2]['number']})"
                    else:
                         original_filename = Path(task_args[1]).name
                running_jobs.append({'id': task['id'], 'name': original_filename, 'worker': worker})
        reserved_tasks = inspector.reserved() or {}
        for worker, tasks in reserved_tasks.items():
            for task in tasks:
                original_filename = "N/A"
                if (task_args := task.get('args')) and isinstance(task_args, (list, tuple)) and len(task_args) > 3:
                    if 'process_chapter_task' in task.get('name', ''):
                         # --- FIX: Use the logical chapter title and sequential number for clarity ---
                         chapter_title = task_args[2].get('title', f"Ch. {task_args[2]['number']}")
                         book_title = task_args[1].get('title', 'Book')
                         # New format: [Book Title] - [Chapter Title (Part X)] (ID: Y)
                         original_filename = f"{book_title} - {chapter_title} (ID: {task_args[2]['number']})"
                    else:
                         original_filename = Path(task_args[1]).name
                queued_jobs.append({'id': task['id'], 'name': original_filename, 'status': 'Reserved'})
        # FIX: Access redis_client from app context
        if app.redis_client:
            try:
                unassigned_job_count = app.redis_client.llen('celery')
            except Exception as e:
                app.logger.error(f"Could not get queue length from Redis: {e}")
    except Exception as e:
        app.logger.error(f"Could could not inspect Celery/Redis: {e}")
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
    app.logger.info(f"Received delete request. Form data: {request.form}")
    basenames_to_delete = set(request.form.getlist('files_to_delete'))
    app.logger.info(f"Basenames to delete from form: {basenames_to_delete}")
    
    deleted_count = 0
    if not basenames_to_delete:
        flash("No files selected for deletion.", "warning")
        app.logger.warning("files_to_delete was empty, no files will be deleted.")
        return redirect(url_for('list_files'))
        
    for base_name in basenames_to_delete:
        safe_base_name = secure_filename(base_name)
        app.logger.info(f"Processing base_name: '{base_name}', sanitized to: '{safe_base_name}'")
        
        files_found = list(Path(app.config['GENERATED_FOLDER']).glob(f"{safe_base_name}*.*"))
        app.logger.info(f"Glob pattern '{safe_base_name}*.*' found {len(files_found)} files: {files_found}")

        for f in files_found:
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
    sample_text = "The Lord is my shepherd; I shall not want. He makes me to lie down in green pastures; He leads me beside the still waters. He restores my soul; He leads me in the paths of righteousness For His name’s sake."
    speed_rate = request.args.get('speed', '1.0')

    try:
        # FIX: Pass app.redis_client
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
    if task.state == 'PENDING':
        response = {'state': 'PENDING', 'status': {'current': 0, 'total': 5, 'status': 'Waiting...'}}
    elif task.state == 'PROGRESS':
        response = {'state': 'PROGRESS', 'status': task.info}
    elif task.state == 'SUCCESS':
        response = {'state': 'SUCCESS', 'status': task.info}
    else:
        response = {'state': task.state, 'status': str(task.info)}
    return jsonify(response)

@app.route('/generated/<name>')
def download_file(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name)

@app.route('/health')
def health_check():
    """A simple health check endpoint."""
    return jsonify({"status": "healthy"}), 200

@app.route('/debug', methods=['GET', 'POST'])
def debug_page():
    voices = get_piper_voices()
    normalized_output = ""
    original_text = ""
    log_file = os.path.join(app.config['GENERATED_FOLDER'], 'app.log')
    
    # Default is 'info'
    current_debug_level = session.get("debug_level", "info").lower()

    if request.method == 'POST':
        if 'debug_level' in request.form:
             # FIX: Save the new debug level to the session 
             new_level = request.form.get("debug_level", "info").lower()
             session["debug_level"] = new_level
             current_debug_level = new_level
             flash(f"Logging level set to '{current_debug_level}'. Processing new files will use this level.", "success")
             return redirect(url_for('debug_page'))
        
        if 'text_to_normalize' in request.form:
             original_text = request.form.get('text_to_normalize', '')
             if original_text:
                 normalized_output = normalize_text(original_text)

    # Log reader logic unchanged
    try:
        # Determine log level filter based on the submitted level for display clarity
        log_level_map = {'off': logging.WARNING, 'info': logging.INFO, 'debug': logging.DEBUG, 'trace': logging.DEBUG}
        max_level = log_level_map.get(current_debug_level, logging.INFO)

        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
            filtered_lines = []
            for line in lines:
                is_debug_line = any(level in line for level in ['DEBUG', 'WARNING', 'ERROR'])
                is_info_line = 'INFO' in line
                
                if (max_level <= logging.INFO and (is_debug_line or is_info_line)) or \
                   (max_level == logging.WARNING and ('WARNING' in line or 'ERROR' in line)):
                    filtered_lines.append(line)

            log_content = "".join(filtered_lines[-500:]) # Show last 500 lines of filtered content
            
    except FileNotFoundError:
        app.logger.warning(f"Log file not found at {log_file} for debug page.")
    except Exception as e:
        log_content = f"Error reading log file: {e}"


    return render_template('debug.html', voices=voices, original_text=original_text, normalized_output=normalized_output, log_content=log_content, current_debug_level=current_debug_level)

if __name__ == '__main__':
    # NOTE: SECRET_KEY is required for sessions to work, which is needed for debug_level persistence
    app.run(host='0.0.0.0', port=5000, debug=True)
