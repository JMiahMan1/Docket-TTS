# /app/tasks.py

import os
import shutil
import subprocess
from pathlib import Path
from werkzeug.utils import secure_filename
from flask import current_app 
from celery import Task, group

from extensions import celery
from utils import (
    extract_text_and_metadata, fetch_enhanced_metadata, create_title_page_text,
    ensure_voice_available, clean_filename_part, tag_mp3_file, create_generic_cover_image,
    _create_audiobook_logic, allowed_file, parse_metadata_from_text,
)
from tts_service import TTSService, normalize_text
import text_cleaner
import chapterizer
from bs4 import BeautifulSoup 
import ebooklib

class AppContextTask(Task):
    """A Celery Task that runs within the Flask application context."""
    def __call__(self, *args, **kwargs):
        # FIX: Import the globally created Flask app instance from app.py
        from app import app as flask_app 
        
        # Line 30 in the traceback is here:
        with flask_app.app_context():
            # We must ensure the application context is available before running the task logic
            # The app instance will have 'redis_client' attached by create_app
            return self.run(*args, **kwargs)

celery.Task = AppContextTask

@celery.task(bind=True)
def process_chapter_task(self, chapter_content, book_metadata, chapter_details, voice_name, speed_rate):
    """Celery task for processing a single chapter (re-integrated from app_old.py)."""
    # Use current_app now that context is pushed
    generated_folder = Path(current_app.config['GENERATED_FOLDER'])
    output_filepath = None 
    try:
        status_msg = f'Processing: {book_metadata.get("title", "Unknown")} - Ch. {chapter_details["number"]} "{chapter_details["title"][:20]}..."'
        self.update_state(state='PROGRESS', meta={'status': status_msg})

        full_voice_path = ensure_voice_available(voice_name, current_app.redis_client)
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        
        final_content = chapter_content
        if chapter_details.get("number") == 1:
            unnormalized_title_page = create_title_page_text(book_metadata)
            normalized_title_page = normalize_text(unnormalized_title_page)
            final_content = normalized_title_page + chapter_content
            
        s_book_title = clean_filename_part(book_metadata.get("title", "book"))
        s_chapter_title = clean_filename_part(chapter_details['title'])
        part_info = chapter_details.get('part_info', (1, 1))
        part_str = ""
        if part_info[1] > 1: part_str = f" - Part {part_info[0]} of {part_info[1]}"
        output_filename = f"{chapter_details['number']:02d} - {s_book_title} - {s_chapter_title}{part_str}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = generated_folder / safe_output_filename 
        
        _, synthesized_text = tts.synthesize(final_content, str(output_filepath))
        
        metadata_title = chapter_details.get('original_title', chapter_details['title'])
        if part_info[1] > 1: metadata_title += f" (Part {part_info[0]} of {part_info[1]})"
        
        tag_mp3_file(
            str(output_filepath),
            metadata={'title': metadata_title, 'author': book_metadata.get("author"), 'book_title': book_metadata.get("title")},
            voice_name=voice_name
        )
        
        text_filename = output_filepath.with_suffix('.txt').name
        (generated_folder / text_filename).write_text(synthesized_text, encoding="utf-8")
        current_app.logger.info(f"Task {self.request.id} completed successfully. Output: {safe_output_filename}")
        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}
    
    except Exception as e:
        current_app.logger.error(f"Chapter processing failed in task {self.request.id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e
    finally:
        if output_filepath and output_filepath.exists():
            try:
                os.remove(output_filepath)
                current_app.logger.warning(f"Cleaned up partial audio file: {output_filepath.name} due to failure.")
            except OSError as e:
                current_app.logger.error(f"Failed to delete partial audio file {output_filepath.name} in cleanup: {e}")

@celery.task(bind=True)
def convert_to_speech_task(self, input_filepath, original_filename, book_title, book_author, voice_name=None, speed_rate='1.0'):
    """Celery task for single-file conversion (re-integrated from app_old.py)."""
    temp_cover_path = None
    generated_folder = current_app.config['GENERATED_FOLDER'] 
    output_filepath = None 
    try:
        self.update_state(state='PROGRESS', meta={'current': 1, 'total': 5, 'status': 'Checking voice model...'})
        
        # FIX: Pass current_app.redis_client
        full_voice_path = ensure_voice_available(voice_name, current_app.redis_client)
        
        self.update_state(state='PROGRESS', meta={'current': 2, 'total': 5, 'status': 'Reading, cleaning, and normalizing text...'})
        
        text_content, _ = extract_text_and_metadata(input_filepath)
        if not text_content: 
            p_filepath = Path(input_filepath)
            if p_filepath.suffix.lower() == '.pdf':
                with fitz.open(input_filepath) as doc: text_content = "\n".join([page.get_text() for page in doc])
            elif p_filepath.suffix.lower() == '.epub':
                book = ebooklib.epub.read_epub(input_filepath)
                for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                    text_content += soup.get_text() + "\n\n"
        if not text_content: raise ValueError('Could not extract text from file for single-file processing.')
        
        cleaned_text = text_cleaner.clean_text(text_content)
        normalized_main_text = normalize_text(cleaned_text)
        enhanced_metadata = fetch_enhanced_metadata(book_title, book_author)
        unnormalized_title_page = create_title_page_text(enhanced_metadata)
        normalized_title_page = normalize_text(unnormalized_title_page)
        final_content_for_synthesis = normalized_title_page + normalized_main_text
        self.update_state(state='PROGRESS', meta={'current': 3, 'total': 5, 'status': 'Synthesizing audio...'})
        
        base_name = Path(original_filename).stem.replace('01 - ', '') 
        output_filename = f"{base_name}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = os.path.join(generated_folder, safe_output_filename) 
        
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        _, synthesized_text = tts.synthesize(final_content_for_synthesis, output_filepath)
        
        cover_url = enhanced_metadata.get('cover_url', '')
        unique_id = str(uuid.uuid4().hex[:8])
        temp_cover_path = os.path.join(generated_folder, f"cover_{unique_id}.jpg")
        cover_path_to_use = None
        if cover_url:
            try:
                response = requests.get(cover_url, stream=True)
                response.raise_for_status()
                with open(temp_cover_path, 'wb') as f: shutil.copyfileobj(response.raw, f)
                cover_path_to_use = temp_cover_path
            except requests.RequestException as e:
                current_app.logger.error(f"Failed to download cover art: {e}")
        if not cover_path_to_use:
            if create_generic_cover_image(enhanced_metadata.get("title"), enhanced_metadata.get("author"), temp_cover_path):
                cover_path_to_use = temp_cover_path
        
        self.update_state(state='PROGRESS', meta={'current': 4, 'total': 5, 'status': 'Tagging and Saving...'})
        tag_mp3_file(
            output_filepath, 
            {'title': enhanced_metadata.get("title"), 'author': enhanced_metadata.get("author"), 'book_title': enhanced_metadata.get("title")}, 
            cover_image_path=cover_path_to_use, 
            voice_name=voice_name
        )
        self.update_state(state='PROGRESS', meta={'current': 5, 'total': 5, 'status': 'Saving text file...'})
        text_filename = Path(output_filepath).with_suffix('.txt').name
        Path(os.path.join(generated_folder, text_filename)).write_text(synthesized_text, encoding="utf-8")
        
        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}
    
    except Exception as e:
        current_app.logger.error(f"TTS Conversion failed in task {self.request.id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e
    finally:
        if os.path.exists(input_filepath): os.remove(input_filepath)
        if temp_cover_path and os.path.exists(temp_cover_path): os.remove(temp_cover_path)

@celery.task(bind=True)
def create_audiobook_task(self, file_list, audiobook_title, audiobook_author, cover_url=None):
    """Celery task to merge multiple MP3s into a single M4B audiobook (re-integrated from app_old.py)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    build_dir = Path(current_app.config['GENERATED_FOLDER']) / f"audiobook_build_{timestamp}"
    os.makedirs(build_dir, exist_ok=True)
    try:
        return _create_audiobook_logic(file_list, audiobook_title, audiobook_author, cover_url, build_dir, task_self=self)
    except Exception as e:
        current_app.logger.error(f"Audiobook creation failed: {e}")
        if isinstance(e, subprocess.CalledProcessError):
            current_app.logger.error(f"FFMPEG stderr: {e.stderr.decode()}")
        raise e
    finally:
        if build_dir.exists(): shutil.rmtree(build_dir)
