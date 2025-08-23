# 1. Start with a stable Python base image
FROM python:3.11-slim

# 2. Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    tar \
    poppler-utils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. Set a working directory
WORKDIR /app

# 4. Install Python libraries, now including Celery and Redis
RUN pip install --no-cache-dir \
    Flask \
    python-docx \
    EbookLib \
    beautifulsoup4 \
    gunicorn \
    celery \
    redis

# 5. Download and install the Piper TTS binary
RUN wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_amd64.tar.gz && \
    tar -zxvf piper_amd64.tar.gz && \
    mv ./piper /opt/piper && \
    ln -s /opt/piper/piper /usr/local/bin/piper && \
    rm piper_amd64.tar.gz

# 6. Tell the system where to find Piper's shared library files
ENV LD_LIBRARY_PATH=/opt/piper/lib

# 7. Download the "hfc_male" voice model
RUN wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx.json

# 8. Copy the web application and templates
COPY app.py .
COPY celery_config.py .
COPY templates ./templates

# 9. Expose the port the web server will run on
EXPOSE 5000

# 10. Set the command to run the Gunicorn server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:app"]
