# -*- coding: utf-8 -*-
"""
Aceitação alinhada à tech-spec ``envio-individual-fila-intercalada-campanhas``:
AC6 (polling), AC7 (API fase 1 só superadmin), AC8 (offset migração), AC11 (sent_today),
AC12 (pausa: linhas dessa campanha não são enviadas pelo tick).

AC1–AC3 e fluxo de criação outbox: ver ``test_admin_campaign_crud.py``.
AC5 (quota diária): coberto aqui com mock de quota esgotada (defer sem POST).
AC9 (checklist PM go-live): manual / processo — não automatizado.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import importlib
import importlib.util
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _TESTS_DIR)

import campaign_test_data as ctd


def _migrate_module():
    path = os.path.join(_REPO_ROOT, "scripts", "migrate_campaign_to_outbox.py")
    spec = importlib.util.spec_from_file_location("migrate_campaign_to_outbox", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _reload_outbox_modules(monkeypatch, enabled: bool):
    if enabled:
        monkeypatch.setenv("USE_MESSAGE_OUTBOX", "1")
    else:
        monkeypatch.delenv("USE_MESSAGE_OUTBOX", raising=False)
    import utils.config as cfg

    importlib.reload(cfg)
    import app as app_mod

    importlib.reload(app_mod)
    import worker_message_outbox as wmo

    importlib.reload(wmo)
    return app_mod, wmo


@pytest.fixture
def db_conn():
    from app import get_db_connection

    conn = get_db_connection()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def ensure_superadmin(db_conn):
    from psycopg2.extras import RealDictCursor

    email = "augustogumi@gmail.com"
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, true) RETURNING id",
            (email, "fakehash"),
        )
        uid = cur.fetchone()["id"]
        db_conn.commit()
        return uid


@pytest.fixture
def ensure_plain_admin_not_super(db_conn):
    """Admin ``is_admin`` mas email fora de SUPER_ADMIN_EMAILS (fase 1 outbox)."""
    from psycopg2.extras import RealDictCursor

    email = "plain_admin_outbox_test@example.com"
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, true) RETURNING id",
            (email, "fakehash"),
        )
        uid = cur.fetchone()["id"]
        db_conn.commit()
        return uid


@pytest.fixture
def ensure_target_user(db_conn):
    from psycopg2.extras import RealDictCursor

    email = "target_outbox_ac@example.com"
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
def ensure_uazapi_instance(db_conn, ensure_target_user):
    """Primeira instância Uazapi ``connected`` do utilizador-alvo; cria se não existir."""
    return ctd.first_connected_uazapi_instance_id(db_conn, ensure_target_user)


def _insert_campaign_with_leads(
    db_conn,
    *,
    user_id: int,
    instance_id: int,
    n_leads: int,
    name: str,
    sent_today: int = 0,
):
    """Campanha Uazapi mínima + leads pendentes ordenados por id."""
    from psycopg2.extras import RealDictCursor

    msg = json.dumps(["Olá {nome}, teste migração outbox."])
    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO campaigns (
                user_id, name, message_template, status,
                use_uazapi_sender, rotation_mode,
                send_hour_start, send_hour_end, send_saturday, send_sunday,
                outbox_delay_min_seconds, outbox_delay_max_seconds,
                sent_today, enable_cadence
            )
            VALUES (
                %s, %s, %s, 'running',
                true, 'single',
                %s, %s, true, true,
                5, 10,
                %s, false
            )
            RETURNING id
            """,
            (
                user_id,
                name,
                msg,
                ctd.DEFAULT_TEST_SEND_HOUR_START,
                ctd.DEFAULT_TEST_SEND_HOUR_END,
                sent_today,
            ),
        )
        cid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s)",
            (cid, instance_id),
        )
        cur.execute(
            """
            INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template)
            VALUES (%s, 1, 'Inicial', %s::text)
            """,
            (cid, msg),
        )
        lead_ids = []
        for i in range(n_leads):
            cur.execute(
                """
                INSERT INTO campaign_leads (campaign_id, phone, name, status)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id
                """,
                (cid, f"551199999{i:04d}", f"Lead {i}"),
            )
            lead_ids.append(int(cur.fetchone()["id"]))
        db_conn.commit()
    return cid, lead_ids


def test_ac8_migration_offset_lead_index_19(db_conn, ensure_target_user, ensure_uazapi_instance):
    """AC8: 10 chunks ``done`` (1 lead) + 1 ``scheduled`` (8 leads) → primeiro outbox no índice 19."""
    from psycopg2.extras import RealDictCursor

    mig = _migrate_module()
    compute_legacy_initial_migration_offset = mig.compute_legacy_initial_migration_offset
    ordered_campaign_lead_ids = mig.ordered_campaign_lead_ids

    uid = ensure_target_user
    iid = ensure_uazapi_instance
    cid, leads = _insert_campaign_with_leads(db_conn, user_id=uid, instance_id=iid, n_leads=25, name="AC8 migration")

    ordered = ordered_campaign_lead_ids(db_conn, cid)
    assert ordered == leads

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        for j in range(10):
            cur.execute(
                """
                INSERT INTO campaign_stage_sends (
                    campaign_id, stage, instance_id, status,
                    planned_count, success_count, failed_count, lead_ids
                )
                VALUES (%s, 'initial', %s, 'done', 1, 1, 0, %s::jsonb)
                """,
                (cid, iid, json.dumps([leads[j]])),
            )
        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'scheduled', 8, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(leads[10:18])),
        )
        db_conn.commit()

    off, detail = compute_legacy_initial_migration_offset(db_conn, cid)
    assert off == 18
    assert ordered[off] == leads[18]
    assert off + 1 == 19
    assert len(detail) == 11


def test_migration_failed_counts_full_chunk_unknown_status_zero(db_conn, ensure_target_user, ensure_uazapi_instance):
    """``failed`` conta tamanho inteiro; status desconhecido (ex.: pending no chunk) conta 0."""
    from psycopg2.extras import RealDictCursor

    mig = _migrate_module()
    compute_legacy_initial_migration_offset = mig.compute_legacy_initial_migration_offset

    uid = ensure_target_user
    iid = ensure_uazapi_instance
    cid, leads = _insert_campaign_with_leads(db_conn, user_id=uid, instance_id=iid, n_leads=15, name="mixed chunks")

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'failed', 3, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(leads[0:3])),
        )
        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'pending', 5, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(leads[3:8])),
        )
        db_conn.commit()

    off, _ = compute_legacy_initial_migration_offset(db_conn, cid)
    assert off == 3


def test_ac7_outbox_state_forbidden_for_plain_admin(db_conn, ensure_plain_admin_not_super, monkeypatch):
    """AC7 (API polling): admin não-super recebe 403 quando ``USE_MESSAGE_OUTBOX`` está ligado."""
    _reload_outbox_modules(monkeypatch, True)
    import app as app_mod

    flask_app = app_mod.app
    flask_app.config["TESTING"] = True
    flask_app.config["LOGIN_DISABLED"] = True
    admin_id = ensure_plain_admin_not_super

    with flask_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_id)
        res = client.get("/api/admin/campaigns/999999/outbox-state")
    assert res.status_code == 403

    _reload_outbox_modules(monkeypatch, False)


def test_ac6_outbox_state_since_attempt_cursor(db_conn, ensure_superadmin, ensure_target_user, ensure_uazapi_instance, monkeypatch):
    """AC6: tentativas com ``since_attempt_id`` só devolvem linhas novas."""
    _reload_outbox_modules(monkeypatch, True)
    uid = ensure_target_user
    iid = ensure_uazapi_instance
    cid, leads = _insert_campaign_with_leads(db_conn, user_id=uid, instance_id=iid, n_leads=2, name="AC6 polling")

    from psycopg2.extras import RealDictCursor

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
            RETURNING id
            """,
            (cid, leads[0], iid, f"campaign-{cid}-lead-{leads[0]}-initial"),
        )
        oid = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO campaign_send_attempts (outbox_id, attempt_no, http_status, uazapi_response, outcome, latency_ms)
            VALUES (%s, 1, 200, '{}', 'sent', 10)
            RETURNING id
            """,
            (oid,),
        )
        aid1 = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO campaign_send_attempts (outbox_id, attempt_no, http_status, uazapi_response, outcome, latency_ms)
            VALUES (%s, 2, 500, '{}', 'failed', 20)
            RETURNING id
            """,
            (oid,),
        )
        aid2 = cur.fetchone()["id"]
        db_conn.commit()

    import app as app_mod

    flask_app = app_mod.app
    flask_app.config["TESTING"] = True
    flask_app.config["LOGIN_DISABLED"] = True
    admin_id = ensure_superadmin

    with flask_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin_id)
        r1 = client.get(f"/api/admin/campaigns/{cid}/outbox-state?since_attempt_id={aid1}")
        assert r1.status_code == 200
        body = r1.get_json()
        attempts = body.get("attempts") or []
        ids = [a["id"] for a in attempts]
        assert aid2 in ids
        assert aid1 not in ids


def test_ac11_sent_today_increments_on_successful_tick(
    db_conn, ensure_superadmin, ensure_target_user, monkeypatch,
):
    """AC11: após tick com sucesso mockado, ``campaigns.sent_today`` incrementa."""
    import tempfile

    from psycopg2.extras import RealDictCursor

    admin_id = ensure_superadmin
    target_user_id = ensure_target_user

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".csv",
        delete=False,
        encoding="utf-8",
        newline="",
    )
    tmp.write(ctd.SAMPLE_LEADS_CSV)
    tmp.close()

    try:
        instance_id = ctd.first_connected_uazapi_instance_id(db_conn, target_user_id)
        with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO scraping_jobs (user_id, keyword, locations, total_results, status, results_path, created_at)
                   VALUES (%s, 'k', 'SP', %s, 'completed', %s, NOW()) RETURNING id""",
                (target_user_id, ctd.SAMPLE_LEADS_ROW_COUNT, tmp.name),
            )
            job_id = cur.fetchone()["id"]
            db_conn.commit()

        _reload_outbox_modules(monkeypatch, True)
        with patch("services.uazapi.UazapiService.create_advanced_campaign") as mock_adv:
            with patch("utils.limits.can_create_campaign_today", return_value=True):
                app_mod = __import__("app", fromlist=["app"])
                flask_app = app_mod.app
                flask_app.config["TESTING"] = True
                flask_app.config["LOGIN_DISABLED"] = True

                with flask_app.test_client() as client:
                    with client.session_transaction() as sess:
                        sess["_user_id"] = str(admin_id)
                    payload = {
                        "user_id": target_user_id,
                        "name": "AC11 sent_today",
                        "job_id": job_id,
                        "message_templates": ["Oi {nome}"],
                        "instance_ids": [instance_id],
                        "use_uazapi_sender": True,
                        "rotation_mode": "single",
                        "send_hour_start": ctd.DEFAULT_TEST_SEND_HOUR_START,
                        "send_hour_end": ctd.DEFAULT_TEST_SEND_HOUR_END,
                        "send_saturday": True,
                        "send_sunday": True,
                    }
                    res = client.post(
                        "/api/admin/campaigns",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )
                assert res.status_code in (200, 201), res.get_data(as_text=True)
                mock_adv.assert_not_called()
                data = json.loads(res.get_data(as_text=True))
                campaign_id = data.get("campaign_id") or data.get("id")

        with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT sent_today FROM campaigns WHERE id = %s", (campaign_id,))
            before = cur.fetchone()["sent_today"] or 0

        import worker_message_outbox as wmo

        with patch.object(wmo, "is_campaign_send_window", return_value=True):
            with patch.object(
                wmo.uazapi_service,
                "send_text_idempotent",
                return_value={"messageId": "m1"},
            ):
                with patch("utils.limits.check_initial_chunk_daily_quota_for_campaign", return_value=True):
                    wmo.process_message_outbox_tick(db_conn)

        with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT sent_today FROM campaigns WHERE id = %s", (campaign_id,))
            after = cur.fetchone()["sent_today"] or 0

        assert after == before + 1
    finally:
        os.unlink(tmp.name)

    monkeypatch.delenv("USE_MESSAGE_OUTBOX", raising=False)
    importlib.reload(__import__("utils.config", fromlist=["cfg"]))
    importlib.reload(__import__("worker_message_outbox", fromlist=["wmo"]))


def test_ac12_paused_campaign_skips_outbox_post(db_conn, ensure_target_user, ensure_uazapi_instance, monkeypatch):
    """AC12: campanha ``paused`` não entra no SELECT do worker; outbox dessa campanha permanece sem tentativa.

    O tick é global: outras filas ``pending`` podem ser processadas no mesmo ``process_message_outbox_tick``.
    O mock com retorno JSON válido evita efeitos colaterais se o tick pegar outra campanha na BD de testes.
    """
    _reload_outbox_modules(monkeypatch, True)
    uid = ensure_target_user
    iid = ensure_uazapi_instance
    cid, leads = _insert_campaign_with_leads(db_conn, user_id=uid, instance_id=iid, n_leads=1, name="AC12 pause")
    from psycopg2.extras import RealDictCursor

    with db_conn.cursor() as cur:
        cur.execute("UPDATE campaigns SET status = 'paused' WHERE id = %s", (cid,))
        cur.execute(
            """
            INSERT INTO campaign_message_outbox (
                campaign_id, campaign_lead_id, instance_id,
                stage, step_priority, status, queued_at, next_run_at,
                idempotency_key, payload_summary
            )
            VALUES (%s, %s, %s, 'initial', 0, 'pending', NOW(), NOW(),
                    %s, '{}'::jsonb)
            """,
            (cid, leads[0], iid, f"campaign-{cid}-lead-{leads[0]}-initial"),
        )
        db_conn.commit()

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM campaign_message_outbox WHERE campaign_id = %s ORDER BY id DESC LIMIT 1",
            (cid,),
        )
        paused_row = cur.fetchone()
        assert paused_row
        paused_outbox_id = int(paused_row["id"])

    import worker_message_outbox as wmo

    with patch.object(wmo, "is_campaign_send_window", return_value=True):
        with patch.object(
            wmo.uazapi_service,
            "send_text_idempotent",
            return_value={"messageId": "ac12-collateral-ok"},
        ):
            with patch("utils.limits.check_initial_chunk_daily_quota_for_campaign", return_value=True):
                wmo.process_message_outbox_tick(db_conn)

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT status FROM campaign_message_outbox WHERE id = %s",
            (paused_outbox_id,),
        )
        assert cur.fetchone()["status"] == "pending"
        cur.execute(
            "SELECT COUNT(*) AS n FROM campaign_send_attempts WHERE outbox_id = %s",
            (paused_outbox_id,),
        )
        assert int(cur.fetchone()["n"]) == 0

    monkeypatch.delenv("USE_MESSAGE_OUTBOX", raising=False)
    importlib.reload(__import__("utils.config", fromlist=["cfg"]))
    importlib.reload(__import__("worker_message_outbox", fromlist=["wmo"]))


def test_ac5_daily_quota_defers_without_post(db_conn, ensure_target_user, ensure_uazapi_instance, monkeypatch):
    """AC5: quota initial esgotada → ``next_run_at`` adiado e sem chamada Uazapi."""
    _reload_outbox_modules(monkeypatch, True)
    uid = ensure_target_user
    iid = ensure_uazapi_instance
    cid, leads = _insert_campaign_with_leads(db_conn, user_id=uid, instance_id=iid, n_leads=1, name="AC5 quota")

    from psycopg2.extras import RealDictCursor

    from datetime import datetime

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO campaign_message_outbox (
                campaign_id, campaign_lead_id, instance_id,
                stage, step_priority, status, queued_at, next_run_at,
                idempotency_key, payload_summary
            )
            VALUES (%s, %s, %s, 'initial', 0, 'pending', NOW(), NOW() - INTERVAL '1 minute',
                    %s, '{}'::jsonb)
            RETURNING id, next_run_at
            """,
            (cid, leads[0], iid, f"campaign-{cid}-lead-{leads[0]}-initial"),
        )
        row = cur.fetchone()
        oid = row["id"]
        prev_run = row["next_run_at"]
        db_conn.commit()

    import worker_message_outbox as wmo

    with patch.object(wmo, "is_campaign_send_window", return_value=True):
        with patch.object(wmo.uazapi_service, "send_text_idempotent") as mock_send:
            with patch.object(
                wmo,
                "check_initial_chunk_daily_quota_for_campaign",
                return_value=False,
            ):
                wmo.process_message_outbox_tick(db_conn)

    mock_send.assert_not_called()

    with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT next_run_at FROM campaign_message_outbox WHERE id = %s", (oid,))
        new_run = cur.fetchone()["next_run_at"]

    assert new_run > prev_run

    monkeypatch.delenv("USE_MESSAGE_OUTBOX", raising=False)
    importlib.reload(__import__("utils.config", fromlist=["cfg"]))
    importlib.reload(__import__("worker_message_outbox", fromlist=["wmo"]))
