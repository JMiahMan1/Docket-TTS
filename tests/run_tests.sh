#!/bin/bash
# run_tests.sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Installing test dependencies ---"
# Install app dependencies from requirements.txt and the pytest package
pip install --no-cache-dir -r requirements.txt pytest Flask gunicorn celery redis python-docx EbookLib PyMuPDF beautifulsoup4 inflect mutagen argostranslate requests pytest-xdist

echo "--- Downloading Argos Translate model for testing ---"
# This step is crucial to ensure the test environment has the required model
python -c "\
from argostranslate import package;\
package.update_package_index();\
available_packages = package.get_available_packages();\
package_to_install = next(filter(lambda x: x.from_code == 'he' and x.to_code == 'en', available_packages));\
package.install_from_path(package_to_install.download());\
"

# Check if a custom URL is provided, otherwise use the default
if [ -z "$APP_BASE_URL" ]; then
  export APP_BASE_URL="http://localhost:8000"
fi

echo "--- Running tests against $APP_BASE_URL ---"

# Add the project root to the Python path to allow tests to import app modules
export PYTHONPATH=.

# Run pytest against the /tests directory. It will find all test_*.py files.
# Run pytest against the files in a specific order: fastest to slowest.
pytest -v -n auto \
  tests/test_deployment.py \
  tests/test_normalization.py \
  tests/test_functionality.py \
  tests/test_audiobook_creation.py \
  tests/test_metadata.py

pytest -v tests/

echo "--- All tests passed successfully! ---"
