#!/usr/bin/env python3
"""
Limpa campaign_stage_sends órfãos do antigo loop de campanhas.

Chunks com status (scheduled, running, partial) que ficaram travados são
marcados como 'done' para desbloquear o botão Continuar em todas as instâncias.

Uso:
  python scripts/cleanup_stale_chunks.py --dry-run   # só mostra o que seria alterado
  python scripts/cleanup_stale_chunks.py             # executa a limpeza
"""
import argparse
import os

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        port=os.environ.get("DB_PORT", "5432"),
    )


def main():
    parser = argparse.ArgumentParser(description="Limpa chunks órfãos (scheduled/running/partial) -> done")
    parser.add_argument("--dry-run", action="store_true", help="Só mostra, não altera")
    parser.add_argument("--campaign", type=int, help="Limitar a uma campanha (opcional)")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            where = "status IN ('scheduled', 'running', 'partial')"
            params = []
            if args.campaign:
                where += " AND campaign_id = %s"
                params.append(args.campaign)

            cur.execute(
                f"""
                SELECT css.id, css.campaign_id, c.name AS campaign_name, css.stage,
                       css.instance_id, i.name AS instance_name, css.uazapi_folder_id,
                       css.status, css.last_sync_at, css.created_at
                FROM campaign_stage_sends css
                JOIN campaigns c ON c.id = css.campaign_id
                JOIN instances i ON i.id = css.instance_id
                WHERE {where}
                ORDER BY css.campaign_id, css.instance_id, css.stage, css.created_at
                """,
                tuple(params) if params else (),
            )
            rows = cur.fetchall() or []

        if not rows:
            print("Nenhum chunk órfão encontrado (scheduled/running/partial).")
            return

        # Agrupar por campanha para resumo
        by_campaign = {}
        for r in rows:
            cid = r["campaign_id"]
            by_campaign.setdefault(cid, {"name": r["campaign_name"], "count": 0, "instances": set()})
            by_campaign[cid]["count"] += 1
            by_campaign[cid]["instances"].add(r["instance_id"])

        print(f"Chunks órfãos a marcar como 'done': {len(rows)}")
        print()
        for cid, info in sorted(by_campaign.items()):
            print(f"  Campanha {cid} ({info['name']}): {info['count']} chunks em {len(info['instances'])} instâncias")
        print()

        if args.dry_run:
            print("--dry-run: nenhuma alteração feita. Execute sem --dry-run para aplicar.")
            return

        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE campaign_stage_sends
                SET status = 'done', updated_at = NOW()
                WHERE {where}
                """,
                tuple(params) if params else (),
            )
            updated = cur.rowcount
        conn.commit()
        print(f"Atualizados {updated} chunks para status='done'.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
