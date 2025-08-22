#!/bin/bash
# tts.sh

# Exit immediately if a command in a pipeline fails
set -o pipefail

# Call piper with the correct, documented flags
piper \
  --model /app/en_US-lessac-medium.onnx \
  --output-raw | \
ffmpeg -f s16le -ar 22050 -ac 1 -i - -f mp3 -q:a 0 -
