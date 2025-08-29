#!/bin/bash
# run_tests.sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Installing test dependencies ---"
# Install app dependencies from requirements.txt and the pytest package
pip install --no-cache-dir -r requirements.txt pytest Flask gunicorn celery redis python-docx EbookLib PyMuPDF beautifulsoup4 inflect mutagen argostranslate requests

# Check if a custom URL is provided, otherwise use the default
if [ -z "$APP_BASE_URL" ]; then
  export APP_BASE_URL="http://localhost:8000"
fi

echo "--- Running tests against $APP_BASE_URL ---"

# Add the project root to the Python path to allow tests to import app modules
export PYTHONPATH=.

# Run pytest against the /tests directory. It will find all test_*.py files.
pytest -v tests/

echo "--- All tests passed successfully! ---"
