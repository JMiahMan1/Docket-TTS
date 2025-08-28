#!/bin/bash
# run_tests.sh

# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Installing test dependencies ---"
pip install --no-cache-dir pytest requests

# Check if a custom URL is provided, otherwise use the default
if [ -z "$APP_BASE_URL" ]; then
  export APP_BASE_URL="http://localhost:8000"
fi

echo "--- Running tests against $APP_BASE_URL ---"

# Run pytest, -v for verbose output
pytest -v test_deployment.py test_functionality.py

echo "--- All tests passed successfully! ---"
