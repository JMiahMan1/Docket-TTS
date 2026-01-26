import fitz
import sys
import re

FILEPATH = "/app/uploads/c7d9b45326dd40718769ef35206dafab.pdf"

def analyze():
    try:
        doc = fitz.open(FILEPATH)
        print(f"--- Document: {FILEPATH} ---")
        print(f"Page Count: {doc.page_count}")
        
        # 1. Dump TOC
        toc = doc.get_toc()
        print(f"\n--- TOC ({len(toc)} entries) ---")
        for i, entry in enumerate(toc):
            print(f"[{i}] L{entry[0]}: '{entry[1]}' -> P{entry[2]}")
            if i > 20: 
                print("... (truncated)")
                break

        # 2. Check Major Heading Regex
        print("\n--- Regex Check ---")
        major_pattern = re.compile(r'^(Chapter|Part|Book|Section)\s+\w+|^\d+\.\s+|^(Introduction|Preface|Foreword|Prologue)', re.IGNORECASE)
        matches = [e for e in toc if major_pattern.match(e[1].strip())]
        print(f"Major Matches Found: {len(matches)}")
        for m in matches[:5]:
             print(f"  MATCH: '{m[1]}'")

        # 3. Dump First 10 Pages Text
        print("\n--- First 10 Pages Text ---")
        for i in range(min(10, doc.page_count)):
            page = doc.load_page(i)
            text = page.get_text()
            print(f"\n[[PAGE_{i+1}]] (Raw Len: {len(text)})")
            # Print first 200 chars and look for Headers
            print(text[:500].replace('\n', '\\n'))
            
            # Check TOC Header Detection
            stripped = text.strip().lower()
            if "contents" in stripped[:50] or "table of contents" in stripped[:50]:
                print("  => TOC HEADER DETECTED!")
            else:
                 print("  => No TOC Header detected in first 50 chars.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze()
