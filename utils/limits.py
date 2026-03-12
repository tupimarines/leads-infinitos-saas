"""
Módulo compartilhado para verificação de limites diários.
Usado por worker_sender e worker_cadence.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'
INFINITE_DAILY_SEND_OPTIONS = (10, 20, 30, 40, 50)

# Fonte única de regras de plano.
PLAN_POLICY = {
    "starter": {
        "instance_limit": 1,
        "monthly_extraction_limit": 1000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
    },
    "pro": {
        "instance_limit": 2,
        "monthly_extraction_limit": 2000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
    },
    "scale": {
        "instance_limit": 4,
        "monthly_extraction_limit": 4000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
    },
    "infinite": {
        "instance_limit": 20,
        "monthly_extraction_limit": 10000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": INFINITE_DAILY_SEND_OPTIONS,
    },
}

# Compatibilidade temporária para dados legados no banco.
LEGACY_LICENSE_TYPE_FALLBACK = {
    "semestral": "pro",
    "anual": "pro",
}

PLAN_PRIORITY = {"starter": 1, "pro": 2, "scale": 3, "infinite": 4}


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432'),
        cursor_factory=RealDictCursor,
    )


def resolve_license_type(license_type: str, allow_legacy_fallback: bool = True):
    normalized = (license_type or "").strip().lower()
    if normalized in PLAN_POLICY:
        return normalized
    if allow_legacy_fallback:
        return LEGACY_LICENSE_TYPE_FALLBACK.get(normalized)
    return None


def get_plan_policy(license_type: str, allow_legacy_fallback: bool = True):
    resolved = resolve_license_type(license_type, allow_legacy_fallback=allow_legacy_fallback)
    if not resolved:
        return PLAN_POLICY["starter"]
    return PLAN_POLICY[resolved]


def _pick_highest_plan(license_rows):
    resolved = []
    for row in license_rows or []:
        r = resolve_license_type(row.get("license_type"))
        if r:
            resolved.append(r)
    if not resolved:
        return "starter"
    return max(resolved, key=lambda plan: PLAN_PRIORITY.get(plan, 0))


def get_instance_daily_limit(user_id: int, instance_id: int = None) -> int:
    return get_user_daily_limit(user_id, instance_id=instance_id)


def get_user_daily_limit(user_id: int, instance_id: int = None) -> int:
    """
    Obtém a cota diária por instância para o usuário.
    Regra padrão: 30 para todos os planos.
    Infinite pode sobrescrever por instância: 10/20/30/40/50.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT license_type FROM licenses WHERE user_id = %s AND status = 'active' AND expires_at > NOW()",
                (user_id,),
            )
            rows = cur.fetchall()
            license_type = _pick_highest_plan(rows)
            plan = PLAN_POLICY[license_type]
            default_limit = int(plan["daily_sends_per_instance_default"])

            if license_type != "infinite":
                return default_limit

            if instance_id is not None:
                cur.execute(
                    """
                    SELECT daily_sends_per_instance
                    FROM instances
                    WHERE id = %s AND user_id = %s
                    LIMIT 1
                    """,
                    (instance_id, user_id),
                )
            else:
                cur.execute(
                    """
                    SELECT daily_sends_per_instance
                    FROM instances
                    WHERE user_id = %s
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (user_id,),
                )
            instance_row = cur.fetchone() or {}
            configured = instance_row.get("daily_sends_per_instance")
            if configured in INFINITE_DAILY_SEND_OPTIONS:
                return int(configured)
            return default_limit
    finally:
        conn.close()


def get_sent_today_count(user_id: int) -> int:
    """
    Conta apenas disparos iniciais enviados hoje (BRT) pelo usuário.
    """
    query = """
    SELECT COUNT(cl.id) as count
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s
      AND cl.status = 'sent'
      AND (
          COALESCE(cl.current_step, 1) = 1
          OR COALESCE(cl.last_sent_stage, '') = 'initial'
      )
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
    Conta apenas disparos iniciais enviados hoje (BRT).
    """
    query = """
    SELECT COUNT(cl.id) as count
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s
      AND cl.status = 'sent'
      AND (
          COALESCE(cl.current_step, 1) = 1
          OR COALESCE(cl.last_sent_stage, '') = 'initial'
      )
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
