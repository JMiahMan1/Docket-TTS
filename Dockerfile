# 1. Start with a stable, minimal base image
FROM debian:bookworm-slim

# 2. Install dependencies, including ca-certificates for SSL verification
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    tar \
    poppler-utils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. Download the specific Piper binary you provided
RUN wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_amd64.tar.gz && \
    tar -zxvf piper_amd64.tar.gz && \
    # Move the ENTIRE piper folder to a permanent location
    mv ./piper /opt/piper && \
    # Create a symlink so the 'piper' command is in the system PATH
    ln -s /opt/piper/piper /usr/local/bin/piper && \
    rm -rf piper_amd64.tar.gz

# 4. Tell the system where to find Piper's shared library files
ENV LD_LIBRARY_PATH=/opt/piper/lib

# 5. Set a working directory and download the default voice model
WORKDIR /app
RUN wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx && \
    wget -q https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# 6. Copy the entrypoint script
COPY tts.sh .
RUN chmod +x tts.sh

<<<<<<< Updated upstream
# 7. Set the entrypoint
ENTRYPOINT ["./tts.sh"]
=======
# Install Python dependencies
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

# Start Gunicorn with an increased timeout for large file downloads
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
>>>>>>> Stashed changes
