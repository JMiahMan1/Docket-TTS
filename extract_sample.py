
import sys
import os
sys.path.append(os.getcwd())
from chapterizer import chapterize

filepath = "text_examples/Preach the Word_ Essays on Expository Prea - Leland Ryken.epub"
chapters = chapterize(filepath, profile="auto")

print(f"Total chapters found: {len(chapters)}")
for i, ch in enumerate(chapters[:3]):
    print(f"\n--- CHAPTER {i+1}: {ch.title} ---\n")
    print(ch.content[:500] + "...")
