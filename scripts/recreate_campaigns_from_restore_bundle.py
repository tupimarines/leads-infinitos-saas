#!/usr/bin/env python3
"""
Recria campanhas a partir de pasta exportada por export_campaign_restore_bundle.py.

Uso (container /app):

  python scripts/recreate_campaigns_from_restore_bundle.py \\
    --backup-dir /app/backups/campaign_restore_20260527_190516 \\
    --source-campaign-ids 202,207,269,270

  python scripts/recreate_campaigns_from_restore_bundle.py --backup-dir ... --dry-run
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

import dotenv as _dotenv_mod

_dotenv_mod.load_dotenv = lambda *args, **kwargs: True


def _find_backup_files(backup_dir: Path, source_campaign_id: int) -> Tuple[Path, Path]:
    snap = list(backup_dir.glob(f"campaign_{source_campaign_id}_*_restore_snapshot.json"))
    csvs = list(backup_dir.glob(f"campaign_{source_campaign_id}_*_pending_initial-pending-admin.csv"))
    if not snap:
        raise FileNotFoundError(
            f"Snapshot não encontrado: campaign_{source_campaign_id}_*_restore_snapshot.json em {backup_dir}"
        )
    if not csvs:
        raise FileNotFoundError(
            f"CSV pendentes não encontrado: campaign_{source_campaign_id}_*_pending_initial-pending-admin.csv"
        )
    return snap[0], csvs[0]


def _remanent_csv_to_import_path(
    remanent_csv: Path, user_id: int, source_campaign_id: int
) -> Path:
    """Converte export remanescente (status pending) para CSV de import (status=1)."""
    df = pd.read_csv(remanent_csv, dtype=str, encoding="utf-8-sig")
    cols = {c.lower(): c for c in df.columns}
    phone_col = cols.get("phone") or next(
        (cols[c] for c in cols if "phone" in c or "tel" in c), None
    )
    name_col = cols.get("name") or cols.get("nome")
    link_col = cols.get("whatsapp_link")
    if not phone_col:
        raise ValueError(f"Coluna phone ausente em {remanent_csv}")

    rows = []
    for _, row in df.iterrows():
        phone = re.sub(r"\D", "", str(row.get(phone_col) or ""))
        if len(phone) < 10:
            continue
        name = str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else "Visitante"
        link = ""
        if link_col and pd.notna(row.get(link_col)):
            link = str(row[link_col]).strip()
        if not link:
            link = f"https://wa.me/{phone}"
        rows.append({"name": name, "phone": phone, "whatsapp_link": link, "status": "1"})

    if not rows:
        raise ValueError(f"Nenhum lead válido em {remanent_csv}")

    user_dir = Path(os.environ.get("STORAGE_DIR", "storage")) / str(user_id) / "uploads"
    user_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = user_dir / f"restore_c{source_campaign_id}_{stamp}.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    return out


def _create_import_job(conn, user_id: int, csv_path: Path, campaign_name: str) -> int:
    df = pd.read_csv(csv_path, dtype=str)
    n = len(df)
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
                f"Restore: {campaign_name[:80]}",
                "Backup admin",
                n,
                n,
                str(csv_path),
            ),
        )
        job_id = int(cur.fetchone()[0])
    conn.commit()
    return job_id


def _parse_create_response(raw) -> Dict:
    if isinstance(raw, tuple):
        body, code = raw[0], int(raw[1]) if len(raw) > 1 else 500
    else:
        body, code = raw, 200
    if isinstance(body, dict):
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


def _latest_campaign_id(conn, user_id: int) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM campaigns
            WHERE user_id = %s
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def recreate_one(
    conn,
    app_module,
    backup_dir: Path,
    source_campaign_id: int,
    *,
    daily_limit: int,
    dry_run: bool,
) -> Dict:
    snap_path, csv_path = _find_backup_files(backup_dir, source_campaign_id)
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    camp = snapshot.get("campaign") or {}
    user_id = int(camp["user_id"])
    name = camp.get("name") or f"Campanha {source_campaign_id}"
    payload = dict(snapshot.get("create_campaign_payload") or {})
    payload.pop("_note", None)
    payload["name"] = name
    payload["daily_limit"] = daily_limit

    report = {
        "source_campaign_id": source_campaign_id,
        "user_id": user_id,
        "name": name,
        "snapshot_file": str(snap_path),
        "pending_csv": str(csv_path),
        "dry_run": dry_run,
    }

    import_csv = _remanent_csv_to_import_path(csv_path, user_id, source_campaign_id)
    report["import_csv"] = str(import_csv)
    report["import_rows"] = len(pd.read_csv(import_csv, dtype=str))

    if dry_run:
        report["would_create"] = True
        return report

    if not payload.get("instance_ids"):
        raise ValueError(f"Campanha {source_campaign_id}: instance_ids vazio no snapshot.")

    job_id = _create_import_job(conn, user_id, import_csv, name)
    payload["job_id"] = job_id
    report["job_id"] = job_id

    created = _parse_create_response(
        app_module._create_campaign_core(user_id, payload, admin_id=None)
    )
    report["create_result"] = created
    if created.get("http", 200) >= 400 or created.get("error"):
        report["error"] = created.get("error") or f"HTTP {created.get('http')}"
        return report

    new_id = created.get("campaign_id") or _latest_campaign_id(conn, user_id)
    report["new_campaign_id"] = new_id
    if not new_id:
        report["error"] = "Campanha criada mas ID não encontrado."
        return report

    chunk = app_module._continue_initial_chunk_core(
        int(new_id),
        user_id,
        log_label="recreate-from-restore-bundle",
        cancel_scheduled=True,
    )
    report["force_chunk"] = {
        "ok": bool(chunk.get("ok")),
        "status_code": chunk.get("status_code"),
        "body": chunk.get("body"),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Recria campanhas a partir do backup restore.")
    parser.add_argument("--backup-dir", type=str, required=True)
    parser.add_argument(
        "--source-campaign-ids",
        type=str,
        default="202,207,269,270",
        help="IDs das campanhas originais (nomes dos arquivos no backup).",
    )
    parser.add_argument("--daily-limit", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    if not backup_dir.is_dir():
        print(f"Pasta inexistente: {backup_dir}", file=sys.stderr)
        return 1

    ids = [int(x.strip()) for x in args.source_campaign_ids.split(",") if x.strip()]

    import app as app_module
    from app import app, get_db_connection

    summary = {"backup_dir": str(backup_dir), "results": []}

    with app.app_context():
        conn = get_db_connection()
        try:
            for sid in ids:
                print(f"=== Recriar (origem ID {sid}) ===")
                try:
                    rep = recreate_one(
                        conn,
                        app_module,
                        backup_dir,
                        sid,
                        daily_limit=args.daily_limit,
                        dry_run=args.dry_run,
                    )
                    summary["results"].append(rep)
                    if rep.get("error"):
                        print(f"ERRO: {rep['error']}")
                    elif args.dry_run:
                        print(
                            f"DRY-RUN OK: {rep['name']} user={rep['user_id']} "
                            f"leads={rep.get('import_rows')}"
                        )
                    else:
                        print(
                            f"OK: new_campaign_id={rep.get('new_campaign_id')} "
                            f"job_id={rep.get('job_id')} leads={rep.get('import_rows')}"
                        )
                except Exception as e:
                    err = {"source_campaign_id": sid, "error": str(e)}
                    summary["results"].append(err)
                    print(f"ERRO {sid}: {e}")
        finally:
            conn.close()

    out = backup_dir / "recreate_report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatório: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
