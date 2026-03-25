#!/usr/bin/env python3
"""
Remove instâncias WhatsApp legadas (MegaAPI) do banco após migração 100% Uazapi.

Critério (alinhado ao código): COALESCE(api_provider, 'megaapi') <> 'uazapi'

Efeitos em cascata (ON DELETE CASCADE): campaign_instances, campaign_stage_sends,
uazapi_instance_sends ligados a essas instâncias.

campaign_leads.last_sent_instance_id não tem FK; ao excluir, zera referências para
esses ids (evita ids órfãos na UI).

Uso em produção (recomendado):
  1) Dry-run (padrão — só lista o que seria removido):
       python scripts/delete_legacy_megaapi_instances.py

  2) Aplicar:
       python scripts/delete_legacy_megaapi_instances.py --execute

Variáveis de ambiente: DATABASE_URL ou DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT
(igual aos outros scripts em scripts/).
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        port=os.environ.get("DB_PORT", "5432"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exclui instâncias MegaAPI legadas (mantém apenas api_provider = 'uazapi')."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Efetua UPDATE/DELETE e COMMIT. Sem esta flag, apenas lista (dry-run).",
    )
    args = parser.parse_args()

    conn = get_db_connection()
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT i.id, i.user_id, i.name, i.api_provider, i.status
                FROM instances i
                WHERE COALESCE(i.api_provider, 'megaapi') <> 'uazapi'
                ORDER BY i.id
                """
            )
            rows = cur.fetchall() or []

        if not rows:
            print("Nenhuma instância legada (MegaAPI / não-Uazapi) encontrada. Nada a fazer.")
            conn.commit()
            return 0

        ids = [int(r["id"]) for r in rows]

        print(f"Instâncias legadas encontradas: {len(ids)}")
        print()

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for r in rows:
                ap = r.get("api_provider")
                ap_disp = repr(ap) if ap is not None else "NULL"
                print(
                    f"  id={r['id']}  user_id={r['user_id']}  name={r['name']!r}  "
                    f"api_provider={ap_disp}  status={r.get('status')!r}"
                )

            cur.execute(
                "SELECT COUNT(*) AS n FROM campaign_instances WHERE instance_id = ANY(%s)",
                (ids,),
            )
            n_ci = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT COUNT(*) AS n FROM campaign_stage_sends WHERE instance_id = ANY(%s)",
                (ids,),
            )
            n_css = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT COUNT(*) AS n FROM uazapi_instance_sends WHERE instance_id = ANY(%s)",
                (ids,),
            )
            n_uis = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM campaign_leads
                WHERE last_sent_instance_id IS NOT NULL AND last_sent_instance_id = ANY(%s)
                """,
                (ids,),
            )
            n_cl = int((cur.fetchone() or {}).get("n") or 0)

        print()
        print("Impacto esperado:")
        print(f"  campaign_instances (CASCADE):   {n_ci} linhas removidas com a instância")
        print(f"  campaign_stage_sends (CASCADE): {n_css} linhas removidas com a instância")
        print(f"  uazapi_instance_sends (CASCADE): {n_uis} linhas removidas com a instância")
        print(f"  campaign_leads.last_sent_instance_id: {n_cl} linhas serão atualizadas (NULL)")
        print()

        if not args.execute:
            conn.rollback()
            print("Dry-run: nenhuma alteração feita.")
            print("Execute com --execute para aplicar.")
            return 0

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_leads
                SET last_sent_instance_id = NULL
                WHERE last_sent_instance_id IS NOT NULL AND last_sent_instance_id = ANY(%s)
                """,
                (ids,),
            )
            n_upd = cur.rowcount

            cur.execute(
                """
                DELETE FROM instances
                WHERE COALESCE(api_provider, 'megaapi') <> 'uazapi'
                  AND id = ANY(%s)
                """,
                (ids,),
            )
            n_del = cur.rowcount

        conn.commit()
        print(f"OK: last_sent_instance_id limpo em {n_upd} lead(s); {n_del} instância(s) removida(s).")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"ERRO: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
