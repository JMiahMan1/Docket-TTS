import pytest
import requests
import time
import os
import uuid

BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

def test_api_synthesis():
    """Test the new /api/synthesize endpoint"""
    url = f"{BASE_URL}/api/synthesize"
    payload = {
        "text": "This is a test of the API.",
        "title": "API Test",
        "voice": "af_bella"
    }
    
    response = requests.post(url, json=payload)
    assert response.status_code == 202
    
    data = response.json()
    task_id = data['task_id']
    status_url = data['status_url']
    
    # Poll for completion
    for _ in range(30):
        time.sleep(2)
        status_res = requests.get(status_url)
        if status_res.status_code == 200:
            status_data = status_res.json()
            if status_data.get('state') == 'SUCCESS':
                 # Cleanup
                filename = status_data['status']['filename']
                requests.post(f"{BASE_URL}/delete-bulk", data={'files_to_delete': [filename.replace('.mp3', '')]})
                return
            if status_data.get('state') == 'FAILURE':
                pytest.fail(f"Task failed: {status_data}")
                
    pytest.fail("Task timed out")

def test_dual_voice_param():
    """Test that secondary_voice parameter is accepted and processed"""
    # We can't easily verify the audio content is dual-voice without advanced analysis,
    # but we can verify the task completes successfully with the parameter.
    
    title = "Dual Voice Test"
    text = 'The narrator said, "This is dialogue." And then continued.'
    
    payload = {
        'text_title': title,
        'text_input': text,
        'voice': 'af_bella',
        'secondary_voice': 'am_adam',
        'speed_rate': "1.0"
    }
    
    # Submit via Form
    multipart_payload = {key: (None, value) for key, value in payload.items()}
    submit_response = requests.post(f"{BASE_URL}/", files=multipart_payload)
    assert submit_response.status_code == 200
    
    # Extract Task ID
    task_id_line = [line for line in submit_response.text.split('\n') if "const taskId = " in line]
    assert task_id_line
    task_id = task_id_line[0].split('"')[1]
    
    # Poll
    for _ in range(30):
        time.sleep(2)
        status_res = requests.get(f"{BASE_URL}/status/{task_id}")
        if status_res.status_code == 200:
            status_data = status_res.json()
            if status_data.get('state') == 'SUCCESS':
                 # Cleanup
                filename = status_data['status']['filename']
                requests.post(f"{BASE_URL}/delete-bulk", data={'files_to_delete': [filename.replace('.mp3', '')]})
                return
                
    pytest.fail("Dual voice task timed out")
