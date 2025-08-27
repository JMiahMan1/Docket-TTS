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
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/norman/medium/en_US-normal-medium.onnx.json


# Stage 2: Final Application Image
FROM fedora:42

# Install runtime dependencies
RUN dnf -y install \
    python3 \
    python3-virtualenv \
    poppler-utils \
    ffmpeg \
    espeak-ng \
    ghostscript \
    cmake \
    gcc-c++ \
    python3-sentencepiece \
    python3-torch \
    python3-requests \
    && dnf clean all

# Prepare app environment
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH"

# Set up and activate Python virtual environment
RUN python3 -m venv --system-site-packages /opt/venv
RUN . /opt/venv/bin/activate

# Install Python dependencies with an increased timeout
RUN pip install --no-cache-dir --timeout=600 \
    Flask \
    gunicorn \
    celery \
    redis \
    python-docx \
    EbookLib \
    PyMuPDF \
    beautifulsoup4 \
    inflect \
    piper-tts \
    mutagen \
    argostranslate

# Download and install the Hebrew to English translation model
RUN python -c "\
from argostranslate import package;\
package.update_package_index();\
available_packages = package.get_available_packages();\
package_to_install = next(filter(lambda x: x.from_code == 'he' and x.to_code == 'en', available_packages));\
package.install_from_path(package_to_install.download());\
"

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
