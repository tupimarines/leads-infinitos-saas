# -*- coding: utf-8 -*-
"""Testes para ui_send_status (tech-spec ui-sincronizacao-status-lead-envio-campanhas)."""

import json
import os
import sys
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _TESTS_DIR)

import pytest

import app as app_mod

import campaign_test_data as ctd
from test_outbox_spec_acceptance import _insert_campaign_with_leads


def test_sql_expr_campaign_lead_has_outbox_sent_default():
    expr = app_mod.sql_expr_campaign_lead_has_outbox_sent()
    assert "campaign_message_outbox" in expr
    assert "campaign_leads.id" in expr
    assert "status = 'sent'" in expr


def test_sql_expr_campaign_lead_has_outbox_sent_alias_cl():
    expr = app_mod.sql_expr_campaign_lead_has_outbox_sent("cl")
    assert "cl.id" in expr


def test_sql_expr_rejects_injection_alias():
    with pytest.raises(ValueError):
        app_mod.sql_expr_campaign_lead_has_outbox_sent("cl; DROP TABLE users;--")


@pytest.mark.parametrize(
    "status,has_outbox,stage,ts_at,expected",
    [
        ("pending", True, None, None, "sent"),
        ("pending", False, "initial", datetime.now(timezone.utc), "sent"),
        ("pending", False, None, None, "pending"),
        ("pending", False, "", datetime.now(timezone.utc), "pending"),
        ("sent", False, None, None, "sent"),
        ("failed", True, "initial", datetime.now(timezone.utc), "failed"),
        ("invalid", False, None, None, "invalid"),
        ("FAILED", False, None, None, "failed"),
    ],
)
def test_compute_ui_send_status_table(status, has_outbox, stage, ts_at, expected):
    assert (
        app_mod.compute_ui_send_status(
            status,
            has_outbox_sent=has_outbox,
            last_sent_stage=stage,
            last_message_sent_at=ts_at,
        )
        == expected
    )


def test_compute_ui_send_status_for_lead_row_outbox_column():
    row = {"status": "pending", "outbox_has_sent": True}
    assert app_mod.compute_ui_send_status_for_lead_row(row) == "sent"


def test_compute_ui_send_status_for_lead_row_explicit_flag():
    row = {"status": "pending", "last_sent_stage": "initial", "last_message_sent_at": datetime.now(timezone.utc)}
    assert app_mod.compute_ui_send_status_for_lead_row(row, has_outbox_sent=False) == "sent"


@pytest.mark.parametrize(
    "step,expected",
    [(1, "initial"), (2, "follow1"), (3, "follow2"), (4, "breakup"), (9, None), ("x", None)],
)
def test_kanban_column_stage_for_step(step, expected):
    assert app_mod.kanban_column_stage_for_step(step) == expected


def test_compute_ui_sent_in_column_stage_last_sent_matches():
    row = {"current_step": 1, "last_sent_stage": "initial"}
    assert app_mod.compute_ui_sent_in_column_stage(row, outbox_sent_stages=set()) is True


def test_compute_ui_sent_in_column_stage_outbox_only():
    row = {"current_step": 2, "last_sent_stage": None}
    assert app_mod.compute_ui_sent_in_column_stage(row, outbox_sent_stages={"follow1"}) is True


def test_compute_ui_sent_in_column_stage_wrong_column():
    row = {"current_step": 2, "last_sent_stage": "initial"}
    assert app_mod.compute_ui_sent_in_column_stage(row, outbox_sent_stages=set()) is False


def test_reconciled_single_folder_skips_when_outbox_rows_exist(monkeypatch):
    """Painel não deve usar pasta legada se a campanha já tem fila outbox."""
    monkeypatch.setattr(app_mod, "_campaign_has_message_outbox_rows", lambda _cid: True)
    row = {
        "use_uazapi_sender": True,
        "uazapi_folder_id": "r20563ad1789d09",
        "enable_cadence": False,
    }
    assert app_mod._reconciled_uazapi_single_folder_list_folders(273, row, 163) is None


@pytest.fixture
def db_conn():
    from app import get_db_connection

    try:
        conn = get_db_connection()
    except Exception as exc:
        pytest.skip(f"PostgreSQL indisponível (teste de integração): {exc}")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def ensure_campaign_owner(db_conn):
    """Utilizador dono da campanha (rota user `/api/campaigns/.../leads`)."""
    from psycopg2.extras import RealDictCursor

    email = "target_ui_send_status_api@example.com"
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, false) RETURNING id",
            (email, "fakehash"),
        )
        uid = cur.fetchone()["id"]
        db_conn.commit()
        return uid


@pytest.fixture
def uazapi_instance_for_owner(db_conn, ensure_campaign_owner):
    return ctd.first_connected_uazapi_instance_id(db_conn, ensure_campaign_owner)


def test_api_campaign_leads_ui_send_status_sent_when_outbox_sent_but_row_pending(
    db_conn, ensure_campaign_owner, uazapi_instance_for_owner,
):
    """
    Cenário sintético de lag: linha em ``campaign_message_outbox`` com ``status='sent'`` mas
    ``campaign_leads.status`` ainda ``pending`` → API deve expor ``ui_send_status='sent'``.
    """
    from psycopg2.extras import RealDictCursor

    uid = ensure_campaign_owner
    iid = uazapi_instance_for_owner
    cid, lead_ids = _insert_campaign_with_leads(
        db_conn,
        user_id=uid,
        instance_id=iid,
        n_leads=1,
        name="UI status lag — outbox sent, lead pending",
    )
    lead_id = lead_ids[0]

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO campaign_message_outbox (
                campaign_id, campaign_lead_id, instance_id,
                stage, step_priority, status, queued_at, next_run_at,
                idempotency_key, payload_summary
            )
            VALUES (%s, %s, %s, 'initial', 0, 'sent', NOW(), NOW(),
                    %s, '{}'::jsonb)
            """,
            (cid, lead_id, iid, f"ui-lag-{cid}-{lead_id}-initial"),
        )
        db_conn.commit()

    flask_app = app_mod.app
    flask_app.config["TESTING"] = True
    flask_app.config["LOGIN_DISABLED"] = True

    with flask_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
        res = client.get(f"/api/campaigns/{cid}/leads")

    assert res.status_code == 200
    body = json.loads(res.get_data(as_text=True))
    leads = body.get("leads") or []
    row = next((L for L in leads if L.get("id") == lead_id), None)
    assert row is not None
    assert row.get("status") == "pending"
    assert row.get("ui_send_status") == "sent"
