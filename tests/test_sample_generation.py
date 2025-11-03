import os
import requests
import io
from mutagen.mp3 import MP3

BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

def get_sample_duration(voice_name, speed_rate):
    """Fetches a voice sample and returns its duration."""
    response = requests.get(f"{BASE_URL}/speak_sample/{voice_name}?speed={speed_rate}")
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'audio/mpeg'
    
    mp3_file = io.BytesIO(response.content)
    audio = MP3(mp3_file)
    return audio.info.length

def test_sample_generation_with_speed_control():
    """
    Tests that the sample generation endpoint produces audio of different
    durations based on the speed parameter.
    """
    voice = "af_bella"
    
    # Generate samples at different speeds
    slow_duration = get_sample_duration(voice, "1.3")
    normal_duration = get_sample_duration(voice, "1.0")
    fast_duration = get_sample_duration(voice, "0.7")

    assert slow_duration > normal_duration
    assert normal_duration > fast_duration
