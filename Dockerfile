# Stage 1: Final Application Image
# Pull the pre-built base image from the GitHub Container Registry.
# The GITHUB_REPOSITORY and BASE_IMAGE_TAG arguments are passed in by the GitHub Actions workflow.
ARG GITHUB_REPOSITORY
ARG BASE_IMAGE_TAG=latest
FROM ghcr.io/${GITHUB_REPOSITORY}-base:${BASE_IMAGE_TAG}

# The WORKDIR and PATH are inherited from the base image.

# Copy requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install any remaining small Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY celery_config.py .
COPY tts_service.py .
COPY normalization.json .
COPY templates ./templates

# Expose port for the web application
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
