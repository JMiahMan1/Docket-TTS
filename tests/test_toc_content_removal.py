import unittest
import re
import sys
from unittest.mock import MagicMock

# Mock dependencies
sys.modules['docx'] = MagicMock()
sys.modules['ebooklib'] = MagicMock()
sys.modules['ebooklib.epub'] = MagicMock()
sys.modules['fitz'] = MagicMock()

import chapterizer
from chapterizer import _remove_toc_pages

# Mock Logger
class MockLogger:
    def info(self, msg): print(f"[INFO] {msg}")
    def warning(self, msg): print(f"[WARN] {msg}")
    
chapterizer.logger = MockLogger()

class TestTOContentRemoval(unittest.TestCase):
    def test_toc_page_removal(self):
        """
        Simulates a document where Page 2 is the TOC.
        It should be removed based on the header "CONTENTS" and matching lines.
        """
        # 1. Metadata from PDF
        toc_metadata = [
            [1, "How to Read the Bible", 5],
            [1, "The Whole Holy Tenor", 12],
            [1, "Holiness in History", 21]
        ]
        
        # 2. Simulated Text (Pages 1-4)
        # Page 1: Copyright (Keep)
        # Page 2: CONTENTS (Remove)
        # Page 3: More TOC (Remove - purely based on match rate)
        # Page 4: Acknowledgments (Keep)
        
        p1 = "[[PAGE_1]]\nCopyright 2010\nAll rights reserved.\n"
        
        p2 = "[[PAGE_2]]\nCONTENTS\n\nHow to Read the Bible ....... 5\nThe Whole Holy Tenor ........ 12\nHoliness in History ......... 21\n"
        
        # Page 3 has no header, but high match rate
        p3 = "[[PAGE_3]]\n10. Holiness as Power ...... 252\n11. Holiness as Character ... 271\n"
        
        # Add fake metadata for p3 check
        toc_metadata.append([1, "Holiness as Power", 252])
        toc_metadata.append([1, "Holiness as Character", 271])
        
        p4 = "[[PAGE_4]]\nACKNOWLEDGMENTS\nI would like to thank my cat.\n"
        
        full_text = p1 + p2 + p3 + p4
        
        # 3. Run Logic
        cleaned_text = _remove_toc_pages(full_text, toc_metadata)
        
        print("\n--- Cleaned Text ---")
        print(cleaned_text)
        
        self.assertIn("Copyright 2010", cleaned_text)
        self.assertNotIn("How to Read the Bible ....... 5", cleaned_text)
        self.assertIn("[TOC REMOVED]", cleaned_text)
        self.assertIn("ACKNOWLEDGMENTS", cleaned_text)

if __name__ == '__main__':
    unittest.main()
