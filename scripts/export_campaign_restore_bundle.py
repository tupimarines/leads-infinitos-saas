#!/usr/bin/env python3
"""
Exporta snapshot JSON (mensagens + regras) e CSV de pendentes iniciais por campanha.

Exemplo (campanhas do relatório admin):

  python scripts/export_campaign_restore_bundle.py --campaign-ids 202,207,269,270

Saída: backups/campaign_restore_<UTC>/campaign_<id>_*_restore_snapshot.json
       e *_pending_initial-pending-admin.csv quando houver pendentes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

DEFAULT_CAMPAIGN_IDS = (202, 207, 269, 270)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta mensagens/regras e CSV pendentes.")
    parser.add_argument(
        "--campaign-ids",
        type=str,
        default=",".join(str(x) for x in DEFAULT_CAMPAIGN_IDS),
        help="IDs separados por vírgula",
    )
    parser.add_argument(
        "--skip-csv",
        action="store_true",
        help="Só JSON de mensagens/regras, sem CSV de pendentes.",
    )
    args = parser.parse_args()
    campaign_ids = [int(x.strip()) for x in args.campaign_ids.split(",") if x.strip()]

    from utils.campaign_restore_snapshot import build_campaign_restore_snapshot, slug_for_filename

    import app as app_module
    from app import app, get_db_connection

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "backups" / f"campaign_restore_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {"output_dir": str(out_dir), "campaigns": []}

    with app.app_context():
        conn = get_db_connection()
        try:
            for cid in campaign_ids:
                entry = {"campaign_id": cid}
                try:
                    snapshot = build_campaign_restore_snapshot(conn, cid)
                except ValueError as e:
                    entry["error"] = str(e)
                    summary["campaigns"].append(entry)
                    print(f"ERRO campanha {cid}: {e}")
                    continue

                cname = (snapshot.get("campaign") or {}).get("name") or "campanha"
                slug = slug_for_filename(cname)
                json_path = out_dir / f"campaign_{cid}_{slug}_restore_snapshot.json"
                json_path.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                entry["snapshot_path"] = str(json_path)
                entry["name"] = cname
                entry["status"] = (snapshot.get("campaign") or {}).get("status")
                entry["user_email"] = (snapshot.get("campaign") or {}).get("user_email")
                entry["initial_messages_count"] = len(
                    (snapshot.get("messages_for_reimport") or {}).get(
                        "initial_message_templates"
                    )
                    or []
                )
                entry["cadence_steps_count"] = len(
                    (snapshot.get("messages_for_reimport") or {}).get("cadence_steps") or []
                )

                if not args.skip_csv:
                    rows = app_module._fetch_remanent_lead_rows(cid, "pending_initial")
                    entry["pending_initial_count"] = len(rows)
                    if rows:
                        csv_path = (
                            out_dir
                            / f"campaign_{cid}_{slug}_pending_initial-pending-admin.csv"
                        )
                        csv_path.write_text(
                            "\ufeff" + app_module._remanent_rows_to_csv_text(rows),
                            encoding="utf-8",
                        )
                        entry["pending_csv_path"] = str(csv_path)

                summary["campaigns"].append(entry)
                print(
                    f"OK {cid} {cname}: JSON -> {json_path.name}"
                    + (
                        f", pendentes={entry.get('pending_initial_count', 0)}"
                        if not args.skip_csv
                        else ""
                    )
                )
        finally:
            conn.close()

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nPasta: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
