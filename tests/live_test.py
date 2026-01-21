
import requests
import time
import argparse
import sys
import os

# Default base URL
BASE_URL = "http://localhost:8000"

def test_paste_text(title, content, profile="standard", book_mode=True):
    print(f"\n--- Testing Paste Text: {title} ---")
    url = f"{BASE_URL}/upload"
    data = {
        'text_title': title,
        'text_input': content,
        'voice': 'af_bella',
        'speed_rate': '1.0',
        'chapter_profile': profile,
        'toc_strategy': 'auto',
    }
    if book_mode:
        data['book_mode'] = 'true'
        
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("Successfully submitted text.")
        return True
    else:
        print(f"Failed to submit text: {response.status_code}")
        print(response.text[:500])
        return False

def test_upload_file(filepath, profile="auto", book_mode=True):
    print(f"\n--- Testing File Upload: {os.path.basename(filepath)} ---")
    url = f"{BASE_URL}/upload"
    
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return False
        
    files = {'file': open(filepath, 'rb')}
    data = {
        'voice': 'af_bella',
        'speed_rate': '1.0',
        'chapter_profile': profile,
        'toc_strategy': 'auto',
    }
    if book_mode:
        data['book_mode'] = 'true'
        
    response = requests.post(url, data=data, files=files)
    if response.status_code == 200:
        print("Successfully uploaded file.")
        return True
    else:
        print(f"Failed to upload file: {response.status_code}")
        print(response.text[:500])
        return False

def poll_jobs(timeout=300):
    print("\n--- Polling Jobs ---")
    url = f"{BASE_URL}/api/jobs"
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                jobs = response.json()
                total = len(jobs)
                completed = sum(1 for j in jobs if j['status'] == 'completed')
                failed = sum(1 for j in jobs if j['status'] == 'failed')
                pending = total - completed - failed
                
                print(f"Jobs: {total} total, {completed} completed, {failed} failed, {pending} pending", end='\r')
                
                if total > 0 and pending == 0:
                    print("\nAll jobs finished!")
                    return jobs
            else:
                print(f"\nAPI Error: {response.status_code}")
        except Exception as e:
            print(f"\nConnection Error: {e}")
            
        time.sleep(5)
    
    print("\nPolling timed out.")
    return None

def verify_results(title_subset):
    print(f"\n--- Verifying Results for '{title_subset}' ---")
    url = f"{BASE_URL}/api/files"
    response = requests.get(url)
    if response.status_code == 200:
        files = response.json()
        matching_files = [f for f in files if title_subset.lower() in f['filename'].lower()]
        print(f"Found {len(matching_files)} matching files.")
        for f in matching_files:
            print(f"  - {f['filename']} ({f['size_formatted']})")
        return matching_files
    return []

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Docket-TTS Live Environment Test Script")
    parser.add_argument("--url", default=BASE_URL, help=f"Base URL of the app (default: {BASE_URL})")
    parser.add_argument("--mode", choices=['paste', 'upload', 'full'], default='full')
    parser.add_argument("--file", help="Path to file for upload test")
    
    args = parser.parse_args()
    BASE_URL = args.url

    # Sample text for pasting
    sample_text = """Test Project: Splitting Verification
Chapter 1
This is the first chapter content. It is short but should be detected.

Chapter 2
This is the second chapter. Splitting should happen here.

Chapter 3: The Final Chapter
The end of our short test.
"""


    if args.mode in ['paste', 'full']:
        if test_paste_text("Live Test Paste", sample_text):
            time.sleep(2) # Give it a moment to split
            jobs = poll_jobs()
            verify_results("Live_Test_Paste")

    if args.mode in ['upload', 'full']:
        if args.file:
            if test_upload_file(args.file):
                time.sleep(2)
                poll_jobs()
                verify_results(os.path.basename(args.file).split('.')[0])
        else:
            print("\nSkip upload test (no --file provided)")
