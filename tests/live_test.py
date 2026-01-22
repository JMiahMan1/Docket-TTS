
import requests
import time
import argparse
import sys
import os

# Default base URL
BASE_URL = "http://localhost:8000"

def test_paste_text(title, content, profile="standard", book_mode=True):
    print(f"\n--- Testing Paste Text: {title} ---")
    url = f"{BASE_URL}/"
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
    url = f"{BASE_URL}/"

    
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

def poll_jobs(timeout=2700):


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


def test_podcast_api(title='test_podcast', text='Dialogue test'):
    print(f"\n--- Testing Podcast API: {title} ---")
    url = f"{BASE_URL}/api/generate-podcast"
    data = {
        'title': title,
        'text': text,
        'voice': 'af_bella',
        'voice_secondary': 'af_sarah'
    }
    
    try:
        response = requests.post(url, json=data)
        if response.status_code == 200:
            json_resp = response.json()
            print("Successfully generated podcast.")
            print(f"Download URL: {json_resp.get('download_url')}")
            return True
        else:
            print(f"Failed to generate podcast: {response.status_code}")
            return False
    except Exception as e:
        print(f"Exception during podcast test: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Docket-TTS Live Environment Test Script")
    parser.add_argument("--url", default=BASE_URL, help=f"Base URL of the app (default: {BASE_URL})")
    parser.add_argument("--mode", choices=['paste', 'upload', 'podcast', 'full'], default='full')
    parser.add_argument("--file", help="Path to file for upload test")
    
    args = parser.parse_args()
    BASE_URL = args.url

    # Sample text for pasting
    chapter_content = "This is a much longer content block for the chapter to ensure it passes the minimum word count filter of one hundred words. " * 10
    
    sample_text = f"""Test Project: Splitting Verification

Chapter 1
{chapter_content}

Chapter 2: The Second Part
{chapter_content}

Chapter 3
{chapter_content}
"""

    results = {}

    if args.mode in ['paste', 'full']:
        print("\n=== STEP 1: Paste Text Test ===")
        if test_paste_text("Live Chapterization Test", sample_text):
            time.sleep(5) # Wait for async task to process and ensure jobs exist
            jobs = poll_jobs(timeout=600)
            verify_results("Live_Chapterization_Test")
            results['paste'] = 'PASS' if jobs else 'FAIL'

    if args.mode in ['podcast', 'full']:
        print("\n=== STEP 2: Podcast API Test ===")
        success = test_podcast_api(
            text='"Welcome to our deep dive on Exodus," said the host.\n\n"It is a fascinating book," agreed the guest.'
        )
        results['podcast'] = 'PASS' if success else 'FAIL'

    if args.mode in ['upload', 'full']:
        if args.file:
            print(f"\n=== STEP 3: File Upload Test ({args.file}) ===")
            if test_upload_file(args.file):
                print("Upload accepted. Waiting for async analysis to populate jobs...")
                time.sleep(10) # Give analyze_book_task time to run
                jobs = poll_jobs(timeout=900)
                verify_results(os.path.basename(args.file).split('.')[0])
                results['upload'] = 'PASS' if jobs else 'FAIL'
        else:
            print("\nSkip upload test (no --file provided)")

    print("\n\n=== VERIFICATION SUMMARY ===")
    for test, result in results.items():
        print(f"{test.upper()}: {result}")

