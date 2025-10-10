# /app/tasks.py

import os
import shutil
import subprocess
from pathlib import Path
from flask import current_app
from celery import Task, group

from extensions import celery
from utils import (
    extract_text_and_metadata, fetch_enhanced_metadata, create_title_page_text,
    ensure_voice_available, clean_filename_part, tag_mp3_file, create_generic_cover_image,
    _create_audiobook_logic
)
from tts_service import TTSService, normalize_text
import text_cleaner
import chapterizer

class AppContextTask(Task):
    """A Celery Task that runs within the Flask application context."""
    def __call__(self, *args, **kwargs):
        with current_app.app_context():
            return self.run(*args, **kwargs)

celery.Task = AppContextTask

@celery.task(bind=True)
def process_book_task(self, input_filepath, original_filename, voice_name, speed_rate, debug_level='off'):
    """Master task to handle chapterization in the background."""
    try:
        self.update_state(state='PROGRESS', meta={'status': f'Extracting text from {original_filename}...'})
        current_app.logger.debug(f"[Task:{self.request.id}] Starting book processing. File: {original_filename}, Debug: {debug_level}")
        
        text_content, metadata = extract_text_and_metadata(input_filepath)
        enhanced_metadata = fetch_enhanced_metadata(metadata.get('title'), metadata.get('author'))

        self.update_state(state='PROGRESS', meta={'status': f'Splitting "{enhanced_metadata.get("title")}" into chapters...'})
        current_app.logger.debug(f"[Task:{self.request.id}] Starting chapterizer.chapterize...")
        
        chapters = chapterizer.chapterize(filepath=input_filepath, text_content=text_content, debug_level=debug_level)
        
        if not chapters:
            current_app.logger.warning(f"[Task:{self.request.id}] Chapterizer found no chapters for {original_filename}. Falling back to single-file processing.")
            self.update_state(state='PROGRESS', meta={'status': 'No chapters found. Processing as a single file...'})
            # Replace this task with the single-file conversion task
            raise self.replace(convert_to_speech_task.s(
                input_filepath, original_filename, 
                enhanced_metadata.get('title'), enhanced_metadata.get('author'),
                voice_name, speed_rate, debug_level
            ))

        current_app.logger.debug(f"[Task:{self.request.id}] Chapterizer found {len(chapters)} chapters. Creating sub-tasks.")
        self.update_state(state='PROGRESS', meta={'status': f'Found {len(chapters)} chapters. Queuing for audio conversion...'})
        
        task_group = group(
            process_chapter_task.s(
                chapter.content, enhanced_metadata, 
                {'number': chapter.number, 'title': chapter.title, 'original_title': chapter.original_title, 'part_info': chapter.part_info}, 
                voice_name, speed_rate, debug_level
            ) for chapter in chapters
        )
        task_group.apply_async()
        
        os.remove(input_filepath)
        return {'status': 'Success', 'message': f'Successfully queued {len(chapters)} chapters for processing.'}

    except Exception as e:
        current_app.logger.error(f"Master book processing failed for {original_filename}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        if os.path.exists(input_filepath):
             os.remove(input_filepath)
        raise

@celery.task(bind=True)
def process_chapter_task(self, chapter_content, book_metadata, chapter_details, voice_name, speed_rate, debug_level='off'):
    """Celery task for processing a single chapter."""
    generated_folder = Path(current_app.config['GENERATED_FOLDER'])
    try:
        status_msg = f'Processing: {book_metadata.get("title", "Unknown")} - Ch. {chapter_details["number"]} "{chapter_details["title"][:20]}..."'
        self.update_state(state='PROGRESS', meta={'status': status_msg})
        current_app.logger.debug(f"[Task:{self.request.id}] Processing chapter {chapter_details['number']}. Title: {chapter_details['title']}.")
        
        full_voice_path = ensure_voice_available(voice_name, current_app.redis_client)
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        
        final_content = chapter_content 
        if chapter_details.get("number") == 1:
            unnormalized_title_page = create_title_page_text(book_metadata)
            normalized_title_page = normalize_text(unnormalized_title_page, debug_level=debug_level)
            final_content = normalized_title_page + chapter_content
            
        s_book_title = clean_filename_part(book_metadata.get("title", "book"))
        s_chapter_title = clean_filename_part(chapter_details['title'])
        part_info = chapter_details.get('part_info', (1, 1))
        part_str = f" - Part {part_info[0]} of {part_info[1]}" if part_info[1] > 1 else ""
        output_filename = f"{chapter_details['number']:02d} - {s_book_title} - {s_chapter_title}{part_str}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = generated_folder / safe_output_filename
        
        _, synthesized_text = tts.synthesize(final_content, str(output_filepath), debug_level=debug_level)
        
        metadata_title = chapter_details.get('original_title', chapter_details['title'])
        if part_info[1] > 1:
            metadata_title += f" (Part {part_info[0]} of {part_info[1]})"
            
        tag_mp3_file(str(output_filepath), {'title': metadata_title, 'author': book_metadata.get("author"), 'book_title': book_metadata.get("title")}, voice_name=voice_name)
        
        text_filename = output_filepath.with_suffix('.txt').name
        (generated_folder / text_filename).write_text(synthesized_text, encoding="utf-8")
        
        current_app.logger.debug(f"[Task:{self.request.id}] Chapter {chapter_details['number']} completed.")
        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}

    except Exception as e:
        current_app.logger.error(f"Chapter processing failed in task {self.request.id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise e

@celery.task(bind=True)
def convert_to_speech_task(self, input_filepath, original_filename, book_title, book_author, voice_name, speed_rate, debug_level='off'):
    """Celery task for processing a single file that is not chapterized."""
    temp_cover_path = None
    generated_folder = Path(current_app.config['GENERATED_FOLDER'])
    try:
        self.update_state(state='PROGRESS', meta={'status': 'Starting single file conversion...'})
        current_app.logger.debug(f"[Task:{self.request.id}] Running single-file conversion for {original_filename}")
        
        text_content, _ = extract_text_and_metadata(input_filepath)
        if not text_content:
            raise ValueError("Could not extract any text from the source file.")

        cleaned_text = text_cleaner.clean_text(text_content, debug_level=debug_level)
        
        self.update_state(state='PROGRESS', meta={'status': 'Normalizing text...'})
        normalized_main_text = normalize_text(cleaned_text, debug_level=debug_level)
        
        enhanced_metadata = fetch_enhanced_metadata(book_title, book_author)
        unnormalized_title_page = create_title_page_text(enhanced_metadata)
        normalized_title_page = normalize_text(unnormalized_title_page, debug_level=debug_level)
        final_content_for_synthesis = normalized_title_page + normalized_main_text

        self.update_state(state='PROGRESS', meta={'status': 'Synthesizing audio...'})
        s_book_title = clean_filename_part(enhanced_metadata.get("title", book_title))
        output_filename = f"{s_book_title}.mp3"
        safe_output_filename = secure_filename(output_filename)
        output_filepath = generated_folder / safe_output_filename
        
        full_voice_path = ensure_voice_available(voice_name, current_app.redis_client)
        tts = TTSService(voice_path=full_voice_path, speed_rate=speed_rate)
        _, synthesized_text = tts.synthesize(final_content_for_synthesis, str(output_filepath), debug_level=debug_level)
        
        cover_url = enhanced_metadata.get('cover_url', '')
        unique_id = str(uuid.uuid4().hex[:8])
        temp_cover_path = generated_folder / f"cover_{unique_id}.jpg"
        cover_path_to_use = None
        if cover_url:
            try:
                response = requests.get(cover_url, stream=True)
                response.raise_for_status()
                with open(temp_cover_path, 'wb') as f:
                    shutil.copyfileobj(response.raw, f)
                cover_path_to_use = temp_cover_path
            except requests.RequestException:
                pass

        if not cover_path_to_use:
            if create_generic_cover_image(enhanced_metadata.get("title"), enhanced_metadata.get("author"), temp_cover_path):
                cover_path_to_use = temp_cover_path
        
        self.update_state(state='PROGRESS', meta={'status': 'Tagging and Saving...'})
        tag_mp3_file(
            str(output_filepath), 
            {'title': enhanced_metadata.get("title"), 'author': enhanced_metadata.get("author"), 'book_title': enhanced_metadata.get("title")}, 
            cover_image_path=cover_path_to_use, 
            voice_name=voice_name
        )
        
        text_filename = output_filepath.with_suffix('.txt').name
        (generated_folder / text_filename).write_text(synthesized_text, encoding="utf-8")

        return {'status': 'Success', 'filename': safe_output_filename, 'textfile': text_filename}

    except Exception as e:
        current_app.logger.error(f"Single-file conversion failed in task {self.request.id}: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'exc_type': type(e).__name__, 'exc_message': str(e)})
        raise
    finally:
        if os.path.exists(input_filepath):
            os.remove(input_filepath)
        if temp_cover_path and os.path.exists(str(temp_cover_path)):
            os.remove(temp_cover_path)

@celery.task(bind=True)
def create_audiobook_task(self, file_list, audiobook_title, audiobook_author, cover_url=None):
    """Celery task to merge multiple MP3s into a single M4B audiobook."""
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
