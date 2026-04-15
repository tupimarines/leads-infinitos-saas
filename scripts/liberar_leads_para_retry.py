#!/usr/bin/env python3
"""
Libera leads para retry: marca campaign_stage_sends (initial) como 'failed'
e limpa lead_ids, para que o materialize possa incluir esses leads novamente.

ATENÇÃO: Use apenas se tiver certeza de que quer reenviar. Pode causar duplicatas
se as mensagens já foram entregues mas o sync ainda não atualizou.

Uso: python scripts/liberar_leads_para_retry.py <campaign_id>
     python scripts/liberar_leads_para_retry.py 140 --dry-run  # só mostra o que faria
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    dry_run = "--dry-run" in sys.argv
    campaign_id = None
    for a in sys.argv[1:]:
        if a.isdigit():
            campaign_id = int(a)
            break
    if not campaign_id:
        print("Uso: python scripts/liberar_leads_para_retry.py <campaign_id> [--dry-run]")
        sys.exit(1)

    from app import get_db_connection
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, instance_id, status, uazapi_folder_id,
                          jsonb_array_length(COALESCE(lead_ids, '[]'::jsonb)) as lead_count
                   FROM campaign_stage_sends
                   WHERE campaign_id = %s AND stage = 'initial'
                     AND status IN ('done', 'running', 'partial')
                   ORDER BY created_at DESC""",
                (campaign_id,),
            )
            rows = cur.fetchall()
        if not rows:
            print(f"Campanha {campaign_id}: nenhum chunk done/running/partial para liberar.")
            return
        print(f"Campanha {campaign_id}: {len(rows)} chunk(s) a marcar como failed:")
        for r in rows:
            print(f"  id={r[0]} inst={r[1]} status={r[2]} folder={r[3]} leads={r[4]}")
        if dry_run:
            print("\n[DRY-RUN] Nada alterado. Remova --dry-run para executar.")
            return
        confirm = input("\nConfirma? (s/N): ").strip().lower()
        if confirm != "s":
            print("Cancelado.")
            return
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE campaign_stage_sends
                   SET status = 'failed', lead_ids = '[]'::jsonb, updated_at = NOW()
                   WHERE campaign_id = %s AND stage = 'initial'
                     AND status IN ('done', 'running', 'partial')""",
                (campaign_id,),
            )
            n = cur.rowcount
        conn.commit()
        print(f"✓ {n} chunk(s) marcados como failed. Leads liberados para retry.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
