"""
Módulo compartilhado para verificação de limites diários.
Usado por worker_sender e worker_cadence.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

from utils.config import SUPER_ADMIN_EMAILS
INFINITE_DAILY_SEND_OPTIONS = (10, 20, 30, 40, 50)

# campaign_stage_sends (stage initial): só estes status bloqueiam novo chunk na mesma instância
# (worker_cadence.schedule_next_initial_chunk). failed/done não entram — após pasta órfã (sync → failed),
# a instância volta a poder receber chunk (tech-spec-uazapi-campanhas-n8n-sync-observabilidade, Task 3).
INITIAL_CHUNK_ACTIVE_SEND_STATUSES = ("scheduled", "running", "partial")

# Fonte única de regras de plano.
# validity_days: dias até expiração (a partir da data de aplicação ao usuário).
# starter_trial: 7 dias; demais planos: 365 dias.
PLAN_POLICY = {
    "starter": {
        "instance_limit": 1,
        "monthly_extraction_limit": 1000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
        "validity_days": 365,
    },
    "starter_trial": {
        "instance_limit": 2,
        "monthly_extraction_limit": 210,
        "daily_sends_per_instance_default": 15,
        "infinite_daily_options": (),
        "validity_days": 7,
    },
    "pro": {
        "instance_limit": 2,
        "monthly_extraction_limit": 2000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
        "validity_days": 365,
    },
    "scale": {
        "instance_limit": 4,
        "monthly_extraction_limit": 4000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": (),
        "validity_days": 365,
    },
    "infinite": {
        "instance_limit": 20,
        "monthly_extraction_limit": 10000,
        "daily_sends_per_instance_default": 30,
        "infinite_daily_options": INFINITE_DAILY_SEND_OPTIONS,
        "validity_days": 365,
    },
}

# Compatibilidade temporária para dados legados no banco.
LEGACY_LICENSE_TYPE_FALLBACK = {
    "semestral": "pro",
    "anual": "pro",
}

PLAN_PRIORITY = {"starter": 1, "starter_trial": 1, "pro": 2, "scale": 3, "infinite": 4}


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
    Valor padrão definido por plano em PLAN_POLICY['daily_sends_per_instance_default'].
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


# Limite de chunks por instância/dia removido para evitar travas.
# Antes: 8 chunks/dia. Agora: sem limite (bloqueio só por chunk ativo).
def can_create_campaign_today(instance_id: int) -> bool:
    """Sempre permite criar chunks (limite diário removido)."""
    return True


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
