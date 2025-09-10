# Stage 1: Define build arguments passed from Docker Compose or CI
ARG GITHUB_REPOSITORY
ARG BASE_IMAGE_TAG=latest

# Pull the pre-built base image which already contains /opt/venv
FROM ghcr.io/${GITHUB_REPOSITORY}-base:${BASE_IMAGE_TAG} AS base

# Stage 2: Create a 'builder' stage to install ONLY application-specific dependencies
FROM base AS builder
WORKDIR /app
COPY requirements.txt .
# This pip command now correctly uses the venv from the base image
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: Create the final application image
FROM base
WORKDIR /app
# Copy the updated venv (with gunicorn and filetype) from the 'builder' stage
COPY --from=builder /opt/venv /opt/venv
# Copy the rest of the application code
COPY . .

# Expose the application port
EXPOSE 5000

# Default command to run the application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
