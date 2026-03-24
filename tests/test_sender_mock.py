import unittest
from unittest.mock import MagicMock, patch
import importlib.util
import sys

# Import helper to load worker_sender as a module (evita falha se .env local estiver corrompido)
with patch("dotenv.load_dotenv", lambda *a, **k: None):
    spec = importlib.util.spec_from_file_location("worker_sender", "worker_sender.py")
    worker_sender = importlib.util.module_from_spec(spec)
    sys.modules["worker_sender"] = worker_sender
    spec.loader.exec_module(worker_sender)

from utils import limits as limits_module

class TestSenderWorker(unittest.TestCase):
    
    def test_format_jid(self):
        # Case 1: Just DDD + Number (11 digits)
        self.assertEqual(worker_sender.format_jid("41999998888"), "5541999998888@s.whatsapp.net")
        # Case 2: Already has DDI (13 digits)
        self.assertEqual(worker_sender.format_jid("5541999998888"), "5541999998888@s.whatsapp.net")
        # Case 3: Landline (10 digits) -> +55
        self.assertEqual(worker_sender.format_jid("4133334444"), "554133334444@s.whatsapp.net")

    @patch.object(worker_sender, "uazapi_service")
    def test_check_phone_exists(self, mock_svc):
        mock_svc.check_phone.return_value = [
            {"isInWhatsapp": True, "jid": "5541999988888@s.whatsapp.net", "query": "5541999988888"}
        ]
        exists, correct_jid, is_inst_err = worker_sender.check_phone_on_whatsapp(
            "inst1",
            "5541999@s.wh",
            apikey="fake-token",
            api_provider="uazapi",
        )
        self.assertTrue(exists)
        self.assertFalse(is_inst_err)
        mock_svc.check_phone.assert_called_once()

    @patch.object(worker_sender, "uazapi_service")
    def test_send_message(self, mock_svc):
        mock_svc.send_text.return_value = {"key": "123"}
        success, resp = worker_sender.send_message(
            "inst1",
            "5541999988888@s.wh",
            "Hello",
            apikey="fake-token",
            api_provider="uazapi",
        )
        self.assertTrue(success)
        args, _kwargs = mock_svc.send_text.call_args
        self.assertEqual(args[2], "Hello")

    @patch('worker_sender.get_user_daily_limit', return_value=30)
    @patch('worker_sender.get_db_connection')
    def test_instance_daily_limit_check_uses_per_instance_policy(self, mock_conn, mock_get_user_daily_limit):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        # Mock returning 5 initial messages sent for this instance today
        mock_cursor.fetchone.return_value = {'count': 5}

        self.assertTrue(worker_sender.check_instance_daily_limit(1, "inst-01", instance_id=11))
        mock_get_user_daily_limit.assert_called_once_with(1, instance_id=11)
        args, _ = mock_cursor.execute.call_args
        self.assertIn("COALESCE(cl.current_step, 1) = 1", args[0])
        self.assertIn("COALESCE(cl.last_sent_stage, '') = 'initial'", args[0])

    @patch('worker_sender.get_user_daily_limit', return_value=10)
    @patch('worker_sender.get_db_connection')
    def test_instance_daily_limit_honors_infinite_configurable_limit(self, mock_conn, _mock_daily_limit):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        # Já enviou 10 hoje na instância -> bloqueia
        mock_cursor.fetchone.return_value = {'count': 10}
        self.assertFalse(worker_sender.check_instance_daily_limit(77, "inst-infinite", instance_id=900))


class TestLimitsPolicy(unittest.TestCase):
    @patch('utils.limits.get_db_connection')
    def test_non_initial_followup_is_not_counted_in_daily_limit(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {'count': 8}

        allowed = limits_module.check_daily_limit(user_id=1, plan_limit=10)
        self.assertTrue(allowed)
        executed_sql = mock_cursor.execute.call_args[0][0]
        self.assertIn("COALESCE(cl.current_step, 1) = 1", executed_sql)
        self.assertIn("COALESCE(cl.last_sent_stage, '') = 'initial'", executed_sql)

    def test_can_create_campaign_today_always_allows(self):
        """Limite diário removido: sempre permite criar chunks."""
        self.assertTrue(limits_module.can_create_campaign_today(instance_id=55))

    def test_can_create_campaign_today_allows_superadmin(self):
        self.assertTrue(limits_module.can_create_campaign_today(instance_id=55))

if __name__ == '__main__':
    unittest.main()
