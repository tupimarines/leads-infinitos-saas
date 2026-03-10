"""
Módulo compartilhado para verificação de limites diários.
Usado por worker_sender e worker_cadence.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'


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
            if lt == 'infinite':
                limit = max(limit, 50)
            elif lt == 'scale':
                limit = max(limit, 30)
            elif lt == 'pro':
                limit = max(limit, 20)
            elif lt == 'starter':
                limit = max(limit, 10)
        return limit
    finally:
        conn.close()


def get_sent_today_count(user_id: int) -> int:
    """
    Conta mensagens enviadas hoje (BRT) pelo usuário.
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
        return row['count'] if row else 0
    finally:
        conn.close()


def get_sent_today_count_by_instance(instance_id: int) -> int:
    """
    Conta campanhas Uazapi criadas hoje para esta instância.
    Usado para limite 1 campanha por instância por dia.
    Usa tabela uazapi_instance_sends (registrada ao criar campanha).
    """
    query = """
    SELECT COUNT(*) as count
    FROM uazapi_instance_sends
    WHERE instance_id = %s
      AND date(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo')
          = date(NOW() AT TIME ZONE 'America/Sao_Paulo')
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (instance_id,))
            row = cur.fetchone()
        return row['count'] if row else 0
    finally:
        conn.close()


def can_create_campaign_today(instance_id: int) -> bool:
    """
    Retorna True se a instância ainda pode criar campanha hoje (1 por instância por dia).
    Nova campanha liberada apenas após meia-noite BRT.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.email
                FROM instances i
                JOIN users u ON u.id = i.user_id
                WHERE i.id = %s
                LIMIT 1
                """,
                (instance_id,),
            )
            row = cur.fetchone() or {}
        # Superadmin não fica limitado a 1 campanha/instância/dia.
        if (row.get('email') or '').strip().lower() == SUPER_ADMIN_EMAIL:
            return True
    finally:
        conn.close()

    return get_sent_today_count_by_instance(instance_id) < 1


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
