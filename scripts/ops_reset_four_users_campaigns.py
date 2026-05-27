#!/usr/bin/env python3
"""
Reset operacional: 4 usuários — backup pendentes, snapshot da última campanha,
excluir todas as campanhas, recriar a mais recente e disparar chunk inicial (30/dia).

Uso (na raiz do repo, com .env e Postgres acessível):

  python scripts/ops_reset_four_users_campaigns.py --dry-run
  python scripts/ops_reset_four_users_campaigns.py --execute

Saída: backups/ops_reset_<UTC>/ por usuário (CSV *-pending-admin.csv + snapshot JSON).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv


def _safe_load_dotenv(env_path: Path) -> None:
    """Carrega .env ignorando null bytes (Windows / editores que corrompem o arquivo)."""
    if not env_path.is_file():
        load_dotenv()
        return
    try:
        load_dotenv(env_path)
        return
    except ValueError:
        pass
    cleaned = env_path.read_bytes().replace(b"\x00", b"")
    for line in cleaned.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_safe_load_dotenv(ROOT / ".env")

# app.py chama load_dotenv() no import; evitar segunda leitura de .env corrompido.
import dotenv as _dotenv_mod

_dotenv_mod.load_dotenv = lambda *args, **kwargs: True

DEFAULT_USER_IDS = (10, 13, 41, 40)
DAILY_LIMIT = 30


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (name or "campanha").strip())
    return re.sub(r"_+", "_", s).strip("_")[:60] or "campanha"


def _parse_step_messages(raw) -> list:
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


def _fetch_user_email(conn, user_id: int) -> str:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return (row or {}).get("email") or f"user_{user_id}"


def _fetch_latest_campaign(conn, user_id: int) -> Optional[Dict]:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT *
            FROM campaigns
            WHERE user_id = %s
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _fetch_campaign_ids(conn, user_id: int) -> List[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM campaigns WHERE user_id = %s ORDER BY id ASC",
            (user_id,),
        )
        return [int(r[0]) for r in cur.fetchall()]


def _build_snapshot(conn, campaign_id: int) -> dict:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
        camp = dict(cur.fetchone() or {})
        cur.execute(
            """
            SELECT step_number, step_label, message_template, delay_days, media_type, media_path
            FROM campaign_steps WHERE campaign_id = %s ORDER BY step_number
            """,
            (campaign_id,),
        )
        steps = []
        for s in cur.fetchall() or []:
            steps.append(
                {
                    "step_number": s["step_number"],
                    "step_label": s.get("step_label") or "",
                    "message_templates": _parse_step_messages(s.get("message_template")),
                    "delay_days": int(s.get("delay_days") or 0),
                    "media_type": s.get("media_type"),
                    "has_media": bool(s.get("media_path")),
                }
            )
        cur.execute(
            """
            SELECT ci.instance_id
            FROM campaign_instances ci
            WHERE ci.campaign_id = %s
            ORDER BY ci.instance_id
            """,
            (campaign_id,),
        )
        instance_ids = [int(r["instance_id"]) for r in (cur.fetchall() or [])]

    for k, v in list(camp.items()):
        if hasattr(v, "isoformat"):
            camp[k] = v.isoformat()

    camp["message_templates"] = _parse_step_messages(camp.get("message_template"))
    return {
        "source_campaign_id": campaign_id,
        "campaign": camp,
        "steps": steps,
        "instance_ids": instance_ids,
    }


def _export_pending_csvs(conn, user_id: int, out_dir: Path, app_module) -> Tuple[List[Path], List[dict]]:
    """Por campanha: CSV com sufixo -pending-admin; retorna paths e linhas para import."""
    from psycopg2.extras import RealDictCursor

    paths: list[Path] = []
    import_rows: list[dict] = []
    seen_phones: set[str] = set()

    campaign_ids = _fetch_campaign_ids(conn, user_id)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for cid in campaign_ids:
            cur.execute("SELECT name FROM campaigns WHERE id = %s", (cid,))
            name_row = cur.fetchone()
            cname = (name_row or {}).get("name") or "campanha"
            rows = app_module._fetch_remanent_lead_rows(cid, "pending_initial")
            if not rows:
                continue
            slug = _slug(cname)
            fname = f"{user_id}_{cid}_{slug}_pending_initial-pending-admin.csv"
            fpath = out_dir / fname
            csv_text = "\ufeff" + app_module._remanent_rows_to_csv_text(rows)
            fpath.write_text(csv_text, encoding="utf-8")
            paths.append(fpath)
            for r in rows:
                phone = re.sub(r"\D", "", str(r.get("phone") or ""))
                if len(phone) < 10 or phone in seen_phones:
                    continue
                seen_phones.add(phone)
                import_rows.append(
                    {
                        "name": (r.get("name") or "Visitante"),
                        "phone": phone,
                        "whatsapp_link": r.get("whatsapp_link")
                        or f"https://wa.me/{phone}",
                        "status": "1",
                    }
                )

    if import_rows:
        combined = out_dir / f"{user_id}_all_pending_import-pending-admin.csv"
        pd.DataFrame(import_rows).to_csv(combined, index=False, encoding="utf-8")
        paths.append(combined)
    return paths, import_rows


def _create_import_job(conn, user_id: int, csv_path: Path) -> int:
    df = pd.read_csv(csv_path, dtype=str)
    valid_count = len(df)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scraping_jobs (
                user_id, keyword, locations, total_results, lead_count,
                status, results_path, progress, completed_at
            )
            VALUES (%s, %s, %s, %s, %s, 'completed', %s, 100, NOW())
            RETURNING id
            """,
            (
                user_id,
                "Ops reset pending",
                "Backup admin",
                valid_count,
                valid_count,
                str(csv_path),
            ),
        )
        job_id = int(cur.fetchone()[0])
    conn.commit()
    return job_id


def _delete_user_campaigns(conn, user_id: int, app_module) -> list[dict]:
    """Remove campanhas do usuário (Uazapi delete quando possível, depois DB)."""
    results = []
    for cid in _fetch_campaign_ids(conn, user_id):
        err = None
        ok = False
        try:
            success, err = app_module._uazapi_control_campaign(
                cid, user_id, "delete", admin_mode=True
            )
            ok = bool(success)
        except Exception as ex:
            err = str(ex)
        if not ok:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM campaigns WHERE id = %s", (cid,))
            conn.commit()
            ok = True
        results.append({"campaign_id": cid, "ok": ok, "uazapi_error": err})
    return results


def _parse_create_campaign_response(raw) -> Dict:
    if isinstance(raw, tuple):
        body = raw[0]
        code = int(raw[1]) if len(raw) > 1 else 500
    else:
        body, code = raw, 200
    if isinstance(body, (dict, list)):
        parsed = body
    else:
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
    if not isinstance(parsed, dict):
        parsed = {"result": parsed}
    parsed["http"] = code
    return parsed


def _recreate_campaign(conn, user_id: int, snapshot: dict, job_id: int, app_module) -> Dict:
    camp = snapshot.get("campaign") or {}
    payload = {
        "name": camp.get("name") or f"Campanha restaurada {user_id}",
        "job_id": job_id,
        "message_templates": snapshot.get("campaign", {}).get("message_templates")
        or _parse_step_messages(camp.get("message_template")),
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
        "steps": [
            {
                "step_number": s["step_number"],
                "step_label": s.get("step_label", ""),
                "message_templates": s.get("message_templates") or [],
                "delay_days": s.get("delay_days", 0),
            }
            for s in (snapshot.get("steps") or [])
        ],
        "daily_limit": DAILY_LIMIT,
        "cadence_setup_mode": "now",
    }
    if camp.get("cadence_config"):
        cc = camp["cadence_config"]
        if isinstance(cc, str):
            try:
                cc = json.loads(cc)
            except json.JSONDecodeError:
                cc = {}
        if isinstance(cc, dict) and cc.get("cadence_setup_mode"):
            payload["cadence_setup_mode"] = cc["cadence_setup_mode"]

    raw = app_module._create_campaign_core(user_id, payload, admin_id=None)
    return _parse_create_campaign_response(raw)


def _force_initial_chunk(campaign_id: int, user_id: int, app_module) -> dict:
    r = app_module._continue_initial_chunk_core(
        campaign_id,
        user_id,
        log_label="ops-reset-four-users",
        cancel_scheduled=True,
    )
    return {
        "ok": bool(r.get("ok")),
        "status_code": r.get("status_code"),
        "body": r.get("body"),
    }


def process_user(
    conn,
    user_id: int,
    out_root: Path,
    *,
    execute: bool,
    app_module,
) -> dict:
    email = _fetch_user_email(conn, user_id)
    user_dir = out_root / f"user_{user_id}_{_slug(email)}"
    user_dir.mkdir(parents=True, exist_ok=True)

    latest = _fetch_latest_campaign(conn, user_id)
    report = {
        "user_id": user_id,
        "email": email,
        "latest_campaign_id": latest.get("id") if latest else None,
        "campaign_ids_before": _fetch_campaign_ids(conn, user_id),
    }

    if not latest:
        report["error"] = "Nenhuma campanha encontrada para snapshot."
        return report

    snapshot = _build_snapshot(conn, int(latest["id"]))
    (user_dir / f"{user_id}_campaign_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report["snapshot_path"] = str(user_dir / f"{user_id}_campaign_snapshot.json")

    csv_paths, import_rows = _export_pending_csvs(conn, user_id, user_dir, app_module)
    report["csv_files"] = [str(p) for p in csv_paths]
    report["pending_import_count"] = len(import_rows)

    if not execute:
        report["dry_run"] = True
        report["would_delete_campaigns"] = report["campaign_ids_before"]
        report["would_recreate_from_campaign_id"] = snapshot["source_campaign_id"]
        return report

    if not import_rows:
        report["error"] = "Nenhum lead pending_initial para importar; abortando recreate."
        return report

    combined = user_dir / f"{user_id}_all_pending_import-pending-admin.csv"
    if not combined.is_file():
        pd.DataFrame(import_rows).to_csv(combined, index=False, encoding="utf-8")

    deletes = _delete_user_campaigns(conn, user_id, app_module)
    report["deleted"] = deletes

    job_id = _create_import_job(conn, user_id, combined)
    report["import_job_id"] = job_id

    created = _recreate_campaign(conn, user_id, snapshot, job_id, app_module)
    report["create_result"] = created
    new_id = created.get("campaign_id")
    if not new_id and created.get("success"):
        latest2 = _fetch_latest_campaign(conn, user_id)
        new_id = latest2.get("id") if latest2 else None
    report["new_campaign_id"] = new_id
    if created.get("http", 200) >= 400 or created.get("error"):
        report["error"] = created.get("error") or f"Falha ao criar campanha (HTTP {created.get('http')})"
        return report
    if new_id:
        report["force_chunk"] = _force_initial_chunk(int(new_id), user_id, app_module)
    else:
        report["force_chunk"] = {"skipped": True, "reason": "campanha não criada"}

    (user_dir / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset campanhas dos 4 usuários operacionais.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa delete + recreate + chunk (padrão: só backup dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias explícito (default se --execute ausente).",
    )
    parser.add_argument(
        "--user-ids",
        type=str,
        default=",".join(str(x) for x in DEFAULT_USER_IDS),
        help="IDs separados por vírgula (default: 10,13,41,40).",
    )
    args = parser.parse_args()
    execute = bool(args.execute)
    user_ids = [int(x.strip()) for x in args.user_ids.split(",") if x.strip()]

    import app as app_module

    out_root = ROOT / "backups" / f"ops_reset_{_utc_stamp()}"
    out_root.mkdir(parents=True, exist_ok=True)

    from app import app, get_db_connection

    summary = {
        "execute": execute,
        "user_ids": user_ids,
        "output_dir": str(out_root),
        "daily_limit": DAILY_LIMIT,
        "users": [],
    }

    with app.app_context():
        conn = get_db_connection()
        try:
            for uid in user_ids:
                print(f"--- user_id={uid} execute={execute} ---")
                rep = process_user(
                    conn, uid, out_root, execute=execute, app_module=app_module
                )
                summary["users"].append(rep)
                print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        finally:
            conn.close()

    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nResumo gravado em {out_root / 'summary.json'}")
    if not execute:
        print("Dry-run: nada foi apagado nem recriado. Use --execute para aplicar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
