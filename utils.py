# /app/utils.py

import os
import re
import shutil
import time
import textwrap
import requests
from pathlib import Path
from datetime import datetime, timezone

import fitz  # PyMuPDF
import docx
from bs4 import BeautifulSoup
from ebooklib import epub
import ebooklib
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, COMM, APIC
from PIL import Image, ImageDraw, ImageFont
from flask import current_app
from huggingface_hub import list_repo_files, hf_hub_download
from werkzeug.utils import secure_filename
import subprocess
import uuid # Added for consistency with tasks.py logic
# REMOVED: from difflib import SequenceMatcher # The _similar function is no longer needed

VOICES_FOLDER = '/app/voices'
PIPER_VOICES_REPO = "rhasspy/piper-voices"
CACHED_PIPER_VOICES = None
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'} # Added for consistency

def get_piper_voices():
    global CACHED_PIPER_VOICES
    if CACHED_PIPER_VOICES is not None:
        return CACHED_PIPER_VOICES
    
    current_app.logger.info("Fetching Piper voice list from Hugging Face Hub...")
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
        current_app.logger.info(f"Successfully fetched and cached {len(CACHED_PIPER_VOICES)} voices.")
        return CACHED_PIPER_VOICES
    except Exception as e:
        current_app.logger.error(f"Could not fetch voices from Hugging Face Hub: {e}")
        return list_available_voices()

# REMOVED: def _similar(a, b): ... function definition removed.

# Function logic re-integrated from app_old.py, adapted to take redis_client
def ensure_voice_available(voice_name, redis_client):
    all_voices = get_piper_voices()
    voice_info = next((v for v in all_voices if v['id'] == voice_name), None)
    
    if not voice_info:
        raise ValueError(f"Could not find metadata for voice '{voice_name}' to download.")

    full_voice_path = Path(VOICES_FOLDER) / voice_info['repo_path']
    full_config_path = full_voice_path.with_suffix(full_voice_path.suffix + ".json")

    if full_voice_path.exists() and full_config_path.exists():
        current_app.logger.info(f"Voice '{voice_name}' found locally at {full_voice_path}")
        return str(full_voice_path)

    if not redis_client:
        current_app.logger.warning("Redis client not available. Proceeding without lock. This may cause issues with parallel downloads.")
    
    lock_key = f"lock:voice-download:{voice_name}"
    lock_acquired = False
    
    try:
        wait_start_time = time.time()
        while time.time() - wait_start_time < 120:
            if redis_client:
                # FIX: Use 60s timeout as in app_old.py
                lock_acquired = redis_client.set(lock_key, "1", nx=True, ex=60)
            
            if lock_acquired:
                current_app.logger.info(f"Acquired lock for downloading voice '{voice_name}'.")
                break
            else:
                current_app.logger.info(f"Waiting for lock on voice '{voice_name}'...")
                time.sleep(2)
        else:
            raise RuntimeError(f"Could not acquire lock for voice '{voice_name}' after 2 minutes.")
        
        # Check again in case another worker finished
        if full_voice_path.exists() and full_config_path.exists():
            current_app.logger.info(f"Voice '{voice_name}' was downloaded by another worker while waiting for lock.")
            return str(full_voice_path)

        current_app.logger.warning(f"Voice '{voice_name}' not found locally. Starting download...")
        hf_hub_download(repo_id=PIPER_VOICES_REPO, filename=voice_info['repo_path'], local_dir=VOICES_FOLDER, local_dir_use_symlinks=False)
        hf_hub_download(repo_id=PIPER_VOICES_REPO, filename=voice_info['repo_path'] + ".json", local_dir=VOICES_FOLDER, local_dir_use_symlinks=False)
        current_app.logger.info(f"Successfully downloaded voice: {voice_name}")
        return str(full_voice_path)
    except Exception as e:
        current_app.logger.error(f"Failed to ensure voice {voice_name} is available: {e}")
        raise
    finally:
        if lock_acquired and redis_client:
            redis_client.delete(lock_key)
            current_app.logger.info(f"Released lock for voice '{voice_name}'.")

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
            mime = 'image/jpeg' if Path(cover_image_path).suffix.lower() == '.jpg' else 'image/png'
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=image_data))
        audio.save()
        current_app.logger.info(f"Successfully tagged {filepath}")
    except Exception as e:
        current_app.logger.error(f"Failed to tag {filepath}: {e}")

# Re-integrated from app_old.py
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

# Re-integrated from app_old.py
def extract_text_and_metadata(filepath):
    p_filepath = Path(filepath)
    extension = p_filepath.suffix.lower()
    text = ""
    metadata = {'title': p_filepath.stem.replace('_', ' ').title(), 'author': 'Unknown'}
    
    # --- DEBUG LOGGING START ---
    current_app.logger.debug(f"DEBUG: Starting extraction from {filepath}. Initial title from filename: '{metadata['title']}'")
    # --- DEBUG LOGGING END ---
    
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
            # NOTE: This only extracts text for fallbacks. The new chapterizer uses the book object itself.
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
        current_app.logger.error(f"Error extracting text and metadata from {filepath}: {e}")
        return "", metadata
    
    # Post-extraction metadata cleanup
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
    
    current_app.logger.debug(f"DEBUG: Finished extraction. Extracted Title: '{metadata['title']}', Extracted Author: '{metadata['author']}'")
    
    return text, metadata

# Re-integrated from app_old.py
def fetch_enhanced_metadata(title, author):
    # CRITICAL: Always start by trusting the locally extracted title and author.
    metadata = {
        'title': title, 'subtitle': None, 'author': author,
        'publisher': None, 'published_date': None, 'cover_url': ''
    }
    
    if not title or title == "Unknown": 
        return metadata

    # Use a stripped-down title for the API query to maximize cover search success.
    # For "Preach the Word _ Essays on Expository Preaching - In Honor.epub", we query on "Preach the Word".
    query_title = title.split(':')[0].strip().split('_')[0].split(' - ')[0].strip()

    if not query_title:
        query_title = title 
        
    # --- DEBUG LOGGING START ---
    current_app.logger.debug(f"DEBUG: Starting Google Books query.")
    current_app.logger.debug(f"DEBUG: Original Title: '{title}', Original Author: '{author}'")
    current_app.logger.debug(f"DEBUG: Query Title (Simplified): '{query_title}'")
    # --- DEBUG LOGGING END ---
        
    try:
        query = f"intitle:{query_title}"
        if author and author != 'Unknown': query += f"+inauthor:{author}"
        
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=1"
        current_app.logger.debug(f"DEBUG: Final API Query URL: {url}")
        
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        if data.get('totalItems', 0) > 0:
            book_info = data['items'][0]['volumeInfo']
            
            api_title = book_info.get('title', 'N/A')
            api_authors = book_info.get('authors', [])
            api_cover_url = book_info.get('imageLinks', {}).get('thumbnail', '')
            
            # --- DEBUG LOGGING START ---
            current_app.logger.debug(f"DEBUG: API Match Found. API Title: '{api_title}'")
            current_app.logger.debug(f"DEBUG: API Authors: {api_authors}")
            current_app.logger.debug(f"DEBUG: API Cover URL: {api_cover_url[:40]}...")
            # --- DEBUG LOGGING END ---
            
            # CRITICAL FIX: DO NOT overwrite 'title' or 'author'. Only update auxiliary fields and cover URL.
            metadata.update({
                'subtitle': book_info.get('subtitle'),
                'publisher': book_info.get('publisher'),
                'published_date': book_info.get('publishedDate'),
                'cover_url': api_cover_url
            })
            
            current_app.logger.info(f"Google Books API found enhanced cover/details for '{title}'")
            
        else:
             current_app.logger.info(f"Google Books API found no match for query: '{query_title}'.")
            
    except requests.RequestException as e:
        current_app.logger.error(f"Google Books API request failed: {e}")
    return metadata

# Re-integrated from app_old.py
def list_available_voices():
    voices = []
    voice_dir = Path(VOICES_FOLDER)
    if voice_dir.is_dir():
        for voice_file in voice_dir.glob("*.onnx"):
            voices.append({"id": voice_file.name, "name": voice_file.stem})
    return sorted(voices, key=lambda v: v['name'])

# Re-integrated from app_old.py
def clean_filename_part(name_part):
    s_name = re.sub(r'[^\w\s-]', '', name_part)
    s_name = re.sub(r'[-\s]+', ' ', s_name).strip()
    return s_name[:40]

# Re-integrated from app_old.py
def create_title_page_text(metadata):
    parts = []
    title_parts = []
    if metadata.get('title'): title_parts.append(metadata['title'].strip().rstrip('.'))
    if metadata.get('subtitle'): title_parts.append(metadata['subtitle'].strip().rstrip('.'))
    if title_parts: parts.append(" ".join(title_parts) + ".")
    if metadata.get('author'): parts.append(f"By {metadata['author']}.")
    if metadata.get('publisher'): parts.append(f"Published by {metadata['publisher']}.")
    if metadata.get('published_date'):
        year_match = re.search(r'\d{4}', metadata['published_date'])
        if year_match: parts.append(f"Copyright {year_match.group(0)}.")
    return " ".join(parts) + "\n\n" if parts else ""

# Re-integrated from app_old.py
def create_generic_cover_image(title, author, save_path):
    try:
        # Use Image import from the top of the file
        from PIL import Image, ImageDraw, ImageFont 
        width, height = 800, 1200
        image = Image.new('RGB', (width, height), color = (73, 109, 137))
        draw = ImageDraw.Draw(image)
        try:
            font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", size=60)
            font_author = ImageFont.truetype("DejaVuSans.ttf", size=40)
        except IOError:
            font_title = ImageFont.load_default()
            font_author = ImageFont.load_default()
        # Use textwrap import from the top of the file
        import textwrap 
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
        current_app.logger.error(f"Failed to create generic cover image: {e}")
        return None

# Re-integrated from app_old.py
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

    current_app.logger.info(f"Using metadata for M4B: Title='{final_audiobook_title}', Author='{final_audiobook_author}'")

    update_state(state='PROGRESS', meta={'current': 1, 'total': 5, 'status': 'Gathering chapters and text...'})
    safe_mp3_paths = [generated_folder / secure_filename(fname) for fname in unique_file_list]
    merged_text_content = "".join(p.with_suffix('.txt').read_text(encoding='utf-8') + "\n\n" for p in safe_mp3_paths if p.with_suffix('.txt').exists())
    update_state(state='PROGRESS', meta={'current': 2, 'total': 5, 'status': 'Downloading cover art...'})
    cover_path = None
    if cover_url:
        try:
            import requests # Use requests import from the top of the file
            response = requests.get(cover_url, stream=True)
            response.raise_for_status()
            cover_path = build_dir / "cover.jpg"
            import shutil # Use shutil import from the top of the file
            with open(cover_path, 'wb') as f: shutil.copyfileobj(response.raw, f)
        except requests.RequestException as e:
            current_app.logger.error(f"Failed to download cover art: {e}")
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
    import subprocess # Use subprocess import from the top of the file
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
