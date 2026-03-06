"""
Módulo compartilhado para verificação de limites diários.
Usado por worker_sender e worker_cadence.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432'),
        cursor_factory=RealDictCursor,
    )


def get_user_daily_limit(user_id: int) -> int:
    """
    Obtém o limite diário do plano do usuário (License).
    Retorna 10 (starter), 20 (pro) ou 30 (scale). Default 10.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT license_type FROM licenses WHERE user_id = %s AND status = 'active' AND expires_at > NOW()",
                (user_id,),
            )
            rows = cur.fetchall()
        limit = 10
        for row in rows:
            lt = (row.get('license_type') or '').lower()
            if lt == 'scale':
                limit = max(limit, 30)
            elif lt == 'pro':
                limit = max(limit, 20)
            elif lt == 'starter':
                limit = max(limit, 10)
        return limit
    finally:
        conn.close()


def check_daily_limit(user_id: int, plan_limit: int) -> bool:
    """
    Verifica se o usuário já atingiu o limite diário de disparos.
    Retorna True se PODE enviar, False se atingiu o limite.
    Conta mensagens enviadas hoje (BRT).
    """
    query = """
    SELECT COUNT(cl.id) as count
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s
      AND cl.status = 'sent'
      AND date(cl.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')
          = date(NOW() AT TIME ZONE 'America/Sao_Paulo')
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()
        return row['count'] < plan_limit
    finally:
        conn.close()
