"""
Worker — fila Postgres ``campaign_message_outbox`` (envio unitário Uazapi).

ADR-5: (A) claim curto em transação → COMMIT → (B) HTTP sem tx → (C) persistência tentativa + estado.
Task 4 tech-spec: throttle antes do claim; §6.1 contagens só após HTTP 200 na fase (C).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from psycopg2.extras import RealDictCursor

from services.uazapi import UazapiService
from utils.campaign_dispatch_audit import append_dispatch_audit_event
from utils.config import SUPER_ADMIN_EMAILS, USE_MESSAGE_OUTBOX
from utils.limits import check_initial_chunk_daily_quota_for_campaign
from utils.next_valid_uazapi_send import is_campaign_send_window, next_valid_send_utc_naive
from utils.outbox_prometheus import observe_campaign_outbox_send_attempt

try:
    uazapi_service = UazapiService()
except ImportError:
    uazapi_service = None

logger = logging.getLogger(__name__)

# Métrica legada: última instância servida (envio segue ordem csv_row_order, sem round-robin).
_last_outbox_instance_id: Optional[int] = None

STAGE_TO_STEP_NUMBER = {"initial": 1, "follow1": 2, "follow2": 3, "breakup": 4}

# Cadência outbox: (stage, lead.current_step na coluna Kanban, step_priority, campaign_steps.step_number)
_CADENCE_OUTBOX_STAGES: tuple[tuple[str, int, int, int], ...] = (
    ("follow1", 2, 1, 2),
    ("follow2", 3, 2, 3),
    ("breakup", 4, 3, 4),
)

_REAPER_MINUTES = int(os.environ.get("UAZAPI_OUTBOX_SENDING_REAPER_MINUTES", "20"))


def _parse_message_template(raw: Any) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            msgs = [str(x).strip() for x in parsed if str(x).strip()]
            return msgs if msgs else []
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
    except Exception:
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
    return []


def _normalize_phone_e164_br(phone: Any) -> Optional[str]:
    if not phone:
        return None
    clean = re.sub(r"\D", "", str(phone))
    if len(clean) <= 11 and not clean.startswith("55"):
        clean = "55" + clean
    return clean if len(clean) >= 12 else clean or None


def _is_media_path_safe(media_path: str, user_id: int) -> bool:
    if not media_path or not user_id:
        return False
    if ".." in media_path:
        return False
    prefix = f"storage/{user_id}/"
    return bool(media_path.startswith(prefix))


def _truncate(s: Optional[str], n: int = 1800) -> str:
    if not s:
        return ""
    s = str(s)
    return s if len(s) <= n else s[:n] + "…"


def _log_outbox_attempt_event(
    *,
    campaign_id: int,
    outbox_id: int,
    instance_id: int,
    latency_ms: int,
    outcome: str,
    http_status: Optional[int] = None,
) -> None:
    """
    Um evento por tentativa (Task 11); JSON numa linha — sem PII (F11).
    """
    payload: dict[str, Any] = {
        "event": "campaign_outbox_send_attempt",
        "campaign_id": campaign_id,
        "outbox_id": outbox_id,
        "instance_id": instance_id,
        "latency_ms": latency_ms,
        "outcome": outcome,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    observe_campaign_outbox_send_attempt(outcome, latency_ms)


def _campaign_dict_from_row(c: dict) -> dict:
    return {
        "send_hour_start": c.get("send_hour_start"),
        "send_hour_end": c.get("send_hour_end"),
        "send_saturday": c.get("send_saturday"),
        "send_sunday": c.get("send_sunday"),
    }


def _user_is_superadmin(conn, user_id: int) -> bool:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return bool(row and row.get("email") in SUPER_ADMIN_EMAILS)


def _outbox_csv_sort_key(row: dict) -> tuple:
    ordv = row.get("csv_row_order")
    try:
        ord_i = int(ordv) if ordv is not None else int(row.get("campaign_lead_id") or 0)
    except (TypeError, ValueError):
        ord_i = int(row.get("campaign_lead_id") or 0)
    nr = row.get("next_run_at")
    nr_key = 0
    if nr is not None:
        try:
            nr_key = int(nr.timestamp()) if hasattr(nr, "timestamp") else 0
        except Exception:
            nr_key = 0
    return (
        int(row.get("step_priority") or 0),
        ord_i,
        int(row.get("campaign_lead_id") or 0),
        nr_key,
        int(row.get("id") or 0),
    )


def enqueue_missing_cadence_outbox_rows(conn) -> None:
    """
    Para campanhas Uazapi com fila outbox e ``enable_cadence``: enfileira follow1/follow2/breakup
    quando o lead já passou pelo buffer (snoozed) e ``snooze_until`` expirou.
    Ordem de inserção = ``csv_row_order`` (mesma hierarquia visual do Kanban).
    """
    if not USE_MESSAGE_OUTBOX:
        return
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.id AS campaign_id, c.user_id,
                   COALESCE(NULLIF(TRIM(c.rotation_mode), ''), 'single') AS rotation_mode
            FROM campaigns c
            WHERE c.status IN ('running', 'pending')
              AND COALESCE(c.enable_cadence, FALSE) = TRUE
              AND COALESCE(c.use_uazapi_sender, FALSE) = TRUE
              AND EXISTS (SELECT 1 FROM campaign_message_outbox o WHERE o.campaign_id = c.id LIMIT 1)
            """
        )
        campaigns = cur.fetchall() or []

    base_utc = datetime.utcnow()
    for camp in campaigns:
        cid = int(camp["campaign_id"])
        uid = int(camp["user_id"])
        rotation_mode = (camp.get("rotation_mode") or "single").strip().lower()
        if rotation_mode not in ("single", "round_robin"):
            rotation_mode = "single"

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.id AS instance_id, i.apikey
                FROM campaign_instances ci
                JOIN instances i ON i.id = ci.instance_id
                WHERE ci.campaign_id = %s
                  AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
                  AND i.apikey IS NOT NULL
                  AND TRIM(i.apikey) <> ''
                ORDER BY i.id ASC
                """,
                (cid,),
            )
            instances = cur.fetchall() or []
        if not instances:
            continue
        n_inst = len(instances)

        for stage, lead_step, step_priority, msg_step in _CADENCE_OUTBOX_STAGES:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT 1 FROM campaign_steps
                    WHERE campaign_id = %s AND step_number = %s
                    LIMIT 1
                    """,
                    (cid, msg_step),
                )
                if not cur.fetchone():
                    continue

                cur.execute(
                    """
                    SELECT cl.id, COALESCE(cl.csv_row_order, cl.id) AS ord_key
                    FROM campaign_leads cl
                    WHERE cl.campaign_id = %s
                      AND cl.current_step = %s
                      AND cl.cadence_status = 'snoozed'
                      AND cl.snooze_until IS NOT NULL
                      AND cl.snooze_until <= NOW()
                      AND COALESCE(cl.removed_from_funnel, FALSE) = FALSE
                      AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
                      AND NOT EXISTS (
                          SELECT 1 FROM campaign_message_outbox o
                          WHERE o.campaign_lead_id = cl.id
                            AND LOWER(TRIM(o.stage)) = LOWER(%s)
                            AND o.status IN ('pending', 'sending', 'sent')
                      )
                    ORDER BY COALESCE(cl.csv_row_order, cl.id) ASC, cl.id ASC
                    """,
                    (cid, lead_step, stage),
                )
                leads_to_queue = cur.fetchall() or []

            if not leads_to_queue:
                continue

            try:
                with conn.cursor() as cur:
                    for i, lr in enumerate(leads_to_queue):
                        lead_id = int(lr["id"])
                        if rotation_mode == "round_robin":
                            inst = instances[i % n_inst]
                        else:
                            inst = instances[0]
                        instance_id = int(inst["instance_id"])
                        idempotency_key = f"campaign-{cid}-lead-{lead_id}-{stage}"
                        payload_summary = json.dumps(
                            {
                                "stage": stage,
                                "enqueue": "cadence_followup_auto",
                                "rotation_mode": rotation_mode,
                            },
                            ensure_ascii=False,
                        )
                        queued_at = base_utc + timedelta(microseconds=min(i, 999999))
                        cur.execute(
                            """
                            INSERT INTO campaign_message_outbox (
                                campaign_id, campaign_lead_id, instance_id,
                                stage, step_priority, status, queued_at,
                                next_run_at, idempotency_key, payload_summary
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, 'pending', %s,
                                NOW(), %s, %s::jsonb
                            )
                            ON CONFLICT (campaign_lead_id, stage) DO NOTHING
                            """,
                            (
                                cid,
                                lead_id,
                                instance_id,
                                stage,
                                step_priority,
                                queued_at,
                                idempotency_key,
                                payload_summary,
                            ),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception(
                    "enqueue_missing_cadence_outbox_rows failed campaign_id=%s stage=%s",
                    cid,
                    stage,
                )


def _defer_row_next_window(conn, outbox_id: int, campaign_dict: dict) -> None:
    try:
        nv = next_valid_send_utc_naive(campaign_dict, datetime.utcnow())
    except Exception:
        nv = datetime.utcnow() + timedelta(minutes=30)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaign_message_outbox
            SET next_run_at = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (nv, outbox_id),
        )


def _defer_row_retry_later(conn, outbox_id: int, minutes: int = 45) -> None:
    nv = datetime.utcnow() + timedelta(minutes=minutes)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaign_message_outbox
            SET next_run_at = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (nv, outbox_id),
        )


def _reaper_stale_sending(conn) -> None:
    if _REAPER_MINUTES <= 0:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaign_message_outbox
            SET status = 'pending',
                updated_at = NOW()
            WHERE status = 'sending'
              AND updated_at < (NOW() - (%s * INTERVAL '1 minute'))
            """,
            (_REAPER_MINUTES,),
        )


def _passes_throttle_initial(c: dict) -> bool:
    if (c.get("stage") or "").lower() != "initial":
        return True
    return check_initial_chunk_daily_quota_for_campaign(int(c["campaign_id"]))


def _load_step_row(conn, campaign_id: int, step_number: int) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT step_number, message_template, media_path, media_type
            FROM campaign_steps
            WHERE campaign_id = %s AND step_number = %s
            LIMIT 1
            """,
            (campaign_id, step_number),
        )
        row = cur.fetchone()
    return dict(row) if row else {}


def _pick_message_text(conn, campaign_id: int, step_number: int) -> str:
    row = _load_step_row(conn, campaign_id, step_number)
    raw = row.get("message_template") or ""
    msgs = _parse_message_template(raw)
    if msgs:
        return random.choice(msgs)
    if step_number == 1:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT message_template FROM campaigns WHERE id = %s LIMIT 1",
                (campaign_id,),
            )
            crow = cur.fetchone() or {}
        msgs = _parse_message_template(crow.get("message_template") or "")
        if msgs:
            return random.choice(msgs)
    return ""


def _apply_lead_success(
    cur,
    *,
    campaign_id: int,
    lead_id: int,
    stage: str,
    instance_name: str,
    enable_cadence: bool,
) -> None:
    st = (stage or "initial").lower()
    step_n = STAGE_TO_STEP_NUMBER.get(st, 1)

    if st == "initial":
        if enable_cadence:
            cur.execute(
                """
                UPDATE campaign_leads
                SET status = 'sent',
                    sent_at = NOW(),
                    last_sent_stage = 'initial',
                    sent_by_instance = %s,
                    last_message_sent_at = NOW(),
                    current_step = 2,
                    cadence_status = 'monitoring'
                WHERE id = %s AND campaign_id = %s
                """,
                (instance_name, lead_id, campaign_id),
            )
        else:
            cur.execute(
                """
                UPDATE campaign_leads
                SET status = 'sent',
                    sent_at = NOW(),
                    last_sent_stage = 'initial',
                    sent_by_instance = %s,
                    last_message_sent_at = NOW()
                WHERE id = %s AND campaign_id = %s
                """,
                (instance_name, lead_id, campaign_id),
            )
        return

    next_step = min(step_n + 1, 32)
    cur.execute(
        """
        UPDATE campaign_leads
        SET status = 'sent',
            sent_at = NOW(),
            last_sent_stage = %s,
            sent_by_instance = %s,
            last_message_sent_at = NOW(),
            current_step = %s,
            cadence_status = 'monitoring'
        WHERE id = %s AND campaign_id = %s
        """,
        (st, instance_name, next_step, lead_id, campaign_id),
    )


def process_message_outbox_tick(conn) -> None:
    """
    Um envio por tick (global). Feature flag ``USE_MESSAGE_OUTBOX`` (utils.config).
    """
    global _last_outbox_instance_id

    if not USE_MESSAGE_OUTBOX:
        return
    if not uazapi_service:
        return

    _reaper_stale_sending(conn)
    conn.commit()

    enqueue_missing_cadence_outbox_rows(conn)
    conn.commit()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.id, o.campaign_id, o.campaign_lead_id, o.instance_id, o.stage, o.step_priority,
                   o.queued_at, o.next_run_at, o.idempotency_key, o.payload_summary,
                   c.user_id, c.enable_cadence, c.status AS campaign_status,
                   c.send_hour_start, c.send_hour_end, c.send_saturday, c.send_sunday,
                   c.scheduled_start, c.outbox_delay_min_seconds, c.outbox_delay_max_seconds,
                   cl.phone, cl.name AS lead_name, cl.csv_row_order,
                   i.apikey, i.name AS instance_name,
                   COALESCE(i.api_provider, 'megaapi') AS api_provider
            FROM campaign_message_outbox o
            JOIN campaigns c ON c.id = o.campaign_id
            JOIN campaign_leads cl ON cl.id = o.campaign_lead_id
            JOIN instances i ON i.id = o.instance_id
            WHERE o.status = 'pending'
              AND o.next_run_at <= NOW()
              AND c.status IN ('running', 'pending')
              AND (c.scheduled_start IS NULL OR c.scheduled_start <= NOW())
            ORDER BY o.step_priority ASC,
                     COALESCE(cl.csv_row_order, cl.id) ASC,
                     cl.id ASC,
                     o.next_run_at ASC,
                     o.id ASC
            LIMIT 300
            """
        )
        rows = cur.fetchall() or []

    if not rows:
        return

    in_window: list[dict] = []
    for r in rows:
        r = dict(r)
        cd = _campaign_dict_from_row(r)
        if is_campaign_send_window(cd):
            in_window.append(r)

    if not in_window:
        first = dict(rows[0])
        _defer_row_next_window(conn, int(first["id"]), _campaign_dict_from_row(first))
        conn.commit()
        return

    candidates: list[dict] = []
    for r in in_window:
        if r.get("api_provider") != "uazapi" or not (r.get("apikey") or "").strip():
            continue
        if not _passes_throttle_initial(r):
            _defer_row_retry_later(conn, int(r["id"]), minutes=60)
            conn.commit()
            return
        candidates.append(r)

    if not candidates:
        return

    candidates.sort(key=_outbox_csv_sort_key)
    chosen = candidates[0]

    outbox_id = int(chosen["id"])
    campaign_id = int(chosen["campaign_id"])
    lead_id = int(chosen["campaign_lead_id"])
    user_id = int(chosen["user_id"])
    stage = (chosen.get("stage") or "initial").lower()
    step_number = STAGE_TO_STEP_NUMBER.get(stage, 1)
    track_id = (chosen.get("idempotency_key") or "").strip() or f"outbox-{outbox_id}"
    track_source = "campaign_message_outbox"

    # --- (A) Claim curto ---
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, status FROM campaign_message_outbox
                WHERE id = %s AND status = 'pending'
                FOR UPDATE SKIP LOCKED
                """,
                (outbox_id,),
            )
            lk = cur.fetchone()
            if not lk:
                conn.rollback()
                return
            cur.execute(
                """
                UPDATE campaign_message_outbox
                SET status = 'sending', updated_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (outbox_id,),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return
        conn.commit()
    except Exception:
        conn.rollback()
        return

    _last_outbox_instance_id = int(chosen["instance_id"])

    phone_num = _normalize_phone_e164_br(chosen.get("phone"))
    token = (chosen.get("apikey") or "").strip()
    message = _pick_message_text(conn, campaign_id, step_number)
    lead_name = (chosen.get("lead_name") or "Visitante").strip()
    message = (
        message.replace("{{nome}}", lead_name)
        .replace("{{name}}", lead_name)
        .replace("{nome}", lead_name)
        .replace("{name}", lead_name)
    )

    step_row = _load_step_row(conn, campaign_id, step_number)
    media_path = step_row.get("media_path") or ""
    media_type = (step_row.get("media_type") or "image").lower()
    is_sa = _user_is_superadmin(conn, user_id)

    if not phone_num:
        latency_ms = 0
        _persist_outcome(
            conn,
            outbox_id=outbox_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            chosen=chosen,
            http_status=None,
            response_body="missing_phone",
            outcome="failed",
            latency_ms=latency_ms,
            success=False,
            track_from_response=None,
            audit_request={"kind": "none", "reason": "missing_phone"},
            audit_response_body=None,
        )
        return

    started = time.monotonic()
    http_status: Optional[int] = None
    response_body: Optional[str] = None
    result_json: Optional[dict] = None

    audit_request: dict[str, Any]
    # --- (B) HTTP fora de transação ---
    if (
        media_path
        and is_sa
        and _is_media_path_safe(media_path, user_id)
        and os.path.exists(media_path)
    ):
        audit_request = {
            "kind": "media",
            "number": phone_num,
            "media_type": media_type if media_type in ("image", "video") else "image",
            "media_file": os.path.basename(media_path) or media_path,
            "caption_preview": (message or "")[:400],
            "track_id": track_id,
            "track_source": track_source,
        }
        result_json = uazapi_service.send_media_campaign(
            token,
            phone_num or "",
            media_type if media_type in ("image", "video") else "image",
            media_path,
            caption=message or "",
            track_id=track_id,
            track_source=track_source,
        )
        http_status = 200 if result_json else None
        response_body = json.dumps(result_json) if result_json else None
    else:
        if not message.strip():
            latency_ms = int((time.monotonic() - started) * 1000)
            _persist_outcome(
                conn,
                outbox_id=outbox_id,
                campaign_id=campaign_id,
                lead_id=lead_id,
                chosen=chosen,
                http_status=None,
                response_body="missing_message_template",
                outcome="failed",
                latency_ms=latency_ms,
                success=False,
                track_from_response=None,
                audit_request={
                    "kind": "text",
                    "reason": "missing_message_template",
                    "number": phone_num,
                    "step_number": step_number,
                },
                audit_response_body=None,
            )
            return
        audit_request = {
            "kind": "text",
            "number": phone_num,
            "text_preview": message[:500],
            "track_id": track_id,
            "track_source": track_source,
        }
        result_json = uazapi_service.send_text_idempotent(
            token,
            phone_num or "",
            message,
            track_id=track_id,
            track_source=track_source,
        )
        http_status = 200 if result_json else None
        response_body = json.dumps(result_json) if result_json else None

    latency_ms = int((time.monotonic() - started) * 1000)
    success = bool(result_json)

    _persist_outcome(
        conn,
        outbox_id=outbox_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        chosen=chosen,
        http_status=http_status,
        response_body=response_body,
        outcome="sent" if success else "failed",
        latency_ms=latency_ms,
        success=success,
        track_from_response=(result_json or None),
        audit_request=audit_request,
        audit_response_body=result_json if isinstance(result_json, dict) else _truncate(response_body, 4000),
    )


def _persist_outcome(
    conn,
    *,
    outbox_id: int,
    campaign_id: int,
    lead_id: int,
    chosen: dict,
    http_status: Optional[int],
    response_body: Optional[str],
    outcome: str,
    latency_ms: int,
    success: bool,
    track_from_response: Any,
    audit_request: Optional[dict] = None,
    audit_response_body: Any = None,
) -> None:
    """Fase (C): tentativa + estado terminal; §6.1 só se ``success`` e HTTP 200 implícito."""
    stage = (chosen.get("stage") or "initial").lower()
    enable_cadence = bool(chosen.get("enable_cadence"))
    instance_name = chosen.get("instance_name") or ""
    track_stored = None
    if isinstance(track_from_response, dict):
        track_stored = (
            track_from_response.get("messageId")
            or track_from_response.get("message_id")
            or track_from_response.get("id")
        )
        if track_stored is not None:
            track_stored = str(track_stored)
    if not track_stored:
        track_stored = (chosen.get("idempotency_key") or "") or None

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) AS m FROM campaign_send_attempts WHERE outbox_id = %s",
            (outbox_id,),
        )
        attempt_no = int((cur.fetchone() or {}).get("m") or 0) + 1

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_send_attempts
                    (outbox_id, attempt_no, http_status, uazapi_response, outcome, latency_ms, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    outbox_id,
                    attempt_no,
                    http_status,
                    _truncate(response_body),
                    outcome,
                    latency_ms,
                ),
            )

            if success:
                cur.execute(
                    """
                    UPDATE campaign_message_outbox
                    SET status = 'sent',
                        uazapi_track_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (track_stored, outbox_id),
                )
                _apply_lead_success(
                    cur,
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    stage=stage,
                    instance_name=instance_name,
                    enable_cadence=enable_cadence,
                )
                cur.execute(
                    """
                    UPDATE campaigns
                    SET sent_today = COALESCE(sent_today, 0) + 1
                    WHERE id = %s
                    """,
                    (campaign_id,),
                )

                dmin = int(chosen.get("outbox_delay_min_seconds") or 600)
                dmax = int(chosen.get("outbox_delay_max_seconds") or 900)
                lo, hi = min(dmin, dmax), max(dmin, dmax)
                delta_sec = random.randint(lo, hi)
                cur.execute(
                    """
                    UPDATE campaign_message_outbox
                    SET next_run_at = GREATEST(next_run_at, NOW() + (%s * INTERVAL '1 second')),
                        updated_at = NOW()
                    WHERE campaign_id = %s
                      AND status = 'pending'
                      AND id <> %s
                    """,
                    (delta_sec, campaign_id, outbox_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE campaign_message_outbox
                    SET status = 'failed',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (outbox_id,),
                )

        conn.commit()
        _log_outbox_attempt_event(
            campaign_id=campaign_id,
            outbox_id=outbox_id,
            instance_id=int(chosen.get("instance_id") or 0),
            latency_ms=latency_ms,
            outcome=outcome,
            http_status=http_status,
        )
        try:
            uid_audit = int(chosen.get("user_id") or 0)
            if uid_audit:
                append_dispatch_audit_event(
                    user_id=uid_audit,
                    campaign_id=campaign_id,
                    event={
                        "campaign_id": campaign_id,
                        "lead_id": lead_id,
                        "csv_row_order": chosen.get("csv_row_order"),
                        "outbox_id": outbox_id,
                        "stage": stage,
                        "attempt_no": attempt_no,
                        "instance_id": int(chosen.get("instance_id") or 0),
                        "outcome": outcome,
                        "latency_ms": latency_ms,
                        "http_status": http_status,
                        "request": audit_request or {},
                        "response": audit_response_body
                        if audit_response_body is not None
                        else _truncate(response_body, 4000),
                    },
                )
        except Exception:
            logger.exception(
                "append_dispatch_audit_event failed campaign_id=%s outbox_id=%s",
                campaign_id,
                outbox_id,
            )
    except Exception as e:
        conn.rollback()
        observe_campaign_outbox_send_attempt("persist_failed", latency_ms)
        logger.error(
            "%s",
            json.dumps(
                {
                    "event": "campaign_outbox_send_attempt",
                    "campaign_id": campaign_id,
                    "outbox_id": outbox_id,
                    "instance_id": int(chosen.get("instance_id") or 0),
                    "latency_ms": latency_ms,
                    "outcome": "persist_failed",
                    "http_status": http_status,
                    "error_class": type(e).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE campaign_message_outbox
                    SET status = 'pending', updated_at = NOW()
                    WHERE id = %s AND status = 'sending'
                    """,
                    (outbox_id,),
                )
            conn.commit()
        except Exception:
            conn.rollback()


if __name__ == "__main__":
    import psycopg2 as _pg

    def _conn():
        return _pg.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            database=os.environ.get("DB_NAME", "leads_infinitos"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD"),
            port=os.environ.get("DB_PORT", "5432"),
        )

    c = _conn()
    try:
        process_message_outbox_tick(c)
    finally:
        c.close()
