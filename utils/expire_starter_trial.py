"""
Lógica de expiração de licenças starter_trial.

Usado por scripts/expire_starter_trial.py e rota /cron/expire-starter-trial.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

from services.uazapi import UazapiService


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "devpassword"),
        port=os.environ.get("DB_PORT", "5432"),
        cursor_factory=RealDictCursor,
    )


def expire_starter_trial_licenses(log_fn=None):
    """
    Expira licenças starter_trial vencidas e deleta instâncias Uazapi.

    Args:
        log_fn: função opcional para log (ex: print). Se None, usa print.

    Returns:
        int: quantidade de licenças processadas.
    """
    log = log_fn or print
    conn = get_db_connection()
    uazapi = UazapiService()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.id AS license_id, l.user_id, l.expires_at, u.email
            FROM licenses l
            JOIN users u ON u.id = l.user_id
            WHERE l.license_type = 'starter_trial'
              AND l.status = 'active'
              AND l.expires_at <= NOW()
            """
        )
        expired = cur.fetchall()

    if not expired:
        log("✅ Nenhuma licença starter_trial vencida.")
        conn.close()
        return 0

    processed = 0
    for row in expired:
        license_id = row["license_id"]
        user_id = row["user_id"]
        email = row["email"]
        expires_at = row["expires_at"]

        log(f"🔄 Processando licença {license_id} (user {user_id}, {email}) expirada em {expires_at}")

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, apikey
                FROM instances
                WHERE user_id = %s AND api_provider = 'uazapi'
                """,
                (user_id,),
            )
            instances = cur.fetchall()

        for inst in instances:
            apikey = inst.get("apikey")
            if apikey:
                ok, status = uazapi.delete_instance(apikey)
                if ok:
                    log(f"  ✅ Instância {inst['name']} (id={inst['id']}) deletada na Uazapi")
                else:
                    log(f"  ⚠️ Falha ao deletar instância {inst['name']} na Uazapi (status={status})")
            with conn.cursor() as cur:
                cur.execute("DELETE FROM instances WHERE id = %s", (inst["id"],))
            log(f"  ✅ Instância {inst['id']} removida do banco")

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE licenses SET status = 'expired' WHERE id = %s",
                (license_id,),
            )
        log(f"  ✅ Licença {license_id} marcada como expirada")
        processed += 1

    conn.commit()
    conn.close()
    log(f"✅ Processadas {processed} licença(s) starter_trial.")
    return processed
