import unittest
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app

class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_health_check(self):
        resp = self.app.get('/health')
        self.assertEqual(resp.status_code, 200)

    def test_index_page(self):
        resp = self.app.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Docket-TTS', resp.data)

    def test_jobs_page(self):
        resp = self.app.get('/jobs')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Job Queue', resp.data)

    def test_files_page(self):
        resp = self.app.get('/files')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Generated Files', resp.data)
        
    def test_api_jobs(self):
        resp = self.app.get('/api/jobs')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.is_json)

if __name__ == '__main__':
    unittest.main()
