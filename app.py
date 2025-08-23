import os
import subprocess
import uuid
from flask import Flask, request, render_template_string, send_from_directory, flash, redirect, url_for
from werkzeug.utils import secure_filename
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
GENERATED_FOLDER = 'generated'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'epub'}
MODEL_PATH = '/app/en_US-hfc_male-medium.onnx' # Path to the new voice model

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['GENERATED_FOLDER'] = GENERATED_FOLDER
app.config['SECRET_KEY'] = 'supersecretkey' # Needed for flash messages

# --- Ensure Directories Exist ---
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_epub(epub_path):
    """Extracts text from an EPUB file."""
    book = epub.read_epub(epub_path)
    text_parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_body_content(), 'html.parser')
        text_parts.append(soup.get_text())
    return "\n".join(text_parts)

def extract_text(filepath):
    """Extracts text from a file based on its extension."""
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
            text = extract_text_from_epub(filepath)
        elif extension == 'txt':
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
    except Exception as e:
        print(f"Error extracting text from {filepath}: {e}")
        return None
    return text

# --- Web Interface HTML Template ---
HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Text-to-Speech Converter</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 40px auto; padding: 0 20px; background-color: #f4f4f4; }
        .container { background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        h1 { color: #1a1a1a; text-align: center; }
        .form-upload { margin-top: 30px; padding: 20px; border: 2px dashed #ccc; border-radius: 5px; text-align: center; }
        .form-upload input[type="file"] { width: 100%; padding: 10px; }
        .form-upload input[type="submit"] { background-color: #007bff; color: white; padding: 12px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; margin-top: 20px; transition: background-color 0.3s; }
        .form-upload input[type="submit"]:hover { background-color: #0056b3; }
        .flash-message { padding: 15px; margin-bottom: 20px; border-radius: 4px; }
        .flash-success { color: #155724; background-color: #d4edda; border: 1px solid #c3e6cb; }
        .flash-error { color: #721c24; background-color: #f8d7da; border: 1px solid #f5c6cb; }
        .download-link { display: block; text-align: center; margin-top: 30px; font-size: 18px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Piper TTS Web Interface 🔊</h1>
        <p style="text-align:center;">Upload a <code>.txt</code>, <code>.docx</code>, <code>.epub</code>, or <code>.pdf</code> file to convert it to an MP3.</p>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message flash-{{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <form method="post" enctype="multipart/form-data" class="form-upload">
            <input type="file" name="file">
            <br>
            <input type="submit" value="Upload and Convert to MP3">
        </form>

        {% if filename %}
            <div class="download-link">
                <p>✅ Conversion successful!</p>
                <a href="{{ url_for('download_file', name=filename) }}">Download {{ filename }}</a>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part in the request.', 'error')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            original_filename = secure_filename(file.filename)
            input_filepath = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
            file.save(input_filepath)

            text_content = extract_text(input_filepath)
            if not text_content:
                flash(f'Could not extract text from {original_filename}. The file might be empty or corrupted.', 'error')
                return redirect(request.url)

            unique_id = str(uuid.uuid4())
            output_filename = f"{os.path.splitext(original_filename)[0]}_{unique_id}.mp3"
            output_filepath = os.path.join(app.config['GENERATED_FOLDER'], output_filename)

            try:
                # --- MODIFIED SECTION ---
                # Combine piper and ffmpeg into a single shell pipeline for efficiency.
                # The text content is passed directly to the pipeline's standard input.
                command = (
                    f"piper --model {MODEL_PATH} --output-raw | "
                    f"ffmpeg -f s16le -ar 22050 -ac 1 -i - -f mp3 -q:a 0 {output_filepath}"
                )
                
                subprocess.run(
                    command,
                    shell=True,
                    input=text_content.encode('utf-8'),
                    check=True, # Raise an exception if the command fails
                    stderr=subprocess.PIPE # Capture error output
                )
                # --- END OF MODIFIED SECTION ---

                flash('File successfully converted!', 'success')
                return render_template_string(HTML_TEMPLATE, filename=output_filename)

            except subprocess.CalledProcessError as e:
                flash(f"An error occurred during TTS conversion: {e.stderr.decode()}", 'error')
                return redirect(request.url)
            except Exception as e:
                flash(f"An unexpected error occurred: {e}", 'error')
                return redirect(request.url)
        else:
            flash('Invalid file type. Please upload a txt, docx, epub, or pdf file.', 'error')
            return redirect(request.url)

    return render_template_string(HTML_TEMPLATE, filename=None)

@app.route('/generated/<name>')
def download_file(name):
    return send_from_directory(app.config["GENERATED_FOLDER"], name)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
