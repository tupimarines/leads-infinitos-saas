import os
import logging
import time
import re
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import pytz

load_dotenv()

_logger_sender = logging.getLogger(__name__)

BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

# Configuração

# Uazapi (superadmin)
try:
    from services.uazapi import UazapiService
    uazapi_service = UazapiService()
except ImportError:
    uazapi_service = None

try:
    from utils.sync_uazapi import sync_campaign_leads_from_uazapi
except ImportError:
    sync_campaign_leads_from_uazapi = None

UAZAPI_USAGE_SYNC_INTERVAL_MINUTES = 10

# Log prefixos para filtro (grep "[ENVIO]" ou "[HORARIO]")
LOG_PREFIX_ENVIO = "[ENVIO]"


def log_envio(msg, flush=True):
    """Log de envio com prefixo [ENVIO] para filtro fácil."""
    print(f"{LOG_PREFIX_ENVIO} {msg}", flush=flush)

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )
    return conn

def get_instance_status_api(instance_name, apikey=None, api_provider=None):
    """
    Verifica o status da instância via API (Uazapi).
    Uazapi: GET /instance/status (header token=apikey)
    Retorna: 'connected', 'connecting', 'disconnected', ou None se erro
    """
    if api_provider == 'uazapi' and uazapi_service and apikey:
        try:
            result = uazapi_service.get_status(apikey)
            if not result:
                return None
            # Uazapi retorna instance.status: connected, connecting, disconnected
            status = result.get('instance', {}).get('status') or result.get('status')
            if status in ('connected', 'connecting', 'disconnected'):
                return status
            return 'disconnected'
        except Exception as e:
            print(f"❌ [Uazapi] Exception checking instance status: {e}")
            return None

    print(f"⚠️ get_instance_status_api: api_provider não suportado ({api_provider})")
    return None

def _update_instance_status_db(instance_name, status):
    """Atualiza status da instância no DB."""
    try:
        with psycopg2.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            database=os.environ.get('DB_NAME', 'leads_infinitos'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', 'devpassword'),
            port=os.environ.get('DB_PORT', '5432')
        ) as conn_fix:
            with conn_fix.cursor() as cur_fix:
                cur_fix.execute(
                    "UPDATE instances SET status = %s, updated_at = NOW() WHERE name = %s",
                    (status, instance_name),
                )
            conn_fix.commit()
        return True
    except Exception as e:
        print(f"⚠️ Failed to update DB status: {e}")
        return False


def verify_and_recover_instance(instance_name, apikey=None, api_provider=None):
    """
    Verifica status da instância e tenta recovery se necessário.
    Uazapi: não tem restart; apenas atualiza status no DB e retorna False se desconectada.

    Retorna: True se instância está conectada, False caso contrário
    """
    if api_provider != 'uazapi':
        print(f"⚠️ verify_and_recover_instance: ignorando instância legada (api_provider={api_provider})")
        return False

    print(f"🔍 Verifying instance {instance_name} status...")

    status = get_instance_status_api(instance_name, apikey=apikey, api_provider=api_provider)

    if status == 'connected':
        print(f"✅ Instance {instance_name} is connected")
        return True

    print(f"⚠️ Instance {instance_name} (Uazapi) not connected (status: {status}). No restart available.")
    _update_instance_status_db(instance_name, status or 'disconnected')
    return False

def format_jid(phone):
    """
    Formats a phone number into a WhatsApp JID.
    Removes non-digits. Adds 55 if missing (assuming BR for now based on context).
    Adds @s.whatsapp.net
    """
    clean_phone = re.sub(r'\D', '', str(phone))
    
    # Basic heuristic for Brazil DDI 55
    if len(clean_phone) <= 11: # DDD + Number (10 or 11 digits)
        clean_phone = '55' + clean_phone
        
    return f"{clean_phone}@s.whatsapp.net"


def jid_to_number(phone_jid):
    """
    Extrai o número do JID (remove @s.whatsapp.net).
    Uazapi usa número no formato 5511999999999.
    """
    if not phone_jid:
        return None
    return str(phone_jid).replace('@s.whatsapp.net', '').strip()


# Limites centralizados de plano/instância
from utils.limits import get_user_daily_limit

def check_instance_daily_limit(user_id, instance_name, instance_id=None):
    """
    Verifica se uma instância específica já atingiu o limite diário de disparos.
    Conta apenas disparos iniciais enviados por esta instância hoje.
    Retorna True se PODE enviar, False se atingiu o limite.
    """
    instance_limit = get_user_daily_limit(user_id, instance_id=instance_id)
    query = """
    SELECT COUNT(cl.id) as count 
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s 
    AND cl.status = 'sent' 
    AND cl.sent_by_instance = %s
    AND (
        COALESCE(cl.current_step, 1) = 1
        OR COALESCE(cl.last_sent_stage, '') = 'initial'
    )
    AND date(cl.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo') = date(NOW() AT TIME ZONE 'America/Sao_Paulo')
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (user_id, instance_name))
            row = cur.fetchone()
        
        current_sent = row['count']
        return current_sent < instance_limit
    finally:
        conn.close()

def check_phone_on_whatsapp(instance_name, phone_jid, apikey=None, api_provider=None, retry_count=0):
    """
    Verifica se o número existe no WhatsApp (Uazapi).
    Uazapi: POST /chat/check com {numbers: [number]} (number sem @s.whatsapp.net)

    Retorna tupla: (exists, correct_jid, is_instance_error)
    - exists: True se número existe no WhatsApp
    - correct_jid: JID corrigido pela API
    - is_instance_error: True se foi erro de instância (não marcar número como inválido)
    """
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK] Checked existence for {phone_jid}: True")
        time.sleep(0.1)
        return True, phone_jid, False

    if api_provider != 'uazapi' or not uazapi_service or not apikey:
        print(f"⚠️ check_phone_on_whatsapp: apenas Uazapi é suportado (api_provider={api_provider})")
        return False, None, True

    number = jid_to_number(phone_jid)
    if not number:
        return False, None, False
    try:
        result = uazapi_service.check_phone(apikey, [number])
        if result is None:
            return False, None, True  # API error -> instance error
        # Resposta: array com query, jid, isInWhatsapp (Uazapi OpenAPI)
        items = result if isinstance(result, list) else [result]
        if not items:
            return False, None, True
        item = items[0] if isinstance(items[0], dict) else {}
        exists = item.get('isInWhatsapp', False)
        correct_jid = item.get('jid') or format_jid(item.get('query', number)) if exists else phone_jid
        return bool(exists), correct_jid, False
    except Exception as e:
        print(f"❌ [Uazapi] Exception checking phone: {e}")
        return False, None, True

def send_message(instance_name, phone_jid, message, apikey=None, api_provider=None):
    """
    Envia mensagem (Uazapi: POST /send/text, number sem @s.whatsapp.net).
    """
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK] Sent message to {phone_jid}: {message[:20]}...")
        time.sleep(0.5)
        return True, {"key": "mocked_key"}

    if api_provider != 'uazapi' or not uazapi_service or not apikey:
        msg = f"Apenas Uazapi é suportado (api_provider={api_provider})"
        log_envio(msg)
        return False, msg

    number = jid_to_number(phone_jid)
    if not number:
        return False, "Invalid phone number"
    try:
        result = uazapi_service.send_text(apikey, number, message)
        if result is not None:
            log_envio("Uazapi OK")
            return True, result
        return False, "Uazapi send_text returned None"
    except Exception as e:
        error_msg = str(e)
        log_envio(f"Uazapi FALHA {error_msg}")
        return False, error_msg


def _sync_uazapi_usage(conn):
    """
    Reconcilia campanhas Uazapi ativas para manter consumo diário consistente
    entre estado local e API (Task 18).
    """
    if not uazapi_service or not sync_campaign_leads_from_uazapi:
        return

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id AS campaign_id, c.uazapi_folder_id, i.apikey
            FROM campaigns c
            JOIN campaign_instances ci ON ci.campaign_id = c.id
            JOIN instances i ON i.id = ci.instance_id
            JOIN campaign_stage_sends css ON css.campaign_id = c.id
            WHERE c.status IN ('pending', 'running', 'completed')
              AND c.use_uazapi_sender = TRUE
              AND c.uazapi_folder_id IS NOT NULL
              AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
              AND i.apikey IS NOT NULL
              AND css.status IN ('scheduled', 'running', 'partial')
              AND css.uazapi_folder_id IS NOT NULL
            ORDER BY c.id ASC
            """
        )
        rows = cur.fetchall() or []

    seen = set()
    for row in rows:
        campaign_id = row["campaign_id"]
        if campaign_id in seen:
            continue
        seen.add(campaign_id)
        try:
            sync_campaign_leads_from_uazapi(
                conn,
                campaign_id,
                row["apikey"],
                row.get("uazapi_folder_id"),
                uazapi_service,
            )
        except Exception as e:
            print(f"⚠️ [Sender Sync] Campaign {campaign_id}: falha no sync de uso Uazapi: {e}")


def process_campaigns():
    """
    Loop do worker: sincroniza uso/leads Uazapi periodicamente (envio em massa é pela API Uazapi).
    """
    _logger_sender.debug("Sender worker loop iniciado.")
    last_uazapi_usage_sync_at = None
    
    while True:
        try:
            conn = get_db_connection()
            now_sync = datetime.now(BRAZIL_TZ)
            should_sync_uazapi_usage = (
                last_uazapi_usage_sync_at is None
                or (now_sync - last_uazapi_usage_sync_at).total_seconds() >= UAZAPI_USAGE_SYNC_INTERVAL_MINUTES * 60
            )
            if should_sync_uazapi_usage:
                _sync_uazapi_usage(conn)
                last_uazapi_usage_sync_at = now_sync

            conn.close()

            # Envio em massa legado (MegaAPI / worker local) foi descontinuado; campanhas usam Uazapi.
            time.sleep(60)

        except Exception as e:
            print(f"Error in sender loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    process_campaigns()
