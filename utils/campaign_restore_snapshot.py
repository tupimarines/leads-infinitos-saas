"""
Snapshot de campanha para backup/recriação: mensagens, cadência, instâncias e payload admin.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional


def parse_message_templates(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
            if loaded:
                return [str(loaded).strip()]
        except json.JSONDecodeError:
            if raw.strip():
                return [raw.strip()]
    return []


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def slug_for_filename(name: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\-]+", "_", (name or "campanha").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s or "campanha")[:max_len]


def build_create_campaign_payload(snapshot: Dict) -> Dict:
    """Payload compatível com ``POST /api/admin/campaigns`` / ``_create_campaign_core``."""
    camp = snapshot.get("campaign") or {}
    cc = camp.get("cadence_config")
    cadence_setup_mode = "now"
    if isinstance(cc, dict) and cc.get("cadence_setup_mode"):
        cadence_setup_mode = str(cc["cadence_setup_mode"])

    daily_limit = camp.get("daily_limit")
    try:
        daily_limit = int(daily_limit) if daily_limit is not None else 30
    except (TypeError, ValueError):
        daily_limit = 30

    return {
        "name": camp.get("name") or f"Campanha restaurada {snapshot.get('source_campaign_id')}",
        "message_templates": camp.get("message_templates")
        or parse_message_templates(camp.get("message_template")),
        "instance_ids": snapshot.get("instance_ids") or [],
        "rotation_mode": camp.get("rotation_mode") or "single",
        "delay_min_minutes": camp.get("delay_min_minutes"),
        "delay_max_minutes": camp.get("delay_max_minutes"),
        "send_hour_start": camp.get("send_hour_start", 8),
        "send_hour_end": camp.get("send_hour_end", 20),
        "send_saturday": bool(camp.get("send_saturday")),
        "send_sunday": bool(camp.get("send_sunday")),
        "enable_cadence": bool(camp.get("enable_cadence")),
        "terms_accepted": bool(camp.get("terms_accepted")),
        "daily_limit": daily_limit,
        "cadence_setup_mode": cadence_setup_mode,
        "scheduled_start": camp.get("scheduled_start"),
        "steps": [
            {
                "step_number": s["step_number"],
                "step_label": s.get("step_label", ""),
                "message_templates": s.get("message_templates") or [],
                "delay_days": s.get("delay_days", 0),
            }
            for s in (snapshot.get("steps") or [])
        ],
        "_note": "Preencha job_id após upload do CSV de pendentes (admin validate-csv ou scraping_jobs).",
    }


def build_campaign_restore_snapshot(conn, campaign_id: int) -> Dict:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.*, u.email AS user_email
            FROM campaigns c
            JOIN users u ON u.id = c.user_id
            WHERE c.id = %s
            """,
            (campaign_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Campanha {campaign_id} não encontrada.")
        camp = dict(row)

        cur.execute(
            """
            SELECT step_number, step_label, message_template, delay_days, media_type, media_path
            FROM campaign_steps
            WHERE campaign_id = %s
            ORDER BY step_number
            """,
            (campaign_id,),
        )
        steps_out = []
        for s in cur.fetchall() or []:
            msgs = parse_message_templates(s.get("message_template"))
            steps_out.append(
                {
                    "step_number": int(s["step_number"]),
                    "step_label": s.get("step_label") or "",
                    "message_templates": msgs,
                    "delay_days": int(s.get("delay_days") or 0),
                    "media_type": s.get("media_type"),
                    "media_path": s.get("media_path"),
                    "has_media": bool(s.get("media_path")),
                }
            )

        cur.execute(
            """
            SELECT ci.instance_id, i.name AS instance_name, i.status AS instance_status
            FROM campaign_instances ci
            JOIN instances i ON i.id = ci.instance_id
            WHERE ci.campaign_id = %s
            ORDER BY ci.instance_id
            """,
            (campaign_id,),
        )
        instances = [dict(r) for r in (cur.fetchall() or [])]
        instance_ids = [int(r["instance_id"]) for r in instances]

    camp = _json_safe(camp)
    camp["message_templates"] = parse_message_templates(camp.get("message_template"))

    snapshot = {
        "source_campaign_id": int(campaign_id),
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "campaign": camp,
        "steps": steps_out,
        "instance_ids": instance_ids,
        "instances": _json_safe(instances),
        "messages_for_reimport": {
            "initial_message_templates": camp["message_templates"],
            "cadence_steps": [
                {
                    "step_number": s["step_number"],
                    "step_label": s["step_label"],
                    "message_templates": s["message_templates"],
                    "delay_days": s["delay_days"],
                }
                for s in steps_out
            ],
        },
    }
    snapshot["create_campaign_payload"] = build_create_campaign_payload(snapshot)
    return snapshot
