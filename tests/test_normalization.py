
import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tts_service import normalize_text

class TestNormalization(unittest.TestCase):
    
    def test_bible_references(self):
        # Test cases for Bible references
        cases = [
            ("ROM 9:28", ["Romans", "nine", "twenty-eight"]),
            ("Rom. 9:28", ["Romans", "nine", "twenty-eight"]),
            ("Is 43", ["Isaiah", "forty-three"]),
            ("IS 43", ["Isaiah", "forty-three"]),
            ("Is. 43", ["Isaiah", "forty-three"]),
            ("Gen 1:1", ["Genesis", "one"]),
        ]
        
        for input_text, expected_keywords in cases:
            with self.subTest(input_text=input_text):
                result = normalize_text(input_text)
                print(f"Input: {input_text} -> Output: {result}")
                
                for keyword in expected_keywords:
                    self.assertIn(keyword, result, f"Expected '{keyword}' in '{result}'")

    def test_hyphenation(self):
        # Test hyphen handling
        input_text = "This is a sen-\ntence with a hyp-\nhen."
        # Expectation: hyphens at end of line joined, newlines removed
        # Note: 'normalize_text' might verify specific spacing, but key is 'sentence' is whole
        result = normalize_text(input_text)
        print(f"Hyphen Input: {input_text!r} -> Output: {result!r}")
        
        self.assertIn("sentence", result)
        self.assertNotIn("sen-", result)
        self.assertNotIn("sen -", result)

    def test_standard_text_cleaning(self):
        # Test normal text
        input_text = "Chapter 1\n\nThis is a test."
        result = normalize_text(input_text)
        print(f"Standard Input: {input_text!r} -> Output: {result!r}")
        # Expect "Chapter one" because numbers are expanded
        self.assertIn("Chapter one", result)

if __name__ == '__main__':
    unittest.main()
