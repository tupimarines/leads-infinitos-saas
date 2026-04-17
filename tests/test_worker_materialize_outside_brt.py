"""T9: materialize Uazapi fora da janela BRT — telemetria + bump ``scheduled_for`` (ou ``failed``)."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest


def _row_base(**kw):
    base = {
        "id": 100,
        "campaign_id": 1,
        "stage": "initial",
        "instance_id": 2,
        "scheduled_for": dt.datetime(2026, 4, 16, 22, 0, 0),
        "status": "scheduled",
        "apikey": "tok",
        "delay_min_minutes": 5,
        "delay_max_minutes": 15,
        "message_variations": None,
        "lead_ids": None,
        "campaign_delay_min": 5,
        "campaign_delay_max": 15,
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": True,
        "send_sunday": True,
        "use_uazapi_sender": True,
        "daily_limit": 30,
    }
    base.update(kw)
    return base


def _make_conn_sequence(select_rows, lead_rows):
    """Cursores na ordem: SELECT sends, SELECT leads, (opcional) UPDATE."""
    cursors = []

    sel = MagicMock()
    sel.__enter__ = MagicMock(return_value=sel)
    sel.__exit__ = MagicMock(return_value=False)
    sel.fetchall.return_value = select_rows
    cursors.append(sel)

    leads_cur = MagicMock()
    leads_cur.__enter__ = MagicMock(return_value=leads_cur)
    leads_cur.__exit__ = MagicMock(return_value=False)
    leads_cur.fetchall.return_value = lead_rows
    cursors.append(leads_cur)

    conn = MagicMock()

    def _cursor_factory(*_a, **_k):
        if not cursors:
            upd = MagicMock()
            upd.__enter__ = MagicMock(return_value=upd)
            upd.__exit__ = MagicMock(return_value=False)
            return upd
        return cursors.pop(0)

    conn.cursor.side_effect = _cursor_factory
    return conn


@pytest.fixture
def fixed_now():
    return dt.datetime(2026, 4, 16, 22, 0, 0)


def test_materialize_outside_brt_bumps_scheduled_for(monkeypatch, fixed_now, capsys):
    monkeypatch.setattr(
        "worker_cadence.uazapi_service",
        object(),
        raising=False,
    )
    import worker_cadence as wc

    row = _row_base()
    conn = _make_conn_sequence([row], [{"id": 1, "phone": "5511999999999", "whatsapp_link": None, "name": "A"}])
    next_sf = dt.datetime(2026, 4, 17, 11, 5, 0)

    dt_shim = type(
        "DT",
        (),
        {"utcnow": staticmethod(lambda: fixed_now), "now": dt.datetime.now},
    )()

    with (
        patch.object(wc, "datetime", dt_shim),
        patch.object(wc, "sync_campaign_stage_sends_before_new_chunk"),
        patch.object(wc, "_load_step_messages", return_value=["oi {nome}"]),
        patch.object(wc, "is_campaign_send_window", return_value=False),
        patch.object(wc, "next_valid_send_utc_naive", return_value=next_sf),
    ):
        wc._materialize_scheduled_stage_sends(conn)

    assert conn.cursor.call_count >= 3
    assert conn.commit.called

    out = capsys.readouterr().out
    assert "uazapi_materialize_outside_send_window" in out
    payload = None
    for line in out.splitlines():
        if "uazapi_materialize_outside_send_window" in line:
            payload = json.loads(line)
            break
    assert payload is not None
    assert payload["skipped_outside_window"] is True
    assert payload["policy"] == "bump_scheduled_for"
    assert payload["reason"] == "materialize_outside_brt_bump_next_valid"


def test_materialize_outside_brt_invalid_window_marks_failed(monkeypatch, fixed_now, capsys):
    monkeypatch.setattr("worker_cadence.uazapi_service", object(), raising=False)
    import worker_cadence as wc

    row = _row_base(
        send_hour_start=20,
        send_hour_end=8,
    )
    conn = _make_conn_sequence([row], [{"id": 1, "phone": "5511999999999", "whatsapp_link": None, "name": "A"}])
    dt_shim = type(
        "DT",
        (),
        {"utcnow": staticmethod(lambda: fixed_now), "now": dt.datetime.now},
    )()

    with (
        patch.object(wc, "datetime", dt_shim),
        patch.object(wc, "sync_campaign_stage_sends_before_new_chunk"),
        patch.object(wc, "_load_step_messages", return_value=["x"]),
        patch.object(wc, "is_campaign_send_window", return_value=False),
    ):
        wc._materialize_scheduled_stage_sends(conn)

    out = capsys.readouterr().out
    assert "materialize_outside_brt_invalid_window" in out
    assert conn.commit.called
