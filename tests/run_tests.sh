#!/bin/bash
# run_tests.sh

# Exit immediately if a command exits with a non-zero status.
set -e

# The application environment is already set up by the Dockerfile.
# We just need to install pytest and its plugins for the test run.
echo "--- Installing test-specific dependencies ---"
pip install --no-cache-dir pytest pytest-xdist

# Set the Python path to ensure app modules can be imported
export PYTHONPATH=.

# --- STAGE 1: Run critical integration tests first ---
# These tests require a running server and verify core functionality.
# If they fail, 'set -e' will stop the script here.
echo "--- Running critical integration tests (deployment and functionality) ---"
pytest -v -n auto tests/test_deployment.py tests/test_functionality.py

# --- STAGE 2: Run remaining tests ---
# These tests will only run if the critical tests in Stage 1 have passed.
echo "--- Integration tests passed. Running remaining unit and component tests. ---"
pytest -v -n auto \
  tests/test_normalization.py \
  tests/test_audiobook_creation.py \
  tests/test_uploads.py \
  tests/test_sample_generation.py \
  tests/test_chapter_processing.py \
  tests/test_text_cleaner.py \
  tests/test_normalization_edge_cases.py \
  tests/test_task_logic.py

echo "--- All tests passed successfully! ---"
