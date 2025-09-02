# Stage 1: Builder for fetching Piper assets
FROM fedora:42 AS builder

# Install build tools
RUN dnf -y install wget && dnf clean all

# Prepare voice directory
WORKDIR /voices

# Download high-quality voice models
RUN wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx.json && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/norman/medium/en_US-norman-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/norman/medium/en_US-norman-medium.onnx.json


# Stage 2: Final Application Image
FROM fedora:42

# Prepare app environment
WORKDIR /app

# Copy requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install system dependencies AND heavy Python binaries from DNF for speed and efficiency
RUN dnf -y install \
    python3 \
    python3-pip \
    poppler-utils \
    ffmpeg \
    espeak-ng \
    python3-requests \
    python3-torch \
    python3-onnxruntime \
    python3-sentencepiece \
    python3-flask \
    python3-celery \
    python3-redis \
    python3-docx \
    python3-ebooklib \
    python3-PyMuPDF \
    python3-beautifulsoup4 \
    python3-inflect \
    python3-mutagen \
    && dnf clean all \
    && python3 -m venv --system-site-packages /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && python -c "\
from argostranslate import package;\
package.update_package_index();\
available_packages = package.get_available_packages();\
package_to_install = next(filter(lambda x: x.from_code == 'he' and x.to_code == 'en', available_packages));\
package.install_from_path(package_to_install.download());\
"

# Set up environment for subsequent commands
ENV PATH="/opt/venv/bin:$PATH"

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

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
