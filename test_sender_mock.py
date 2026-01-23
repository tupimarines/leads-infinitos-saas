import unittest
from unittest.mock import MagicMock, patch
import datetime
import importlib.util
import sys

# Import helper to load worker_sender as a module
spec = importlib.util.spec_from_file_location("worker_sender", "worker_sender.py")
worker_sender = importlib.util.module_from_spec(spec)
sys.modules["worker_sender"] = worker_sender
spec.loader.exec_module(worker_sender)

class TestSenderWorker(unittest.TestCase):
    
    def test_format_jid(self):
        # Case 1: Just DDD + Number (11 digits)
        self.assertEqual(worker_sender.format_jid("41999998888"), "5541999998888@s.whatsapp.net")
        # Case 2: Already has DDI (13 digits)
        self.assertEqual(worker_sender.format_jid("5541999998888"), "5541999998888@s.whatsapp.net")
        # Case 3: Landline (10 digits) -> +55
        self.assertEqual(worker_sender.format_jid("4133334444"), "554133334444@s.whatsapp.net")

    @patch('worker_sender.requests.get')
    def test_check_phone_exists(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"exists": True}
        
        exists = worker_sender.check_phone_on_whatsapp("inst1", "5541999@s.wh")
        self.assertTrue(exists)
        
        # Test headers
        args, kwargs = mock_get.call_args
        self.assertIn("Authorization", kwargs['headers'])
        self.assertIn("jid", kwargs['params'])

    @patch('worker_sender.requests.post')
    def test_send_message(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"key": "123"}
        
        success, resp = worker_sender.send_message("inst1", "5541@s.w", "Hello")
        self.assertTrue(success)
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['messageData']['text'], "Hello")

    @patch('worker_sender.get_db_connection')
    def test_daily_limit_check(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        
        # Mock returning 5 messages sent
        mock_cursor.fetchone.return_value = {'count': 5}
        
        # Limit 10 -> Should ensure True
        self.assertTrue(worker_sender.check_daily_limit(1, 10))
        
        # Limit 4 -> Should ensure False
        self.assertFalse(worker_sender.check_daily_limit(1, 4))

if __name__ == '__main__':
    unittest.main()
