# Stage 1: Builder for fetching Piper assets
FROM fedora:42 AS builder

# Install build tools
RUN dnf -y install wget && dnf clean all

# Prepare voice directory
WORKDIR /voices

# Download a high-quality default voice model
# This is the recommended US English male voice from the original Dockerfile
RUN wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx.json


# Stage 2: Final Application Image
FROM fedora:42

# Install runtime dependencies for Piper and the web app
RUN dnf -y install \
    python3 \
    python3-virtualenv \
    poppler-utils \
    ffmpeg \
    espeak-ng \
    ghostscript \
    && dnf clean all

# Prepare app environment
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH"

# Set up and activate Python virtual environment
RUN python3 -m venv /opt/venv
RUN . /opt/venv/bin/activate

# Install Python dependencies
# Removed pyttsx3, kept inflect and other necessary libraries
RUN pip install --no-cache-dir \
    Flask \
    gunicorn \
    celery \
    redis \
    python-docx \
    EbookLib \
    PyMuPDF \
    mutagen \
    beautifulsoup4 \
    inflect \
    piper-tts

# Copy voice models from the builder stage
COPY --from=builder /voices /app/voices

# Copy application files
COPY app.py .
COPY celery_config.py .
COPY tts_service.py .
COPY normalization.json .
COPY templates ./templates

# Expose port for the web application
EXPOSE 5000

# Start the Gunicorn web server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
