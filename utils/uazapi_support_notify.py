"""
Notificação ao dono da campanha quando a instância Uazapi está desconectada
ou reconecta. Token: SUPPORT_UAZAPI_INSTANCE_TOKEN.
Cooldown disconnect: instances.last_disconnect_notify_at.
Cooldown WhatsApp reconexão: tabela ``uazapi_reconnect_whatsapp_log`` (por utilizador + instância).
Fila in-app: reconnect_inapp_alerts (Task 5 desconexão).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# (instance_id, monotonic) — TTL 60s para get_status
_status_cache: dict[int, tuple[Any, float]] = {}
_STATUS_CACHE_TTL_SEC = 60.0


def get_instance_status_cached(
    uazapi_service, instance_id: int, token: str
) -> Optional[dict[str, Any]]:
    """
    get_status com cache 60s por instance_id (reduz chamadas no worker 30s).
    """
    if not uazapi_service or not token:
        return None
    now = time.monotonic()
    if instance_id in _status_cache:
        data, ts = _status_cache[instance_id]
        if now - ts < _STATUS_CACHE_TTL_SEC:
            return data
    st = uazapi_service.get_status((token or "").strip())
    _status_cache[instance_id] = (st, now)
    return st


def _parse_disconnected_cooldown_hours() -> int:
    raw = os.environ.get("SUPPORT_NOTIFY_DISCONNECT_COOLDOWN_HOURS", "24")
    try:
        h = int((raw or "24").strip())
    except (TypeError, ValueError):
        h = 24
    return max(1, min(h, 168))


def is_instance_disconnected_status(status_payload: Optional[dict[str, Any]]) -> bool:
    if not status_payload:
        return False
    inst = status_payload.get("instance")
    if isinstance(inst, dict):
        st = (inst.get("status") or inst.get("state") or "").lower()
    else:
        st = (status_payload.get("status") or status_payload.get("state") or "").lower()
    return st in ("disconnected", "close", "closed", "logout", "offline")


def _user_display_name(email: Optional[str]) -> str:
    if not email or not str(email).strip():
        return "Cliente"
    local = str(email).split("@", 1)[0].replace(".", " ").replace("_", " ")
    return local.strip().title() or "Cliente"


def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def _support_message(nome: str) -> str:
    # Texto de produto: sem * se preferir plano; manter * para WhatsApp
    return (
        f"{nome}, sua instância de Whatsapp está desconectada. "
        f"Acesse o *Leads Infinitos* e reconecte para garantir o funcionamento da sua automação."
    )


def maybe_send_disconnect_support_whatsapp(
    conn,
    uazapi_service,
    *,
    campaign_id: int,
    user_id: int,
    instance_id: int,
    context: Optional[dict] = None,
) -> str:
    """
    Envia 1x por janela de cooldown por instância.
    Returns: "sent" | "skipped_cooldown" | "disabled" | "no_support_token" | "no_user_phone" | "send_failed" | "error:..."
    """
    ctx = context or {}
    if (os.environ.get("SUPPORT_UAZAPI_NOTIFY_ENABLED", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )):
        return "disabled"
    support_token = (os.environ.get("SUPPORT_UAZAPI_INSTANCE_TOKEN") or "").strip()
    if not support_token or not uazapi_service:
        print(
            json.dumps(
                {
                    "event": "uazapi_disconnect_notify",
                    "reason": "no_support_token",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                    "user_id": user_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "no_support_token"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, last_disconnect_notify_at FROM instances WHERE id = %s", (instance_id,))
        row = cur.fetchone()
    if not row:
        return "error:instance_not_found"
    last = row.get("last_disconnect_notify_at")
    cool_h = _parse_disconnected_cooldown_hours()
    if last is not None:
        try:
            if getattr(last, "tzinfo", None) is not None:
                from datetime import timezone

                last_cmp = last.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                last_cmp = last
        except Exception:
            last_cmp = None
        if last_cmp is not None and datetime.utcnow() - last_cmp < timedelta(
            hours=cool_h
        ):
            print(
                json.dumps(
                    {
                        "event": "uazapi_disconnect_notify_skipped_cooldown",
                        "campaign_id": campaign_id,
                        "instance_id": instance_id,
                        "user_id": user_id,
                        "last_notified_at": str(last),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return "skipped_cooldown"

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, email, display_name, phone_e164 FROM users WHERE id = %s", (user_id,))
        u = cur.fetchone()
    if not u:
        return "error:no_user"
    email = (u.get("email") or "").strip()
    nome = (u.get("display_name") or "").strip() or _user_display_name(email)
    dest = (u.get("phone_e164") or "").strip()
    digits = _digits_only(dest)
    if not digits or len(digits) < 10:
        print(
            json.dumps(
                {
                    "event": "uazapi_disconnect_notify",
                    "reason": "no_user_phone",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                    "user_id": user_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "no_user_phone"

    if len(digits) <= 11 and not digits.startswith("55"):
        digits = "55" + digits

    text = _support_message(nome)
    try:
        r = uazapi_service.send_text(support_token, digits, text)
    except Exception as e:
        print(
            json.dumps(
                {
                    "event": "uazapi_disconnect_notify_error",
                    "error": str(e)[:500],
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return f"error:{e!s}"[:200]

    if r is None:
        print(
            json.dumps(
                {
                    "event": "uazapi_disconnect_notify_send_failed",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "send_failed"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE instances SET last_disconnect_notify_at = NOW() WHERE id = %s",
            (instance_id,),
        )
    print(
        json.dumps(
            {
                "event": "uazapi_disconnect_notify_sent",
                "campaign_id": campaign_id,
                "instance_id": instance_id,
                "user_id": user_id,
                **(ctx or {}),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return "sent"


def _parse_reconnect_inapp_cooldown_hours() -> int:
    raw = os.environ.get("SUPPORT_RECONNECT_INAPP_COOLDOWN_HOURS", "6")
    try:
        h = int((raw or "6").strip())
    except (TypeError, ValueError):
        h = 6
    return max(1, min(h, 168))


def _parse_reconnect_whatsapp_cooldown_hours() -> int:
    raw = os.environ.get("SUPPORT_RECONNECT_NOTIFY_COOLDOWN_HOURS", "24")
    try:
        h = int((raw or "24").strip())
    except (TypeError, ValueError):
        h = 24
    return max(1, min(h, 168))


def _reconnect_support_message(nome: str, instance_name: str, instance_id: int, n_campaigns: int) -> str:
    label = (instance_name or "").strip() or f"ID {instance_id}"
    return (
        f"{nome}, a instância «{label}» voltou a ficar *ligada* no WhatsApp. "
        f"Há {n_campaigns} campanha(s) que foram pausadas automaticamente por desconexão — "
        f"abra o *Leads Infinitos* e use *Retomar* em Minhas Campanhas quando quiser."
    )


def enqueue_reconnect_inapp_alert(
    conn,
    *,
    user_id: int,
    instance_id: int,
    instance_name: str,
    campaign_count: int,
) -> str:
    """
    Um alerta in-app por utilizador + instância por janela (cooldown).
    Returns: inserted | skipped_cooldown
    """
    cool_h = _parse_reconnect_inapp_cooldown_hours()
    body = (
        f"A instância «{(instance_name or '').strip() or '—'}» (ID {instance_id}) voltou a ficar ligada. "
        f"Existem {campaign_count} campanha(s) pausadas automaticamente por desconexão — "
        f"retome em Minhas Campanhas quando quiser."
    )
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id FROM reconnect_inapp_alerts
            WHERE user_id = %s AND instance_id = %s
              AND created_at > (NOW() - (%s * INTERVAL '1 hour'))
            LIMIT 1
            """,
            (user_id, instance_id, cool_h),
        )
        if cur.fetchone():
            return "skipped_cooldown"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reconnect_inapp_alerts (user_id, instance_id, instance_name, campaign_count, body_text)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                instance_id,
                (instance_name or "")[:500],
                int(campaign_count),
                body[:2000],
            ),
        )
    return "inserted"


def maybe_send_reconnect_support_whatsapp(
    conn,
    uazapi_service,
    *,
    campaign_id: int,
    user_id: int,
    instance_id: int,
    instance_name: str,
    n_campaigns: int,
    context: Optional[dict] = None,
) -> str:
    """
    Irmã de maybe_send_disconnect_support_whatsapp: notifica reconexão com cooldown em
    instances.last_reconnect_notify_at.
    """
    ctx = context or {}
    if (os.environ.get("SUPPORT_UAZAPI_RECONNECT_NOTIFY_ENABLED", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )):
        return "disabled"
    support_token = (os.environ.get("SUPPORT_UAZAPI_INSTANCE_TOKEN") or "").strip()
    if not support_token or not uazapi_service:
        print(
            json.dumps(
                {
                    "event": "uazapi_reconnect_notify",
                    "reason": "no_support_token",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                    "user_id": user_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "no_support_token"
    cool_h = _parse_reconnect_whatsapp_cooldown_hours()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT 1 FROM uazapi_reconnect_whatsapp_log
            WHERE user_id = %s AND instance_id = %s
              AND sent_at > (NOW() - (%s * INTERVAL '1 hour'))
            LIMIT 1
            """,
            (user_id, instance_id, cool_h),
        )
        if cur.fetchone():
            print(
                json.dumps(
                    {
                        "event": "uazapi_reconnect_notify_skipped_cooldown",
                        "campaign_id": campaign_id,
                        "instance_id": instance_id,
                        "user_id": user_id,
                        "scope": "per_user_instance",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return "skipped_cooldown"

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, email, display_name, phone_e164 FROM users WHERE id = %s", (user_id,))
        u = cur.fetchone()
    if not u:
        return "error:no_user"
    email = (u.get("email") or "").strip()
    nome = (u.get("display_name") or "").strip() or _user_display_name(email)
    dest = (u.get("phone_e164") or "").strip()
    digits = _digits_only(dest)
    if not digits or len(digits) < 10:
        print(
            json.dumps(
                {
                    "event": "uazapi_reconnect_notify",
                    "reason": "no_user_phone",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                    "user_id": user_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "no_user_phone"

    if len(digits) <= 11 and not digits.startswith("55"):
        digits = "55" + digits

    text = _reconnect_support_message(nome, instance_name, instance_id, n_campaigns)
    try:
        r = uazapi_service.send_text(support_token, digits, text)
    except Exception as e:
        print(
            json.dumps(
                {
                    "event": "uazapi_reconnect_notify_error",
                    "error": str(e)[:500],
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return f"error:{e!s}"[:200]

    if r is None:
        print(
            json.dumps(
                {
                    "event": "uazapi_reconnect_notify_send_failed",
                    "campaign_id": campaign_id,
                    "instance_id": instance_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return "send_failed"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO uazapi_reconnect_whatsapp_log (user_id, instance_id)
            VALUES (%s, %s)
            """,
            (user_id, instance_id),
        )
    print(
        json.dumps(
            {
                "event": "uazapi_reconnect_notify_sent",
                "campaign_id": campaign_id,
                "instance_id": instance_id,
                "user_id": user_id,
                "n_campaigns": n_campaigns,
                **(ctx or {}),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return "sent"


def fetch_reconnect_inapp_alerts_for_user(conn, user_id: int, *, hours: int = 72) -> list[dict[str, Any]]:
    """
    Banners pendentes para o dashboard / Minhas Campanhas (criados pelo worker na transição desligado→ligado).
    """
    h = max(1, min(int(hours or 72), 24 * 30))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, instance_id, instance_name, campaign_count, body_text, created_at
            FROM reconnect_inapp_alerts
            WHERE user_id = %s
              AND created_at > (NOW() - (%s * INTERVAL '1 hour'))
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (user_id, h),
        )
        rows = cur.fetchall() or []
    return [dict(x) for x in rows]
