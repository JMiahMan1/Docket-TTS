Docker TTS with Piper 🗣️
This project provides a simple, self-contained Docker solution to convert text files (.txt) and PDF files (.pdf) into speech (.mp3) using the high-quality, local Piper TTS engine. It is controlled by a simple shell script for easy use.

## Features
Versatile Input: Converts both .txt and .pdf files.

High-Quality Voice: Uses the fast and natural-sounding Piper TTS engine.

Containerized: Fully containerized with Docker for easy setup and portability. The environment is consistent and works anywhere Docker is installed.

Simple Usage: A single, straightforward command-line script handles the entire process.

MP3 Output: Generates a standard .mp3 audio file.

Robust Error Handling: The main script provides clear error messages if the conversion fails.

## Prerequisites
Before you begin, ensure you have the following installed on your host machine:

Docker Engine: Installation Guide

Docker Compose: Installation Guide

A bash-compatible shell: Standard on Linux and macOS.

pdftotext: This utility is required to extract text from PDF files. It is part of the poppler-utils package.

On Debian/Ubuntu: sudo apt-get update && sudo apt-get install poppler-utils

On Fedora/CentOS: sudo dnf install poppler-utils

On macOS (with Homebrew): brew install poppler

## Project Structure
The repository contains the following key files:

.
├── docker-compose.yml   # Defines and configures the Docker service.
├── Dockerfile           # Builds the Docker image with Piper and all dependencies.
├── speak.sh             # The main script you run on your machine to convert files.
└── tts.sh               # The internal script that runs inside the container to perform the conversion.
## Setup
Follow these steps to set up the project. You only need to do this once.

Make the Script Executable Open your terminal in the project directory and run the following command:

Bash

chmod +x speak.sh
Build the Docker Image This command builds the Docker image, downloading the Piper engine and all its dependencies.

Bash

docker compose build
## Usage
To convert a file, use the speak.sh script. It takes two arguments: the input file and the desired output filename.

Syntax:

Bash

./speak.sh <input-file.pdf|txt> <output-file.mp3>
Example (Converting a PDF):

Bash

./speak.sh my_document.pdf my_audiobook.mp3
Example (Converting a Text File):

Bash

./speak.sh my_notes.txt my_notes_audio.mp3
The script will process the file and save the resulting audio to the output path you specified.

## Customization: Changing the Voice 🎤
You can easily change the voice model by modifying the Dockerfile.

Find a New Voice Browse the official Piper Voice Library on Hugging Face:

https://huggingface.co/rhasspy/piper-voices/tree/main

Get the Download Links Navigate to the voice you want (e.g., en_GB/vctk/medium). You will need the download links for both the .onnx file and the .onnx.json file. Right-click the "download" button for each file and copy the link address.

Edit the Dockerfile Open the Dockerfile and find the section for downloading the voice model (Step #5). Replace the two wget URLs with the new links you copied.

Rebuild the Image Save the Dockerfile and run the build command again to create a new image with the new voice.

Bash

docker compose build
All subsequent conversions will now use the new voice.
