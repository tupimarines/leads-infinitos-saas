"""T7: recovery de ``scheduled`` initial Uazapi sem pasta (TTL), antes do materialize."""

import datetime as dt
import json
import logging
import types
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_row():
    return {
        "id": 42,
        "campaign_id": 7,
        "instance_id": 3,
        "scheduled_for": dt.datetime(2020, 1, 1, 12, 0, 0),
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": True,
        "send_sunday": True,
        "user_id": 1,
        "daily_limit": 30,
        "apikey": "token",
    }


def test_stale_recovery_disabled_no_db_touch(monkeypatch):
    monkeypatch.setenv("UAZAPI_STALE_RECOVERY_ENABLED", "0")
    import worker_cadence as wc

    conn = MagicMock()
    wc._recover_stale_scheduled_initial_uazapi_sends(conn)
    conn.cursor.assert_not_called()


def test_stale_recovery_bump_scheduled_for(monkeypatch, fake_row):
    monkeypatch.delenv("UAZAPI_STALE_RECOVERY_ENABLED", raising=False)
    monkeypatch.setenv("UAZAPI_STALE_RECOVERY_TTL_MINUTES", "60")
    monkeypatch.setenv("UAZAPI_STALE_RECOVERY_MAX_PER_TICK", "10")

    import worker_cadence as wc

    fixed_now = dt.datetime(2026, 4, 16, 18, 0, 0)
    next_slot = dt.datetime(2026, 4, 16, 18, 5, 0)

    select_cur = MagicMock()
    select_cur.__enter__ = MagicMock(return_value=select_cur)
    select_cur.__exit__ = MagicMock(return_value=False)
    select_cur.fetchall.return_value = [fake_row]

    update_cur = MagicMock()
    update_cur.__enter__ = MagicMock(return_value=update_cur)
    update_cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.side_effect = [select_cur, update_cur]

    dt_shim = types.SimpleNamespace(
        utcnow=lambda: fixed_now,
        now=dt.datetime.now,
    )
    with (
        patch.object(wc, "datetime", dt_shim),
        patch.object(wc, "check_initial_chunk_daily_quota_for_campaign", return_value=True),
        patch.object(wc, "next_valid_send_utc_naive", return_value=next_slot),
    ):
        wc._recover_stale_scheduled_initial_uazapi_sends(conn)

    assert update_cur.execute.call_count == 1
    args = update_cur.execute.call_args[0]
    assert "UPDATE campaign_stage_sends" in args[0]
    assert args[1][0] == next_slot
    assert args[1][1] == 42
    conn.commit.assert_called_once()


def test_stale_recovery_failed_without_apikey(monkeypatch, fake_row):
    monkeypatch.delenv("UAZAPI_STALE_RECOVERY_ENABLED", raising=False)
    fake_row = {**fake_row, "apikey": None}

    import worker_cadence as wc

    select_cur = MagicMock()
    select_cur.__enter__ = MagicMock(return_value=select_cur)
    select_cur.__exit__ = MagicMock(return_value=False)
    select_cur.fetchall.return_value = [fake_row]

    update_cur = MagicMock()
    update_cur.__enter__ = MagicMock(return_value=update_cur)
    update_cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.side_effect = [select_cur, update_cur]

    dt_shim = types.SimpleNamespace(
        utcnow=lambda: dt.datetime(2026, 4, 16, 15, 0, 0),
        now=dt.datetime.now,
    )
    with patch.object(wc, "datetime", dt_shim):
        wc._recover_stale_scheduled_initial_uazapi_sends(conn)

    assert "status = 'failed'" in update_cur.execute.call_args[0][0]
    conn.commit.assert_called_once()


def test_stale_recovery_respects_only_campaign_id_in_sql(monkeypatch):
    monkeypatch.setenv("UAZAPI_STALE_RECOVERY_ENABLED", "0")
    import worker_cadence as wc

    select_cur = MagicMock()
    select_cur.__enter__ = MagicMock(return_value=select_cur)
    select_cur.__exit__ = MagicMock(return_value=False)
    select_cur.fetchall.return_value = []

    conn = MagicMock()
    conn.cursor.return_value = select_cur

    wc._recover_stale_scheduled_initial_uazapi_sends(
        conn,
        only_campaign_id=99,
        respect_recovery_env=False,
        return_stats=True,
    )

    sql = select_cur.execute.call_args[0][0]
    params = select_cur.execute.call_args[0][1]
    assert "AND css.campaign_id = %s" in sql
    assert 99 in params


def test_message_outbox_tick_skips_when_feature_disabled(monkeypatch):
    """Alinhado à tech-spec (flag off): ``process_message_outbox_tick`` não consulta o BD."""
    monkeypatch.delenv("USE_MESSAGE_OUTBOX", raising=False)
    import importlib
    import utils.config as cfg
    importlib.reload(cfg)
    import worker_message_outbox as wmo
    importlib.reload(wmo)

    conn = MagicMock()
    wmo.process_message_outbox_tick(conn)
    conn.cursor.assert_not_called()

    importlib.reload(cfg)
    importlib.reload(wmo)


def test_outbox_attempt_structured_log_has_required_fields(caplog):
    """Task 11: evento por tentativa — JSON com ids, latência, outcome; sem PII."""
    import worker_message_outbox as wmo

    caplog.set_level(logging.INFO)
    wmo._log_outbox_attempt_event(
        campaign_id=10,
        outbox_id=20,
        instance_id=30,
        latency_ms=150,
        outcome="sent",
        http_status=200,
    )
    assert caplog.records
    payload = json.loads(caplog.records[0].message)
    assert payload["event"] == "campaign_outbox_send_attempt"
    assert payload["campaign_id"] == 10
    assert payload["outbox_id"] == 20
    assert payload["instance_id"] == 30
    assert payload["latency_ms"] == 150
    assert payload["outcome"] == "sent"
    assert payload["http_status"] == 200


def test_stale_recovery_mark_failed_dry_run(monkeypatch, fake_row):
    monkeypatch.delenv("UAZAPI_STALE_RECOVERY_ENABLED", raising=False)

    import worker_cadence as wc

    select_cur = MagicMock()
    select_cur.__enter__ = MagicMock(return_value=select_cur)
    select_cur.__exit__ = MagicMock(return_value=False)
    select_cur.fetchall.return_value = [fake_row]

    conn = MagicMock()
    conn.cursor.return_value = select_cur

    dt_shim = types.SimpleNamespace(
        utcnow=lambda: dt.datetime(2026, 4, 16, 15, 0, 0),
        now=dt.datetime.now,
    )
    with patch.object(wc, "datetime", dt_shim):
        out = wc._recover_stale_scheduled_initial_uazapi_sends(
            conn,
            respect_recovery_env=False,
            dry_run=True,
            return_stats=True,
            recovery_mode="mark_failed",
        )

    assert out["dry_run_stale_send_ids"] == [42]
    assert out["failed_send_ids"] == []
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
