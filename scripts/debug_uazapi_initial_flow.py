#!/usr/bin/env python3
"""
Traço do fluxo inicial Uazapi (chunks de 30): DB + opcional API.

Para diagnóstico completo (token + listfolders + contagens + prompt para agente), use:
  python scripts/diagnostico_campanha_uazapi.py <campaign_id>

Uso:
  python scripts/debug_uazapi_initial_flow.py <campaign_id>
  UAZAPI_DEBUG=1 já loga o JSON de create_advanced_campaign no worker; este script foca em estado local.

Requer .env com DB_* e instância com apikey para chamadas à API (opcional).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug Uazapi initial chunks (DB + API)")
    parser.add_argument("campaign_id", type=int, help="ID da campanha")
    parser.add_argument(
        "--api",
        action="store_true",
        help="Chama list_folders/get_status por instância (token no banco)",
    )
    args = parser.parse_args()
    cid = args.campaign_id

    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "devpassword"),
        port=os.environ.get("DB_PORT", "5432"),
        cursor_factory=RealDictCursor,
    )
    cur = conn.cursor()

    print(f"=== Campanha {cid} ===\n")
    cur.execute(
        """
        SELECT id, name, use_uazapi_sender, enable_cadence, status,
               delay_min_minutes, delay_max_minutes, scheduled_start
        FROM campaigns WHERE id = %s
        """,
        (cid,),
    )
    c = cur.fetchone()
    if not c:
        print("Campanha não encontrada.")
        return 1
    print("campaigns:", dict(c))

    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'pending' AND current_step = 1) AS pending_initial,
               COUNT(*) FILTER (WHERE current_step = 1) AS total_step1
        FROM campaign_leads
        WHERE campaign_id = %s AND COALESCE(removed_from_funnel, FALSE) = FALSE
        """,
        (cid,),
    )
    counts = cur.fetchone()
    print("leads:", dict(counts or {}))

    cur.execute(
        """
        SELECT css.id, css.instance_id, css.status, css.scheduled_for, css.uazapi_folder_id,
               css.planned_count, css.success_count, css.failed_count,
               css.lead_ids, css.last_sync_at
        FROM campaign_stage_sends css
        WHERE css.campaign_id = %s AND css.stage = 'initial'
        ORDER BY css.id DESC
        LIMIT 20
        """,
        (cid,),
    )
    sends = cur.fetchall() or []
    print(f"\ncampaign_stage_sends (initial, últimos {len(sends)}):")
    for s in sends:
        row = dict(s)
        lids = row.get("lead_ids")
        if isinstance(lids, str):
            try:
                lids = json.loads(lids)
            except Exception:
                pass
        if isinstance(lids, list):
            row["lead_ids"] = f"[{len(lids)} ids] {lids[:5]}{'...' if len(lids) > 5 else ''}"
        print(" ", row)

    cur.execute(
        """
        SELECT i.id, i.name, COALESCE(i.api_provider, 'megaapi') AS api_provider,
               CASE WHEN i.apikey IS NOT NULL AND length(trim(i.apikey)) > 0 THEN '[set]' ELSE '[empty]' END AS apikey
        FROM campaign_instances ci
        JOIN instances i ON i.id = ci.instance_id
        WHERE ci.campaign_id = %s
        ORDER BY i.id
        """,
        (cid,),
    )
    print("\ninstâncias:", [dict(r) for r in (cur.fetchall() or [])])

    # Próximos 30 elegíveis (mesma lógica conceitual do materialize)
    cur.execute(
        """
        SELECT cl.id, cl.phone, cl.whatsapp_link, cl.status, cl.current_step
        FROM campaign_leads cl
        WHERE cl.campaign_id = %s
          AND cl.status IN ('sent', 'pending')
          AND cl.current_step = 1
          AND COALESCE(cl.removed_from_funnel, FALSE) = FALSE
          AND COALESCE(cl.cadence_status, 'active') NOT IN ('converted', 'lost')
          AND cl.id NOT IN (
            SELECT (elem)::int FROM campaign_stage_sends css,
            LATERAL jsonb_array_elements_text(COALESCE(css.lead_ids, '[]'::jsonb)) AS elem
            WHERE css.campaign_id = %s AND css.stage = 'initial' AND css.uazapi_folder_id IS NOT NULL
          )
        ORDER BY COALESCE(cl.send_batch, 999) ASC, cl.id ASC
        LIMIT 30
        """,
        (cid, cid),
    )
    nxt = cur.fetchall() or []
    print(f"\npróximos até 30 elegíveis para materialize (initial): {len(nxt)} linhas")
    for r in nxt[:10]:
        print(" ", dict(r))
    if len(nxt) > 10:
        print(f"  ... +{len(nxt) - 10}")

    if args.api:
        from services.uazapi import UazapiService

        u = UazapiService()
        cur.execute(
            """
            SELECT i.id, i.apikey, i.name
            FROM campaign_instances ci
            JOIN instances i ON i.id = ci.instance_id
            WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
              AND i.apikey IS NOT NULL
            """,
            (cid,),
        )
        for inst in cur.fetchall() or []:
            tok = (inst.get("apikey") or "").strip()
            if not tok:
                continue
            print(f"\n--- API inst {inst['id']} {inst.get('name')} ---")
            try:
                st = u.get_status(tok)
                print(" get_status:", json.dumps(st, ensure_ascii=False, default=str)[:800])
            except Exception as e:
                print(" get_status erro:", e)
            try:
                folders = u.list_folders(tok) or []
                print(f" list_folders: {len(folders)} pastas (mostrando últimas 5 com info)")
                for f in folders[-5:]:
                    print(
                        " ",
                        f.get("id"),
                        f.get("status"),
                        f.get("log_sucess") or f.get("log_success"),
                        (f.get("info") or "")[:60],
                    )
            except Exception as e:
                print(" list_folders erro:", e)

    conn.close()
    print("\nDica: export UAZAPI_DEBUG=1 no worker para ver JSON completo de create_advanced_campaign.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
