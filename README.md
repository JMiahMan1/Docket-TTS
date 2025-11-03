# Docker TTS with Kokoro-TTS and Audiobook Tools 🗣️📖

This project provides a simple, self-contained web application to convert various document types (`.txt`, `.pdf`, `.docx`, `.epub`) into high-quality speech (`.mp3`) using the local **Kokoro-TTS** engine. It also includes a feature to merge multiple generated MP3 files into a single, tagged M4B audiobook.

The entire application is containerized with Docker and managed via Docker Compose for easy setup and deployment.

## ✨ Features

-   **Web Interface**: Modern, easy-to-use web UI for uploading files and managing generated audio.
    
-   **Versatile Input**: Converts `.txt`, `.pdf`, `.docx`, and `.epub` files or pasted text.
    
-   **High-Quality Voices**: Uses the fast and natural-sounding Kokoro-TTS engine, with support for multiple voices.
    
-   **Metadata Extraction**: Extracts Title and Author from document properties and text to tag the final audio files.
    
-   **Audiobook Creation**: Merge multiple generated MP3 files into a single M4B audiobook directly from the file management page.
    
-   **Background Processing**: Uses Celery and Redis to handle long conversions without timing out, with a real-time job status page.
    
-   **Containerized**: Fully containerized for easy setup and portability. The environment is consistent and works anywhere Docker is installed.
    

## 🔧 Prerequisites

Before you begin, ensure you have the following installed on your host machine:

-   **Docker Engine** → [Installation Guide](https://docs.docker.com/engine/install/ "null")
    
-   **Docker Compose** → [Installation Guide](https://docs.docker.com/compose/install/ "null")
    

## 📂 Project Structure

```
.
├── app.py
├── celery_config.py
├── chapterizer.py
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.base
├── normalization.json
├── pytest.ini
├── README.md
├── requirements.txt
├── rules.yaml
├── templates/
│   ├── debug.html
│   ├── files.html
│   ├── index.html
│   ├── jobs.html
│   └── result.html
├── tests/
│   ├── run_tests.sh
│   ├── test_audiobook_creation.py
│   ├── test_auto_book_mode.py
│   ├── test_chapterizer.py
│   ├── test_chapter_processing.py
│   ├── test_deployment.py
│   ├── test_functionality.py
│   ├── test_normalization_edge_cases.py
│   ├── test_normalization.py
│   ├── test_sample_generation.py
│   ├── test_task_logic.py
│   ├── test_text_cleaner.py
│   └── test_uploads.py
├── text_cleaner.py
└── tts_service.py

```

## ⚙️ Setup

Follow these steps to set up and run the project.

### 1. Build and Run the Services

This single command builds the Docker images and starts the web server, background worker, and Redis services.

```
docker compose up --build
```

The application will be available at [http://localhost:8000](https://www.google.com/search?q=http://localhost:8000 "null").

To stop the services, press **Ctrl+C** in the terminal, and then run:

```
docker compose down
```

## ▶️ Usage

### Converting a Document to Speech

1.  Navigate to [http://localhost:8000](https://www.google.com/search?q=http://localhost:8000 "null") in your web browser.
    
2.  Choose a document file (`.txt`, `.pdf`, `.docx`, or `.epub`) or paste in text.
    
3.  Select a voice from the dropdown menu. You can click the **Sample** button to hear a preview.
    
4.  Click **Convert to Speech**.
    
5.  You will be redirected to the job status page. Once complete, click "View Generated Files".
    

### Creating an M4B Audiobook

1.  From the home page or job page, navigate to the **View Generated Files** page.
    
2.  This page lists all the audio files you have created.
    
3.  Use the checkboxes to select two or more MP3 files that you want to combine into an audiobook.
    
4.  Click the **Create Audiobook from Selected** button.
    
5.  A modal will pop up allowing you to confirm or edit the Title and Author.
    
6.  You will be redirected to a progress page. When the process is finished, a download link for your new `.m4b` audiobook file will appear.
