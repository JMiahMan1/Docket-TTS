# Docker TTS with Piper and Audiobook Tools 🗣️📖

This project provides a simple, self-contained web application to convert various document types (`.txt`, `.pdf`, `.docx`, `.epub`) into high-quality speech (`.mp3`) using the local **Piper TTS** engine. It also includes a feature to merge multiple generated MP3 files into a single, tagged M4B audiobook.

The entire application is containerized with Docker and managed via Docker Compose for easy setup and deployment.

---

## ✨ Features
- **Web Interface**: Modern, easy-to-use web UI for uploading files and managing generated audio.
- **Versatile Input**: Converts `.txt`, `.pdf`, `.docx`, and `.epub` files.
- **High-Quality Voices**: Uses the fast and natural-sounding Piper TTS engine, with support for multiple voices.
- **Metadata Extraction**: Automatically extracts Title and Author from document properties and text to tag the final audio files.
- **Audiobook Creation**: Merge multiple generated MP3 files into a single M4B audiobook directly from the file management page.
- **Background Processing**: Uses Celery and Redis to handle long conversions without timing out, with a real-time progress bar.
- **Containerized**: Fully containerized for easy setup and portability. The environment is consistent and works anywhere Docker is installed.

---

## 🔧 Prerequisites
Before you begin, ensure you have the following installed on your host machine:

- **Docker Engine** → [Installation Guide](https://docs.docker.com/engine/install/)  
- **Docker Compose** → [Installation Guide](https://docs.docker.com/compose/install/)  

---

## 📂 Project Structure
```
.
├── app.py               # Main Flask web application
├── tts_service.py       # Handles text normalization and Piper TTS synthesis
├── celery_config.py     # Configuration for the Celery background worker
├── templates/           # HTML templates for the web interface
├── Dockerfile           # Builds the Docker image with all dependencies
├── docker-compose.yml   # Defines and configures the Docker services (web, worker, redis)
└── README.md            # This file
```

---

## ⚙️ Setup
Follow these steps to set up and run the project.

### 1. Build and Run the Services
This single command builds the Docker images and starts the web server, background worker, and Redis services.

```bash
docker compose up --build
```

The application will be available at [http://localhost:8000](http://localhost:8000).

To stop the services, press **Ctrl+C** in the terminal, and then run:

```bash
docker compose down
```

---

## ▶️ Usage

### Converting a Document to Speech
1. Navigate to [http://localhost:8000](http://localhost:8000) in your web browser.  
2. Choose a document file (`.txt`, `.pdf`, `.docx`, or `.epub`) to upload.  
3. Select a voice from the dropdown menu. You can click the **Sample** button to hear a preview.  
4. Click **Convert to Speech**.  
5. You will be redirected to a progress page. Once complete, you will see download links for the generated MP3 and the normalized text file.  

### Creating an M4B Audiobook
1. From the home page or result page, navigate to the **View Generated Files** page.  
2. This page lists all the audio files you have created.  
3. Use the checkboxes to select two or more MP3 files that you want to combine into an audiobook.  
4. Click the **Merge Selected to Audiobook** button.  
5. You will be redirected to the progress page. When the process is finished, a download link for your new `.m4b` audiobook file will appear.  
   - The audiobook will be tagged with the title and author from the first MP3 file selected.  

---

## 🎤 Customization: Changing Voices
You can easily add or change the available voice models by modifying the `Dockerfile`.

1. **Find a New Voice**: Browse the [Piper Voice Library](https://huggingface.co/rhasspy/piper-voices).  
2. **Get Download Links**: Navigate to the voice you want (e.g., `en_GB/vctk/medium`). Copy the link addresses for both the `.onnx` and `.onnx.json` files.  
3. **Edit the Dockerfile**: Open the `Dockerfile` and find the section labeled  
   ```dockerfile
   # Download high-quality voice models
   ```  
   Add or replace the `wget` URLs with the new links.  
4. **Rebuild the Image**: Save the `Dockerfile` and run:  
   ```bash
   docker compose up --build --force-recreate
   ```  
   All subsequent conversions will have the new voice available.  

