# Stage 1: Final Application Image
# Pull the pre-built base image from the GitHub Container Registry.
# The GITHUB_REPOSITORY and BASE_IMAGE_TAG arguments are passed in by the GitHub Actions workflow.
ARG GITHUB_REPOSITORY
FROM ghcr.io/${GITHUB_REPOSITORY}-base:latest AS base

ARG GITHUB_REPOSITORY
ARG BASE_IMAGE_TAG=latest
FROM docket-tts-base:latest AS base

# Create and activate a virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Build Layer: Install dependencies
FROM base AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final Application Image
FROM base
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY . .

# Expose the application port
EXPOSE 5000

# Default command to run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
