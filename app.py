import os
import subprocess
import uuid
import re
import json
from pathlib import Path
from datetime import datetime, timezone
import time
from flask import (
    Flask, request, render_template, send_from_directory,
    flash, redirect, url_for, jsonify, current_app
)
from werkzeug.utils import secure_filename
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from celery import Celery, Task
import fitz
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, COMM, APIC
import redis
import shutil
import base64
import requests
import textwrap
from PIL import Image, ImageDraw, ImageFont
import logging
from logging.handlers import RotatingFileHandler
from huggingface_hub import list_repo_files, hf_hub_download
from difflib import SequenceMatcher

from tts_service import TTSService, normalize_text
import text_cleaner
import chapterizer

APP_VERSION = "0.0.4"
UPLOAD_FOLDER = '/app/uploads'
GENERATED_FOLDER = '/app/generated'
VOICES_FOLDER = '/app/voices'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'}
PIPER_VOICES_REPO = "rhasspy/piper-voices"
LARGE_FILE_WORD_THRESHOLD = 8000

app = Flask(__name__)
app.config.from_mapping(
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    GENERATED_FOLDER=GENERATED_FOLDER,
    SECRET_KEY='a-secure-and-random-secret-key'
)

try:
    if not app.debug and not app.testing:
        os.makedirs(GENERATED_FOLDER, exist_ok=True)
        log_file = os.path.join(GENERATED_FOLDER, 'app.log')
        file_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=5)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Docket TTS startup')
except PermissionError:
    app.logger.warning("Could not configure file logger due to a permission error. This is expected in some test environments.")

@app.context_processor
def inject_version():
    return dict(app_version=APP_VERSION)

def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object("celery_config")
    return celery_app

celery = celery_init_app(app)

try:
    redis_client = redis.from_url(celery.conf.broker_url)
except Exception as e:
    app.logger.error(f"Could not create Redis client: {e}")
    redis_client = None

if os.environ.get('RUNNING_IN_DOCKER'):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(GENERATED_FOLDER, exist_ok=True)
    os.makedirs(VOICES_FOLDER, exist_ok=True)

CACHED_PIPER_VOICES = None
def get_piper_voices():
    global CACHED_PIPER_VOICES
    if CACHED_PIPER_VOICES is not None:
        return CACHED_PIPER_VOICES
    
    app.logger.info("Fetching Piper voice list from Hugging Face Hub...")
    try:
        repo_files = list_repo_files(PIPER_VOICES_REPO)
        voices = []
        for f in repo_files:
            if f.startswith("en/") and f.endswith(".onnx"):
                voice_id = Path(f).name
                parts = voice_id.split('-')
                if len(parts) >= 3:
                    lang_country = parts[0]
                    name = parts[1].replace('_', ' ').title()
                    quality = parts[2]
                    readable_name = f"{name} ({lang_country} - {quality})"
                    voices.append({"id": voice_id, "name": readable_name, "repo_path": f})
        
        CACHED_PIPER_VOICES = sorted(voices, key=lambda v: v['name'])
        app.logger.info(f"Successfully fetched and cached {len(CACHED_PIPER_VOICES)} voices.")
        return CACHED_PIPER_VOICES
    except Exception as e:
        app.logger.error(f"Could not fetch voices from Hugging Face Hub: {e}")
        return list_available_voices()

def ensure_voice_available(voice_name):
    all_voices = get_piper_voices()
    voice_info = next((v for v in all_voices if v['id'] == voice_name), None)
    
    if not voice_info:
        raise ValueError(f"Could not find metadata for voice '{voice_name}' to download.")

    full_voice_path = Path(VOICES_FOLDER) / voice_info['repo_path']
    full_config_path = full_voice_path.with_suffix(full_voice_path.suffix + ".json")

    if full_voice_path.exists() and full_config_path.exists():
        app.logger.info(f"Voice '{voice_name}' found locally at {full_voice_path}")
        return str(full_voice_path)

    if not redis_client:
        app.logger.warning("Redis client not available. Proceeding without lock. This may cause issues with parallel downloads.")
    
    lock_key = f"lock:voice-download:{voice_name}"
    lock_acquired = False
    
    try:
        wait_start_time = time.time()
        while time.time() - wait_start_time < 120:
            if redis_client:
                lock_acquired = redis_client.set(lock_key, "1", nx=True, ex=60)
            
            if lock_acquired:
                app.logger.info(f"Acquired lock for downloading voice '{voice_name}'.")
                break
            else:
                app.logger.info(f"Waiting for lock on voice '{voice_name}'...")
                time.sleep(2)
        else:
            raise RuntimeError(f"Could not acquire lock for voice '{voice_name}' after 2 minutes.")

        if full_voice_path.exists() and full_config_path.exists():
            app.logger.info(f"Voice '{voice_name}' was downloaded by another worker while waiting for lock.")
            return str(full_voice_path)

        app.logger.warning(f"Voice '{voice_name}' not found locally. Starting download...")
        
        hf_hub_download(repo_id=PIPER_VOICES_REPO, filename=voice_info['repo_path'], local_dir=VOICES_FOLDER, local_dir_use_symlinks=False)
        hf_hub_download(repo_id=PIPER_VOICES_REPO, filename=voice_info['repo_path'] + ".json", local_dir=VOICES_FOLDER, local_dir_use_symlinks=False)
        
        app.logger.info(f"Successfully downloaded voice: {voice_name}")
        return str(full_voice_path)

    except Exception as e:
        app.logger.error(f"Failed to ensure voice {voice_name} is available: {e}")
        raise
    finally:
        if lock_acquired and redis_client:
            redis_client.delete(lock_key)
            app.logger.info(f"Released lock for voice '{voice_name}'.")

def human_readable_size(size, decimal_places=2):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0: break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def tag_mp3_file(filepath, metadata, cover_image_path=None, voice_name=None):
    try:
        audio = MP3(filepath, ID3=ID3)
        if audio.tags is None: audio.add_tags()
        
        chapter_title = metadata.get('title', 'Unknown Title')
        author = metadata.get('author', 'Unknown Author')
        book_title = metadata.get('book_title', chapter_title)
        
        clean_voice_name = Path(voice_name).stem if voice_name else 'Default'
        comment_text = f"Narrator: {clean_voice_name}. Generated by Docket TTS."

        safe_chapter_title = (chapter_title[:100] + '..') if len(chapter_title) > 100 else chapter_title
        safe_author = (author[:100] + '..') if len(author) > 100 else author
        safe_book_title = (book_title[:100] + '..') if len(book_title) > 100 else book_title
        
        audio.tags.add(TIT2(encoding=3, text=safe_chapter_title))
        audio.tags.add(TPE1(encoding=3, text=safe_author))
        audio.tags.add(TALB(encoding=3, text=safe_book_title))
        audio.tags.add(COMM(encoding=3, lang='eng', desc='Comment', text=comment_text))
        
        if cover_image_path and os.path.exists(cover_image_path):
            with open(cover_image_path, 'rb') as f:
                image_data = f.read()
            mime = 'image/jpeg' if cover_image_path.lower().endswith('.jpg') else 'image/png'
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=image_data))
        
        audio.save()
        app.logger.info(f"Successfully tagged {filepath}")
    except Exception as e:
        app.logger.error(f"Failed to tag {filepath}: {e}")

def parse_metadata_from_text(text_content):
    parsed_meta = {}
    search_area = text_content[:4000]
    lines = [line.strip() for line in search_area.split('\n') if line.strip()]
    if not lines: return parsed_meta
    for line in lines:
        if line.lower().startswith('by '):
            author = line[3:].strip()
            if 2 < len(author) < 60:
                parsed_meta['author'] = author
                break
    potential_titles = []
    for line in lines[:15]:
        if 'author' in parsed_meta and parsed_meta['author'] in line: continue
        words = line.split()
        if 1 < len(words) < 12:
            if line.isupper():
                potential_titles.append((line, len(line) + 20))
            else:
                potential_titles.append((line, len(line)))
    if potential_titles:
        best_title = sorted(potential_titles, key=lambda x: x[1], reverse=True)[0][0]
        parsed_meta['title'] = best_title
    return parsed_meta

def extract_text_and_metadata(filepath):
    p_filepath = Path(filepath)
    extension = p_filepath.suffix.lower()
    text = ""
    metadata = {'title': p_filepath.stem.replace('_', ' ').title(), 'author': 'Unknown'}
    try:
        if extension == '.pdf':
            with fitz.open(filepath) as doc:
                doc_meta = doc.metadata
                if doc_meta:
                    metadata['title'] = doc_meta.get('title') or metadata['title']
                    metadata['author'] = doc_meta.get('author') or metadata['author']
                text = "\n".join([page.get_text() for page in doc])
        elif extension == '.epub':
            book = epub.read_epub(filepath)
            titles = book.get_metadata('DC', 'title')
            if titles: metadata['title'] = titles[0][0]
            creators = book.get_metadata('DC', 'creator')
            if creators: metadata['author'] = creators[0][0]
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                text += soup.get_text() + "\n\n"
        elif extension == '.docx':
            doc = docx.Document(filepath)
            if doc.core_properties:
                metadata['title'] = doc.core_properties.title or metadata['title']
                metadata['author'] = doc.core_properties.author or metadata['author']
            text = "\n".join([para.text for para in doc.paragraphs])
        elif extension == '.txt':
            text = p_filepath.read_text(encoding='utf-8')
    except Exception as e:
        app.logger.error(f"Error extracting text and metadata from {filepath}: {e}")
        # Return empty text but keep metadata so we have a title at least
        return "", metadata

    if text:
        if re.match(r'^[a-f0-9]{8,}', metadata.get('title', '')):
            metadata['title'] = "Untitled"
        parsed_meta = parse_metadata_from_text(text)
        if metadata['title'] == p_filepath.stem.replace('_', ' ').title() or metadata['title'] == "Untitled":
             if 'title' in parsed_meta:
                metadata['title'] = parsed_meta['title']
        if metadata['author'] == 'Unknown' and 'author' in parsed_meta:
            metadata['author'] = parsed_meta['author']
    
    if not metadata.get('title'): metadata['title'] = p_filepath.stem.replace('_', ' ').title()
    if not metadata.get('author'): metadata['author'] = 'Unknown'
    
    return text, metadata

def list_available_voices():
    voices = []
    voice_dir = Path(VOICES_FOLDER)
    if voice_dir.is_dir():
        for voice_file in voice_dir.glob("*.onnx"):
            voices.append({"id": voice_file.name, "name": voice_file.stem})
    return sorted(voices, key=lambda v: v['name'])

def clean_filename_part(name_part):
    s_name = re.sub(r'[^\w\s-]', '', name_part)
    s_name = re.sub(r'[-\s]+', ' ', s_name).strip()
    return s_name[:40]

@celery.task(bind=True)
def process_chapter_task(self, chapter_content, book_title, book_author, chapter_details, voice_name, speed_rate):
    generated_folder = Path(current_app.config['GENERATED_FOLDER'])
    try:
        status_msg = f'Processing: {book_title} - Ch. {chapter_details["number"]} "{chapter_details["title"][:20]}..."'
        self.update_state(state='PROGRESS', meta={'status': status_msg})
        
        full_voice_path = ensure_voice_available(voice_name)
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        
        s_book_title = clean_filename_part(book_title)
        s_chapter_title = clean_filename_part(chapter_details['title'])
        
        part_info = chapter_details.get('part_info', (1, 1))
        part_str = ""
        if part_info[1] > 1:
            part_str = f" - Part {part_info[0]} of {part_info[1]}"

        output_filename = f"{chapter_details['number']:02d} - {s_book_title} - {s_chapter_title}{part_str}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = generated_folder / safe_output_filename
        
        _, normalized_text = tts.synthesize(chapter_content, str(output_filepath))
        
        metadata_title = chapter_details.get('original_title', chapter_details['title'])
        if part_info[1] > 1:
            metadata_title += f" (Part {part_info[0]} of {part_info[1]})"

        tag_mp3_file(
            str(output_filepath),
            metadata={'title': metadata_title, 'author': book_author, 'book_title': book_title},
            voice_name=voice_name
        )

        text_filename = output_filepath.with_suffix('.txt').name
        (generated_folder / text_filename).write_text(normalized_text, encoding="utf-8")

        app.logger.info(f"Task {self.request.id} completed successfully. Output: {safe_output_filename}")
        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}

    except Exception as e:
        app.logger.error(f"Chapter processing failed in task {self.request.id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e

@celery.task(bind=True)
def convert_to_speech_task(self, input_filepath, original_filename, book_title, book_author, voice_name=None, speed_rate='1.0'):
    temp_cover_path = None
    generated_folder = current_app.config['GENERATED_FOLDER']
    try:
        self.update_state(state='PROGRESS', meta={'current': 1, 'total': 5, 'status': 'Checking voice model...'})
        full_voice_path = ensure_voice_available(voice_name)

        self.update_state(state='PROGRESS', meta={'current': 2, 'total': 5, 'status': 'Reading, cleaning, and normalizing text...'})
        
        text_content, _ = extract_text_and_metadata(input_filepath)
        if not text_content: 
            if Path(input_filepath).suffix.lower() == '.pdf':
                with fitz.open(input_filepath) as doc:
                    text_content = "\n".join([page.get_text() for page in doc])
            elif Path(input_filepath).suffix.lower() == '.epub':
                book = epub.read_epub(input_filepath)
                for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                    text_content += soup.get_text() + "\n\n"
        
        if not text_content:
            raise ValueError('Could not extract text from file for single-file processing.')

        cleaned_text = text_cleaner.clean_text(text_content)
        
        self.update_state(state='PROGRESS', meta={'current': 3, 'total': 5, 'status': 'Synthesizing audio...'})
        s_book_title = clean_filename_part(book_title)
        output_filename = f"01 - {s_book_title}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = os.path.join(generated_folder, safe_output_filename)
        
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        _, normalized_text = tts.synthesize(cleaned_text, output_filepath)
        
        tag_title = book_title
        author = book_author
        
        cover_url = ''
        if tag_title:
            try:
                query_title = tag_title.split(':')[0].strip()
                query = f"intitle:{query_title}"
                if author and author != 'Unknown': query += f"+inauthor:{author}"
                response = requests.get(f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1")
                response.raise_for_status()
                data = response.json()
                if data.get('totalItems', 0) > 0:
                    book_info = data['items'][0]['volumeInfo']
                    cover_url = book_info.get('imageLinks', {}).get('thumbnail', '')
            except requests.RequestException as e:
                app.logger.error(f"Google Books API request failed during TTS task: {e}")
        
        unique_id = str(uuid.uuid4().hex[:8])
        temp_cover_path = os.path.join(generated_folder, f"cover_{unique_id}.jpg")
        cover_path_to_use = None
        if cover_url:
            try:
                response = requests.get(cover_url, stream=True)
                response.raise_for_status()
                with open(temp_cover_path, 'wb') as f:
                    shutil.copyfileobj(response.raw, f)
                cover_path_to_use = temp_cover_path
            except requests.RequestException as e:
                app.logger.error(f"Failed to download cover art: {e}")

        if not cover_path_to_use:
            if create_generic_cover_image(tag_title, author, temp_cover_path):
                cover_path_to_use = temp_cover_path
        
        self.update_state(state='PROGRESS', meta={'current': 4, 'total': 5, 'status': 'Tagging and Saving...'})
        tag_mp3_file(output_filepath, {'title': tag_title, 'author': author, 'book_title': book_title}, cover_image_path=cover_path_to_use, voice_name=voice_name)
        
        self.update_state(state='PROGRESS', meta={'current': 5, 'total': 5, 'status': 'Saving text file...'})
        text_filename = Path(output_filepath).with_suffix('.txt').name
        Path(os.path.join(generated_folder, text_filename)).write_text(normalized_text, encoding="utf-8")

        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}
    except Exception as e:
        app.logger.error(f"TTS Conversion failed in task {self.request.id}: {e}")
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e
    finally:
        if os.path.exists(input_filepath):
            os.remove(input_filepath)
        if temp_cover_path and os.path.exists(temp_cover_path):
            os.remove(temp_cover_path)

def create_generic_cover_image(title, author, save_path):
    try:
        width, height = 800, 1200
        image = Image.new('RGB', (width, height), color = (73, 109, 137))
        draw = ImageDraw.Draw(image)
        try:
            font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", size=60)
            font_author = ImageFont.truetype("DejaVuSans.ttf", size=40)
        except IOError:
            font_title = ImageFont.load_default()
            font_author = ImageFont.load_default()
        title_lines = textwrap.wrap(title, width=20)
        y_text = height / 4
        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=font_title)
            line_width, line_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(((width - line_width) / 2, y_text), line, font=font_title, fill=(255, 255, 255))
            y_text += line_height + 5
        y_text += 50
        author_lines = textwrap.wrap(author, width=30)
        for line in author_lines:
            bbox = draw.textbbox((0, 0), line, font=font_author)
            line_width, line_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(((width - line_width) / 2, y_text), line, font=font_author, fill=(255, 255, 255))
            y_text += line_height + 5
        image.save(save_path)
        return save_path
    except Exception as e:
        app.logger.error(f"Failed to create generic cover image: {e}")
        return None

def _create_audiobook_logic(file_list, audiobook_title_from_form, audiobook_author_from_form, cover_url, build_dir, task_self=None):
    def update_state(state, meta):
        if task_self:
            task_self.update_state(state=state, meta=meta)
    
    generated_folder = build_dir.parent
    unique_file_list = sorted(list(set(file_list)))
    
    first_mp3_path = generated_folder / secure_filename(unique_file_list[0])
    audio_tags = MP3(first_mp3_path, ID3=ID3)
    
    final_audiobook_title = str(audio_tags.get('TALB', [audiobook_title_from_form])[0])
    final_audiobook_author = str(audio_tags.get('TPE1', [audiobook_author_from_form])[0])

    app.logger.info(f"Using metadata for M4B: Title='{final_audiobook_title}', Author='{final_audiobook_author}'")

    update_state(state='PROGRESS', meta={'current': 1, 'total': 5, 'status': 'Gathering chapters and text...'})
    safe_mp3_paths = [generated_folder / secure_filename(fname) for fname in unique_file_list]
    merged_text_content = "".join(p.with_suffix('.txt').read_text(encoding='utf-8') + "\n\n" for p in safe_mp3_paths if p.with_suffix('.txt').exists())
    update_state(state='PROGRESS', meta={'current': 2, 'total': 5, 'status': 'Downloading cover art...'})
    cover_path = None
    if cover_url:
        try:
            response = requests.get(cover_url, stream=True)
            response.raise_for_status()
            cover_path = build_dir / "cover.jpg"
            with open(cover_path, 'wb') as f: shutil.copyfileobj(response.raw, f)
        except requests.RequestException as e:
            app.logger.error(f"Failed to download cover art: {e}")
            cover_path = None
    if not cover_path and final_audiobook_title and final_audiobook_author:
        generic_cover_path = build_dir / "generic_cover.jpg"
        if create_generic_cover_image(final_audiobook_title, final_audiobook_author, generic_cover_path):
            cover_path = generic_cover_path
            
    update_state(state='PROGRESS', meta={'current': 3, 'total': 5, 'status': 'Analyzing chapters...'})
    chapters_meta_content = f";FFMETADATA1\ntitle={final_audiobook_title}\nartist={final_audiobook_author}\n\n"
    concat_list_content = ""
    current_duration_ms = 0
    for i, path in enumerate(safe_mp3_paths):
        audio_chapter = MP3(path, ID3=ID3)
        chapter_title = str(audio_chapter.get('TIT2', [f'Chapter {i+1}'])[0])
        duration_s = audio_chapter.info.length
        duration_ms = int(duration_s * 1000)
        concat_list_content += f"file '{path.resolve()}'\n"
        chapters_meta_content += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={current_duration_ms}\nEND={current_duration_ms + duration_ms}\ntitle={chapter_title}\n\n"
        current_duration_ms += duration_ms
        
    concat_list_path = build_dir / "concat_list.txt"
    chapters_meta_path = build_dir / "chapters.meta"
    concat_list_path.write_text(concat_list_content)
    chapters_meta_path.write_text(chapters_meta_content, encoding='utf-8')
    update_state(state='PROGRESS', meta={'current': 4, 'total': 5, 'status': 'Merging and encoding audio...'})
    temp_audio_path = build_dir / "temp_audio.aac"
    concat_command = ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', str(concat_list_path), '-threads', '0', '-c:a', 'aac', '-b:a', '128k', str(temp_audio_path)]
    subprocess.run(concat_command, check=True, capture_output=True)
    update_state(state='PROGRESS', meta={'current': 5, 'total': 5, 'status': 'Assembling audiobook...'})
    timestamp = build_dir.name.replace('audiobook_build_', '')
    output_filename = f"{secure_filename(final_audiobook_title)}_{timestamp}.m4b"
    output_filepath = generated_folder / output_filename
    mux_command = ['ffmpeg']
    if cover_path: mux_command.extend(['-i', str(cover_path)])
    mux_command.extend(['-i', str(temp_audio_path), '-i', str(chapters_meta_path)])
    map_offset = 1 if cover_path else 0
    mux_command.extend(['-map', f'{map_offset}:a', '-map_metadata', f'{map_offset + 1}'])
    if cover_path:
        mux_command.extend(['-map', '0:v', '-disposition:v', 'attached_pic'])
    mux_command.extend(['-c:a', 'copy', '-c:v', 'copy', str(output_filepath)])
    subprocess.run(mux_command, check=True, capture_output=True)

    text_filepath = output_filepath.with_suffix('.txt')
    text_filepath.write_text(merged_text_content, encoding='utf-8')
    text_filename = text_filepath.name

    return {'status': 'Success', 'filename': output_filename, 'textfile': text_filename}

@celery.task(bind=True)
def create_audiobook_task(self, file_list, audiobook_title, audiobook_author, cover_url=None):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    build_dir = Path(current_app.config['GENERATED_FOLDER']) / f"audiobook_build_{timestamp}"
    os.makedirs(build_dir, exist_ok=True)
    try:
        return _create_audiobook_logic(file_list, audiobook_title, audiobook_author, cover_url, build_dir, task_self=self)
    except Exception as e:
        app.logger.error(f"Audiobook creation failed: {e}")
        if isinstance(e, subprocess.CalledProcessError):
            app.logger.error(f"FFMPEG stderr: {e.stderr.decode()}")
        raise e
    finally:
        if build_dir.exists(): shutil.rmtree(build_dir)

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        voice_name = request.form.get("voice")
        speed_rate = request.form.get("speed_rate", "1.0")
        
        # --- FIX STARTS HERE ---
        # This block restores the functionality for handling pasted text, which is required by the test suite.
        text_input = request.form.get('text_input')
        if text_input and text_input.strip():
            book_title = request.form.get('text_title')
            
            if not book_title or not book_title.strip():
                flash('Title is required for pasted text.', 'error')
                return redirect(request.url)
            
            # The tests expect a single task for pasted text, so we'll use the single-file converter.
            original_filename = f"{secure_filename(book_title.strip())}.txt"
            unique_internal_filename = f"{uuid.uuid4().hex}.txt"
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_internal_filename)
            Path(input_filepath).write_text(text_input, encoding='utf-8')
            
            # For pasted text, author is unknown. The task will try to parse it.
            book_author = 'Unknown'
            
            # Queue the single-file conversion task.
            task = convert_to_speech_task.delay(input_filepath, original_filename, book_title, book_author, voice_name, speed_rate)
            
            # Return the result page with the task ID, which the test suite expects.
            return render_template('result.html', task_id=task.id)
        # --- FIX ENDS HERE ---

        tasks = []
        debug_mode = 'debug_mode' in request.form
        
        files = request.files.getlist('file')
        if not files or all(f.filename == '' for f in files):
            flash('No files selected.', 'error')
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
            book_title = metadata.get('title', Path(original_filename).stem)
            book_author = metadata.get('author', 'Unknown')

            if ':' in book_title:
                book_title = book_title.split(':')[0].strip()

            app.logger.info(f"Processing '{original_filename}'.")
            chapters = chapterizer.chapterize(filepath=input_filepath, text_content=text_content, debug=debug_mode)
            
            if chapters:
                app.logger.info(f"Chapterizer found {len(chapters)} chapters. Queuing tasks.")
                for chapter in chapters:
                    chapter_details = {
                        'number': chapter.number,
                        'title': chapter.title,
                        'original_title': chapter.original_title,
                        'part_info': chapter.part_info
                    }
                    task = process_chapter_task.delay(chapter.content, book_title, book_author, chapter_details, voice_name, speed_rate)
                    tasks.append(task)
                os.remove(input_filepath)
            else:
                flash(f"Could not split '{original_filename}' into chapters. Processing as a single file.", "warning")
                task = convert_to_speech_task.delay(input_filepath, original_filename, book_title, book_author, voice_name, speed_rate)
                tasks.append(task)

        if tasks:
            flash(f'Successfully queued {len(tasks)} job(s) for processing.', 'success')
            return redirect(url_for('jobs_page'))
        else:
            flash('No processable content was found in the uploaded file(s).', 'error')
            return redirect(request.url)

    voices = get_piper_voices()
    return render_template('index.html', voices=voices)

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
        if 'audio_name' not in data:
            continue
        
        data['base_name'] = key
        
        processed_files.append(data)

    return render_template('files.html', audio_files=processed_files)

def _similar(a, b):
    return SequenceMatcher(None, a, b).ratio() > 0.6

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
            queries = [
                f'intitle:"{final_title}" inauthor:"{final_author}"',
                f'intitle:"{final_title}"'
            ]
            
            data = {'totalItems': 0}
            for query in queries:
                if data.get('totalItems', 0) > 0:
                    break
                app.logger.info(f"Trying Google Books API query: {query}")
                response = requests.get(f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1")
                response.raise_for_status()
                data = response.json()

            if data.get('totalItems', 0) > 0:
                book_info = data['items'][0]['volumeInfo']
                api_title = book_info.get('title', '')
                
                if _similar(final_title.lower(), api_title.lower()):
                    final_title = api_title
                    final_author = ", ".join(book_info.get('authors', [final_author]))
                    cover_url = book_info.get('imageLinks', {}).get('thumbnail', '')
                else:
                    app.logger.warning(f"Google Books API returned a mismatched title ('{api_title}' vs '{final_title}'). Rejecting API result.")

        except requests.RequestException as e:
            app.logger.error(f"Google Books API request failed: {e}")
            
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
                         original_filename = f"{task_args[1]} - Ch. {task_args[3]['number']}"
                    else:
                         original_filename = Path(task_args[1]).name
                running_jobs.append({'id': task['id'], 'name': original_filename, 'worker': worker})
        reserved_tasks = inspector.reserved() or {}
        for worker, tasks in reserved_tasks.items():
            for task in tasks:
                original_filename = "N/A"
                if (task_args := task.get('args')) and isinstance(task_args, (list, tuple)) and len(task_args) > 3:
                    if 'process_chapter_task' in task.get('name', ''):
                         original_filename = f"{task_args[1]} - Ch. {task_args[3]['number']}"
                    else:
                         original_filename = Path(task_args[1]).name
                queued_jobs.append({'id': task['id'], 'name': original_filename, 'status': 'Reserved'})
        if redis_client:
            try:
                unassigned_job_count = redis_client.llen('celery')
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
        full_voice_path = ensure_voice_available(voice_name)
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
    log_content = "Log file not found."
    log_file = os.path.join(app.config['GENERATED_FOLDER'], 'app.log')

    if request.method == 'POST':
        original_text = request.form.get('text_to_normalize', '')
        if original_text:
            normalized_output = normalize_text(original_text)
    
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
            log_content = "".join(lines[-100:])
    except FileNotFoundError:
        app.logger.warning(f"Log file not found at {log_file} for debug page.")

    return render_template('debug.html', voices=voices, original_text=original_text, normalized_output=normalized_output, log_content=log_content)
