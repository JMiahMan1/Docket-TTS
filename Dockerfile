# Stage 1: Builder
FROM fedora:42 AS builder

# Install build tools and dependencies
RUN dnf -y install \
    python3 \
    python3-pip \
    python3-devel \
    wget \
    ca-certificates \
    espeak-ng \
    poppler-utils \
    ffmpeg \
    && dnf clean all

# Upgrade pip
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# Install Piper via pip (includes the CLI)
RUN pip3 install --no-cache-dir piper-tts

# Stage 2: Final Image
FROM fedora:42

# Install runtime dependencies
RUN dnf -y install \
    python3 \
    python3-virtualenv \
    wget \
    poppler-utils \
    ffmpeg \
    espeak-ng \
    && dnf clean all

# Prepare app environment
WORKDIR /app

# Set up Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip inside venv
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install Python dependencies (app requirements)
RUN pip install --no-cache-dir \
    Flask \
    python-docx \
    EbookLib \
    beautifulsoup4 \
    gunicorn \
    celery \
    redis \
    inflect \
    pyttsx3

# Download voice model (optional; baked into image)
RUN wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx.json

# Copy application files
COPY app.py .
COPY celery_config.py .
#COPY text_formatter.py .
COPY tts_service.py .
COPY normalization.json .
COPY templates ./templates

# Expose port
EXPOSE 5000

# Default CMD: start your Gunicorn app
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:app"]
