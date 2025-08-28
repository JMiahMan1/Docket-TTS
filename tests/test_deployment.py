# tests/test_deployment.py
import os
import requests

# The base URL for the running web application
# Assumes the app is running on localhost:8000 as per docker-compose.yml
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

def test_app_is_running():
    """
    Tests if the web server is up and returns a 200 OK status code.
    """
    try:
        response = requests.get(f"{BASE_URL}/")
        assert response.status_code == 200
        assert "<title>Docket-TTS</title>" in response.text
    except requests.ConnectionError as e:
        assert False, f"Connection to the application at {BASE_URL} failed: {e}"
