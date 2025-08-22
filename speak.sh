#!/bin/bash
# speak.sh

# Exit immediately if a command in a pipeline fails
set -o pipefail

# --- Usage ---
# ./speak.sh <input_file.txt|input_file.pdf> <output_file.mp3>
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <input_file.txt|pdf> <output_file.mp3>"
    exit 1
fi

INPUT_FILE="$1"
OUTPUT_FILE="$2"

if [ ! -f "$INPUT_FILE" ]; then
    echo "❌ Error: Input file '$INPUT_FILE' not found."
    exit 1
fi

echo "Processing '$INPUT_FILE'..."

# --- Run the conversion and capture the result ---
# The temporary file is used to capture any error messages from the container
TEMP_STDERR=$(mktemp)
if [[ "$INPUT_FILE" == *.pdf ]]; then
    pdftotext "$INPUT_FILE" - | docker compose run --rm --no-deps tts-converter > "$OUTPUT_FILE" 2> "$TEMP_STDERR"
elif [[ "$INPUT_FILE" == *.txt ]]; then
    cat "$INPUT_FILE" | docker compose run --rm --no-deps tts-converter > "$OUTPUT_FILE" 2> "$TEMP_STDERR"
else
    echo "❌ Error: Unsupported file type. Please use a .txt or .pdf file."
    # Clean up the temp file before exiting
    rm -f "$TEMP_STDERR"
    exit 1
fi

# --- Check the exit code and report success or failure ---
EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ] && [ -s "$OUTPUT_FILE" ]; then
    echo "✅ Success! Audio saved to '$OUTPUT_FILE'"
else
    echo "❌ Error: The conversion failed. See details below."
    echo "------------------- Docker Log -------------------"
    cat "$TEMP_STDERR"
    echo "------------------------------------------------"
    # Clean up the empty output file on failure
    rm -f "$OUTPUT_FILE"
fi

# Clean up the temporary log file
rm -f "$TEMP_STDERR"

exit $EXIT_CODE
