"""Testes unitários da política de cota inicial (TD-12 / T2)."""

import unittest
from unittest.mock import MagicMock, patch

from utils import campaign_send_policy as csp
from utils import limits as limits_module


class TestEffectiveInitialDailyCaps(unittest.TestCase):
    def test_g2_returns_both_caps(self):
        caps = csp.effective_initial_daily_caps(30, 12, policy="g2")
        self.assertEqual(caps["policy"], "g2")
        self.assertEqual(caps["user_cap"], 30)
        self.assertEqual(caps["campaign_cap"], 12)

    def test_g1_only_user_cap(self):
        caps = csp.effective_initial_daily_caps(25, 99, policy="g1")
        self.assertEqual(caps["policy"], "g1")
        self.assertEqual(caps["user_cap"], 25)
        self.assertIsNone(caps["campaign_cap"])

    def test_g3_only_campaign_cap(self):
        caps = csp.effective_initial_daily_caps(40, 7, policy="g3")
        self.assertEqual(caps["policy"], "g3")
        self.assertIsNone(caps["user_cap"])
        self.assertEqual(caps["campaign_cap"], 7)

    def test_unknown_policy_falls_back_to_module_default(self):
        caps = csp.effective_initial_daily_caps(10, 5, policy="invalid")
        self.assertEqual(caps["policy"], csp.INITIAL_CHUNK_DAILY_QUOTA_POLICY)


class TestInitialChunkDailyQuotaAllows(unittest.TestCase):
    def test_g2_both_must_be_under_cap(self):
        self.assertTrue(
            csp.initial_chunk_daily_quota_allows(
                5, 4, plan_daily_limit=30, campaign_daily_limit=10, policy="g2"
            )
        )
        self.assertFalse(
            csp.initial_chunk_daily_quota_allows(
                30, 0, plan_daily_limit=30, campaign_daily_limit=10, policy="g2"
            )
        )
        self.assertFalse(
            csp.initial_chunk_daily_quota_allows(
                0, 10, plan_daily_limit=30, campaign_daily_limit=10, policy="g2"
            )
        )

    def test_g1_ignores_campaign_count(self):
        self.assertTrue(
            csp.initial_chunk_daily_quota_allows(
                10, 999, plan_daily_limit=30, campaign_daily_limit=5, policy="g1"
            )
        )

    def test_g3_ignores_user_count(self):
        self.assertTrue(
            csp.initial_chunk_daily_quota_allows(
                999, 2, plan_daily_limit=30, campaign_daily_limit=10, policy="g3"
            )
        )


class TestUazapiInitialChunkDistributionLimits(unittest.TestCase):
    """TD-10: alinhamento com `_create_campaign_core` (ceil por instância, teto 30)."""

    def test_matches_create_core_examples(self):
        self.assertEqual(csp.uazapi_initial_chunk_distribution_limits(10, 3), (4, 10))
        self.assertEqual(csp.uazapi_initial_chunk_distribution_limits(5, 2), (3, 5))
        self.assertEqual(csp.uazapi_initial_chunk_distribution_limits(5, 1), (5, 5))
        self.assertEqual(csp.uazapi_initial_chunk_distribution_limits(100, 2), (30, 100))

    def test_zero_daily_limit_falls_back_to_thirty(self):
        self.assertEqual(csp.uazapi_initial_chunk_distribution_limits(0, 2), (15, 30))


class TestGetSentTodayCampaignInitialCount(unittest.TestCase):
    @patch("utils.limits.get_db_connection")
    def test_query_filters_by_campaign_id(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [{"count": 3}, None]

        n = limits_module.get_sent_today_campaign_initial_count(42)
        self.assertEqual(n, 3)
        first_sql, first_params = mock_cursor.execute.call_args_list[0][0]
        self.assertIn("WHERE c.id = %s", first_sql)
        self.assertEqual(first_params, (42,))


class TestCheckInitialChunkDailyQuotaForCampaign(unittest.TestCase):
    @patch("utils.limits.get_sent_today_campaign_initial_count", return_value=1)
    @patch("utils.limits.get_sent_today_count", return_value=5)
    @patch("utils.limits.get_user_daily_limit", return_value=30)
    @patch("utils.limits.get_db_connection")
    def test_uses_campaign_owner_and_g3_default(
        self, mock_conn, _mock_plan, _mock_sent_user, _mock_sent_camp
    ):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"user_id": 77, "daily_limit": 10}

        ok = limits_module.check_initial_chunk_daily_quota_for_campaign(100, instance_id=3)
        self.assertTrue(ok)
        mock_cursor.execute.assert_called_once()
        self.assertEqual(mock_cursor.execute.call_args[0][1], (100,))
        _mock_sent_user.assert_called_once_with(77)

    @patch("utils.limits.get_sent_today_campaign_initial_count", return_value=1)
    @patch("utils.limits.get_sent_today_count", return_value=30)
    @patch("utils.limits.get_user_daily_limit", return_value=30)
    @patch("utils.limits.get_db_connection")
    def test_g3_default_ignores_user_plan_cap(
        self, mock_conn, _mock_plan, _mock_sent_user, _mock_sent_camp
    ):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"user_id": 77, "daily_limit": 10}

        self.assertTrue(
            limits_module.check_initial_chunk_daily_quota_for_campaign(100)
        )

    @patch("utils.limits.get_sent_today_campaign_initial_count", return_value=10)
    @patch("utils.limits.get_sent_today_count", return_value=5)
    @patch("utils.limits.get_user_daily_limit", return_value=30)
    @patch("utils.limits.get_db_connection")
    def test_false_when_campaign_cap_reached(
        self, mock_conn, _mock_plan, _mock_sent_user, _mock_sent_camp
    ):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"user_id": 1, "daily_limit": 10}

        self.assertFalse(
            limits_module.check_initial_chunk_daily_quota_for_campaign(200)
        )
        self.assertFalse(
            limits_module.check_initial_chunk_daily_quota_for_campaign(200, policy="g2")
        )

    @patch("utils.limits.get_db_connection")
    def test_false_when_campaign_missing(self, mock_conn):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        self.assertFalse(
            limits_module.check_initial_chunk_daily_quota_for_campaign(999999)
        )


class TestInitialChunkQuotaSnapshot(unittest.TestCase):
    @patch("utils.limits.get_sent_today_campaign_initial_count", return_value=2)
    @patch("utils.limits.get_sent_today_count", return_value=30)
    @patch("utils.limits.get_user_daily_limit", return_value=30)
    @patch("utils.limits.get_db_connection")
    def test_g3_default_uses_campaign_cap_only(
        self, mock_conn, _mock_plan, _mock_sent_user, _mock_sent_camp
    ):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"user_id": 5, "daily_limit": 10}

        snap = limits_module.initial_chunk_quota_snapshot(42)

        self.assertEqual(snap["policy"], "g3")
        self.assertTrue(snap["allows_more"])
        self.assertEqual(snap["remaining_slots"], 8)
        self.assertEqual(snap["sent_campaign_today"], 2)
        self.assertEqual(snap["campaign_cap"], 10)

    @patch("utils.limits.get_sent_today_campaign_initial_count", return_value=10)
    @patch("utils.limits.get_sent_today_count", return_value=0)
    @patch("utils.limits.get_user_daily_limit", return_value=30)
    @patch("utils.limits.get_db_connection")
    def test_g3_default_false_when_campaign_cap_reached(
        self, mock_conn, _mock_plan, _mock_sent_user, _mock_sent_camp
    ):
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"user_id": 5, "daily_limit": 10}

        snap = limits_module.initial_chunk_quota_snapshot(42)

        self.assertEqual(snap["policy"], "g3")
        self.assertFalse(snap["allows_more"])
        self.assertEqual(snap["remaining_slots"], 0)


if __name__ == "__main__":
    unittest.main()
