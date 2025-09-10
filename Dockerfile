# Stage 1: Define build arguments passed from Docker Compose or CI
ARG GITHUB_REPOSITORY
ARG BASE_IMAGE_TAG=latest

# Pull the pre-built base image from the GitHub Container Registry.
FROM ghcr.io/${GITHUB_REPOSITORY}-base:${BASE_IMAGE_TAG} AS base

# Stage 2: Create a 'builder' stage to create the venv and install all Python dependencies
FROM base AS builder
# Create the virtual environment
RUN python3 -m venv /opt/venv
# Set the PATH to use the venv's binaries for this stage and any subsequent stages
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
# Install dependencies into the virtual environment
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: Create the final application image
FROM base
WORKDIR /app
# Set the PATH for the final image
ENV PATH="/opt/venv/bin:$PATH"
# Copy the installed dependencies from the 'builder' stage
COPY --from=builder /opt/venv /opt/venv
# Copy the rest of the application code
COPY . .

# Expose the application port
EXPOSE 5000

# Default command to run the application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "300", "app:app"]
