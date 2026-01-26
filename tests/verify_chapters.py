import os
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.append(str(Path(__file__).parent.parent))

from chapterizer import chapterize, ChapterizationProfile
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_chapters")

TEST_DIR = Path("/app/text_examples")
if not TEST_DIR.exists():
    # Fallback for local testing if path differs
    TEST_DIR = Path("text_examples")

def verify_file(filename, profile="auto"):
    filepath = TEST_DIR / filename
    if not filepath.exists():
        print(f"❌ File not found: {filename}")
        return

    print(f"\n--- Testing: {filename} (Profile: {profile}) ---")
    try:
        chapters = chapterize(str(filepath), profile=profile, debug=True)
        print(f"✅ Result: Found {len(chapters)} chapters.")
        for i, chap in enumerate(chapters[:5]): # Show first 5
            print(f"  {i+1}. {chap.title}")
        if len(chapters) > 5:
            print(f"  ... and {len(chapters)-5} more.")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    files_to_test = [
        "A community of character - towards a constructive Christian - Stanley Hauerwas.pdf",
        "An Advent for the Cosmos - Pitts, Jeffrey.pdf",
        "Basics of Biblical Greek Grammar_ Fourth E - Mounce, William D_.epub",
        "Discovering Christian Holiness_ - Diane Leclerc.pdf",
        "Plain Truth for Plain People - Brad Estep;.epub",
        "Preach the Word_ Essays on Expository Prea - Leland Ryken.epub",
        "Romans - The Divine Marriage - Volume 1 Chapters 1-8 - Tom Holland.epub",
        "The Complete Works of John Wesley_ Volume 01_ Journals -- Wesley John.pdf",
        "The Complete Works of St John Chrysostom (36 Books) - Chrysostom, John, St.epub"
    ]

    for f in files_to_test:
        # Default auto
        verify_file(f, "auto")
        
        # Test specific profiles if relevant
        if "Journals" in f:
             verify_file(f, "journal")
