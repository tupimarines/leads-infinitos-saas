"""
Worker Cadence — Processes multi-step campaign cadence follow-ups with Intelligent Checks.

Runs as a separate process alongside worker_sender.py.
For each cadence-enabled campaign:
  1. Finds leads ready for the next step.
  2. DECISION MATRIX: Checks Chatwoot Labels & Status before sending.
  3. Sends the step's message via Mega API.
  4. POST-SEND MONITORING: Puts lead in 'monitoring' state for 5 mins to check for immediate replies.
  5. Finally snoozes or stops based on the outcome.
"""

import os
import time
import json
import random
import requests
import base64
import re
from datetime import datetime, date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import pytz

load_dotenv()

# Config
BUSINESS_HOUR_START = 8
BUSINESS_HOUR_END = 20
BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')

MEGA_API_URL = os.environ.get('MEGA_API_URL', 'https://ruker.megaapi.com.br')
MEGA_API_TOKEN = os.environ.get('MEGA_API_TOKEN')

# Uazapi (prioridade sobre MegaAPI)
try:
    from services.uazapi import UazapiService
    uazapi_service = UazapiService()
except ImportError:
    uazapi_service = None

# Limites compartilhados
from utils.limits import check_daily_limit, get_user_daily_limit
from utils.sync_uazapi import (
    sync_campaign_leads_from_uazapi,
    get_uazapi_campaign_counts,
    is_initial_campaign_finished,
    fetch_all_phones_by_status,
    normalize_phone_for_match,
)

# Super Admin (gate para mídia Uazapi)
SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'

# Chatwoot Config
CHATWOOT_API_URL = os.environ.get('CHATWOOT_API_URL', 'https://chatwoot.wbtech.dev')
CHATWOOT_ACCESS_TOKEN = os.environ.get('CHATWOOT_ACCESS_TOKEN')
CHATWOOT_ACCOUNT_ID = os.environ.get('CHATWOOT_ACCOUNT_ID', '2')

CADENCE_POLL_INTERVAL = 60  # seconds between each poll cycle
SAFETY_BUFFER_MINUTES = 5

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD'),
        port=os.environ.get('DB_PORT', '5432'),
        cursor_factory=RealDictCursor
    )

def is_business_hours():
    now_brazil = datetime.now(BRAZIL_TZ)
    return BUSINESS_HOUR_START <= now_brazil.hour < BUSINESS_HOUR_END

def format_jid(phone):
    """Formats a phone number into a WhatsApp JID."""
    clean = re.sub(r'\D', '', str(phone))
    if len(clean) <= 11 and not clean.startswith('55'):
        clean = '55' + clean
    return clean + '@s.whatsapp.net'


def _is_media_path_safe(media_path, user_id):
    """
    Valida que media_path está sob storage/{user_id}/ (segurança multi-tenant).
    """
    if not media_path or not user_id:
        return False
    if '..' in media_path:
        return False
    try:
        real_path = os.path.abspath(media_path)
        user_storage = os.path.abspath(os.path.join('storage', str(user_id)))
        return real_path.startswith(user_storage)
    except Exception:
        return False


# --- CHATWOOT HELPERS ---

def get_chatwoot_conversation_details(conversation_id):
    """
    Fetches conversation details including labels, status, and messages.
    Returns dict or None.
    """
    if not conversation_id:
        return None
        
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN, "Content-Type": "application/json"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"  ❌ [Chatwoot] Details Error: {e}")
        return None

def toggle_chatwoot_status(conversation_id, status, snoozed_until=None):
    """
    Toggles conversation status ('snoozed', 'open', 'resolved').
    If status is 'snoozed' and snoozed_until is provided, includes the timestamp.
    """
    if not conversation_id: return False
    
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN, "Content-Type": "application/json"}
    payload = {"status": status}
    
    if status == 'snoozed' and snoozed_until:
        # Chatwoot expects Unix timestamp for snoozed_until
        if hasattr(snoozed_until, 'timestamp'):
            payload["snoozed_until"] = int(snoozed_until.timestamp())
        else:
            payload["snoozed_until"] = int(snoozed_until)
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            print(f"  ✅ Chatwoot status set to '{status}' for conv {conversation_id}")
        return resp.status_code == 200
    except Exception as e:
        print(f"  ❌ Chatwoot toggle error: {e}")
        return False

def add_chatwoot_labels(conversation_id, labels):
    """
    Adds labels to a conversation.
    """
    if not conversation_id: return False
    
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/labels"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN, "Content-Type": "application/json"}
    payload = {"labels": labels}
    
    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
        return True
    except:
        return False

# --- CHATWOOT DISCOVERY ---

def get_chatwoot_conversation_messages(conversation_id):
    """Fetches messages for a conversation."""
    if not conversation_id: return []
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            payload = resp.json()
            return payload.get('payload', [])
    except Exception:
        pass
    return []

def discover_chatwoot_conversation(phone, name=None):
    """
    Discovers the Chatwoot conversation ID for a lead.
    Searches by phone number (multiple formats) and name as fallbacks.
    Returns conversation_id or None.
    """
    if not CHATWOOT_ACCESS_TOKEN:
        return None
    
    headers = {
        "api_access_token": CHATWOOT_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    
    clean_phone = re.sub(r'\D', '', str(phone or ''))
    if not clean_phone and not name:
        return None
    
    # Build search strategies (ordered by specificity)
    strategies = []
    if clean_phone:
        strategies.append(('Phone+', f'+{clean_phone}'))
        strategies.append(('PhoneRaw', clean_phone))
        strategies.append(('JID', f'{clean_phone}@s.whatsapp.net'))
        if len(clean_phone) >= 9:
            strategies.append(('Last9', clean_phone[-9:]))
        if len(clean_phone) >= 8:
            strategies.append(('Last8', clean_phone[-8:]))
    if name and name.strip() and name.strip() != '.':
        strategies.append(('Name', name.strip()))
    
    contact_id = None
    matched_via = None
    
    for label, query_val in strategies:
        if contact_id:
            break
        try:
            search_url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/search"
            resp = requests.get(search_url, params={'q': query_val}, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('payload') and len(data['payload']) > 0:
                    contact_id = data['payload'][0]['id']
                    matched_via = label
        except Exception as e:
            pass  # Silent, will try next strategy
    
    if not contact_id:
        return None
    
    try:
        conv_url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/{contact_id}/conversations"
        resp = requests.get(conv_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            conv_data = resp.json()
            if conv_data.get('payload') and len(conv_data['payload']) > 0:
                conv_id = conv_data['payload'][0]['id']
                print(f"  🔗 Chatwoot: Found conv {conv_id} (via {matched_via}) for contact {contact_id}")
                return conv_id
    except Exception as e:
        print(f"  ⚠️ Chatwoot conv fetch error: {e}")
    
    return None


# --- MEGA API HELPERS ---

def send_text_message(instance_name, phone_jid, message):
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK-CADENCE] Text to {phone_jid}: {message[:40]}...")
        time.sleep(0.5)
        return True

    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/text"
    headers = {"Authorization": MEGA_API_TOKEN, "Content-Type": "application/json"}
    payload = {"messageData": {"to": phone_jid, "text": message, "linkPreview": False}}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"  ❌ Text exception: {e}")
        return False

def send_media_message(instance_name, phone_jid, media_path, media_type, caption=""):
    if os.environ.get('MOCK_SENDER'): return True
    if not os.path.exists(media_path): return False

    with open(media_path, 'rb') as f:
        file_data = base64.b64encode(f.read()).decode('utf-8')

    ext = os.path.splitext(media_path)[1].lower()
    mime_map = {'.jpg': 'image/jpeg', '.png': 'image/png', '.mp4': 'video/mp4'}
    mime = mime_map.get(ext, 'application/octet-stream')
    
    endpoint_type = 'imageMessage' if media_type == 'image' else 'videoMessage'
    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/{endpoint_type}"
    headers = {"Authorization": MEGA_API_TOKEN, "Content-Type": "application/json"}
    payload = {
        "messageData": {
            "to": phone_jid,
            "media": f"data:{mime};base64,{file_data}",
            "caption": caption,
            "fileName": os.path.basename(media_path)
        }
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
    except:
        return False

def get_campaign_instance(campaign_id, conn):
    """Retorna instância para a campanha. Prioriza Uazapi sobre MegaAPI."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.name, i.apikey, COALESCE(i.api_provider, 'megaapi') as api_provider
            FROM campaign_instances ci
            JOIN instances i ON ci.instance_id = i.id
            WHERE ci.campaign_id = %s AND i.status = 'connected'
            ORDER BY CASE WHEN COALESCE(i.api_provider, 'megaapi') = 'uazapi' THEN 0 ELSE 1 END
            LIMIT 1
        """, (campaign_id,))
        row = cur.fetchone()
        return dict(row) if row else None

# --- MAIN LOGIC ---

def process_cadence():
    print("🔄 Starting Intelligent Cadence Worker...")

    while True:
        try:
            conn = get_db_connection()

            # --- PART A: SAFETY BUFFER CHECK (Monitoring Phase) ---
            check_monitoring_leads(conn)

            # 1. Find active cadence campaigns (para rollover e envio)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.id, c.name, c.user_id, c.cadence_config, c.send_hour_start, c.send_saturday, c.send_sunday,
                           c.use_uazapi_sender, c.uazapi_folder_id
                    FROM campaigns c
                    WHERE c.enable_cadence = TRUE
                      AND c.status IN ('running', 'pending', 'completed')
                      AND (c.scheduled_start IS NULL OR c.scheduled_start <= NOW())
                """)
                campaigns = cur.fetchall()

            if not campaigns:
                conn.close()
                time.sleep(CADENCE_POLL_INTERVAL)
                continue

            for campaign in campaigns:
                # Part B.0: Rollover diário (leads em Inicial → Follow-up 1) — roda mesmo fora do horário comercial
                process_rollover(campaign, conn)
                # Part B.0b: Rollover FU1→FU2 e FU2→Despedida (cria campanhas Uazapi agendadas)
                process_rollover_fu_next(campaign, conn, from_step=2, to_step=3, step_label="Follow-up 2")
                process_rollover_fu_next(campaign, conn, from_step=3, to_step=4, step_label="Despedida")
                # Part B.1 e B.2: apenas em horário comercial
                if is_business_hours():
                    process_campaign_sends(campaign, conn)
                    bootstrap_pending_leads(campaign, conn)
                else:
                    now_brazil = datetime.now(BRAZIL_TZ)
                    if campaign == campaigns[0]:  # log uma vez por ciclo
                        print(f"⏰ [Cadence] Off hours ({now_brazil.strftime('%H:%M')} BRT). Rollover ok, envio pausado.")

            conn.close()
            time.sleep(CADENCE_POLL_INTERVAL)

        except Exception as e:
            print(f"❌ [Cadence] Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)

def check_monitoring_leads(conn):
    """
    SAFETY BUFFER Logic:
    Checks leads in 'monitoring' status.
    If 5 mins passed since send:
      - Check Chatwoot for replies/unread.
      - If reply: ABORT SNOOZE (Set 'stopped').
      - If safe: SNOOZE in Chatwoot + Schedule Next Step.
    """
    buffer_time = datetime.now(BRAZIL_TZ) - timedelta(minutes=SAFETY_BUFFER_MINUTES)
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT cl.id, cl.chatwoot_conversation_id, cl.campaign_id, cl.current_step, 
                   cl.last_message_sent_at, cl.phone, cl.name
            FROM campaign_leads cl
            WHERE cl.cadence_status = 'monitoring'
              AND cl.last_message_sent_at <= %s
        """, (buffer_time,))
        monitoring_leads = cur.fetchall()

    if not monitoring_leads:
        return

    print(f"🛡️ [Safety Buffer] Checking {len(monitoring_leads)} monitored leads...")

    for lead in monitoring_leads:
        lead_id = lead['id']
        conv_id = lead['chatwoot_conversation_id']
        
        # If no Chatwoot conversation, try to discover it
        if not conv_id:
            conv_id = discover_chatwoot_conversation(lead['phone'], lead.get('name'))
            if conv_id:
                with conn.cursor() as cur:
                    cur.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (conv_id, lead_id))
                conn.commit()
        
        # 1. Check Chatwoot Context
        cw_data = get_chatwoot_conversation_details(conv_id)
        
        abort_snooze = False
        abort_reason = ""

        if cw_data:
            unread = cw_data.get('unread_count', 0)
            status = cw_data.get('status')
            if unread > 0:
                abort_snooze = True
                abort_reason = f"Unread count is {unread}"
            else:
                messages = get_chatwoot_conversation_messages(conv_id)
                if messages:
                    # Check last actual message (0=incoming, 1=outgoing)
                    for msg in reversed(messages):
                        mtype = msg.get('message_type')
                        if mtype in [0, 1]:
                            if mtype == 0:
                                abort_snooze = True
                                abort_reason = "Last message is from Contact"
                            break
        else:
            if conv_id:
                print(f"  ⚠️ Lead #{lead_id}: Could not fetch Chatwoot details. Proceeding with snooze.")

        with conn.cursor() as cur:
            if abort_snooze:
                cur.execute("""
                    UPDATE campaign_leads SET cadence_status = 'stopped', log = %s WHERE id = %s
                """, (f"Safety Buffer Abort: {abort_reason}", lead_id))
                conn.commit()
                print(f"  🛑 Lead #{lead_id}: Snooze ABORTED. {abort_reason}")
            else:
                # SAFE: Execute Snooze + Schedule Next Step
                cur.execute("""
                    SELECT delay_days FROM campaign_steps 
                    WHERE campaign_id = %s AND step_number = %s
                """, (lead['campaign_id'], lead['current_step'] + 1))
                next_step_row = cur.fetchone()
                
                if next_step_row:
                    delay = next_step_row['delay_days']
                    delay = 1 if delay is None else int(delay)
                    now_br = datetime.now(BRAZIL_TZ)
                    snooze_until = now_br + timedelta(minutes=2) if delay <= 0 else now_br + timedelta(days=delay)
                    new_status = 'snoozed'
                    
                    cur.execute("""
                        UPDATE campaign_leads 
                        SET cadence_status = %s, snooze_until = %s 
                        WHERE id = %s
                    """, (new_status, snooze_until, lead_id))
                    
                    # Execute Chatwoot Snooze with timestamp
                    toggle_chatwoot_status(conv_id, 'snoozed', snoozed_until=snooze_until)
                    
                    print(f"  💤 Lead #{lead_id}: Safety Check passed. Snoozed until {snooze_until.strftime('%d/%m %H:%M')}.")
                else:
                    cur.execute("UPDATE campaign_leads SET cadence_status = 'completed' WHERE id = %s", (lead_id,))
                    toggle_chatwoot_status(conv_id, 'resolved')
                    print(f"  🏁 Lead #{lead_id}: Cadence completed.")
            conn.commit()


def _parse_rollover_time(rollover_str):
    """Parse 'HH:MM' ou 'H:MM' para (hour, minute). Default (23, 0)."""
    if not rollover_str or not isinstance(rollover_str, str):
        return 23, 0
    parts = str(rollover_str).strip().split(':')
    if len(parts) >= 2:
        try:
            return int(parts[0]) % 24, int(parts[1]) % 60
        except ValueError:
            pass
    return 23, 0


def _next_send_datetime(from_dt, delay_days, send_hour_start, send_saturday, send_sunday):
    """
    Calcula próximo dia útil no horário send_hour_start.
    Pula sábado/domingo se send_saturday/send_sunday forem False.
    delay_days=0: envia no próximo ciclo (~2 min) para testes.
    """
    if delay_days <= 0:
        return from_dt + timedelta(minutes=2)
    send_sat = bool(send_saturday)
    send_sun = bool(send_sunday)
    d = from_dt.date()
    remaining = delay_days
    for _ in range(30):
        wd = d.weekday()
        if wd == 5 and not send_sat:
            d += timedelta(days=1)
            continue
        if wd == 6 and not send_sun:
            d += timedelta(days=1)
            continue
        if remaining <= 0:
            break
        remaining -= 1
        d += timedelta(days=1)
    target = datetime(d.year, d.month, d.day, send_hour_start or 8, 0, 0, tzinfo=BRAZIL_TZ)
    return target


def process_rollover(campaign, conn):
    """
    Rollover diário: às rollover_time, leads em Inicial (current_step=1) que constam
    em list_messages(Sent) da API → mover para Follow-up 1 e criar campanha Uazapi agendada.
    API é fonte de verdade (não depende de campaign_leads.status).
    Só processa instâncias Uazapi.
    """
    cid = campaign['id']
    cadence_config = campaign.get('cadence_config') or {}
    if isinstance(cadence_config, str):
        try:
            cadence_config = json.loads(cadence_config) if cadence_config else {}
        except json.JSONDecodeError:
            cadence_config = {}
    rollover_str = cadence_config.get('rollover_time', '23:00')
    rollover_test_mode = bool(cadence_config.get('rollover_test_mode', False))
    rollover_h, rollover_m = _parse_rollover_time(rollover_str)

    now_brazil = datetime.now(BRAZIL_TZ)
    # Modo teste OU 00:00: roda em todo ciclo. Caso contrário: só quando hora >= rollover_time
    if not rollover_test_mode and rollover_str != '00:00':
        now_minutes = now_brazil.hour * 60 + now_brazil.minute
        rollover_minutes = rollover_h * 60 + rollover_m
        if now_minutes < rollover_minutes:
            return

    instance = get_campaign_instance(cid, conn)
    if not instance:
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': sem instância vinculada, pulando.")
        return
    if instance.get('api_provider') != 'uazapi':
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': instância {instance.get('api_provider', '?')}, requer Uazapi.")
        return
    if not uazapi_service:
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': UazapiService indisponível.")
        return

    # Sync Uazapi → DB antes do rollover (marca envios para Kanban/stats; rollover não depende mais dele)
    if campaign.get('use_uazapi_sender') and campaign.get('uazapi_folder_id') and instance.get('apikey'):
        try:
            sync_result = sync_campaign_leads_from_uazapi(
                conn, cid, instance['apikey'], campaign['uazapi_folder_id'], uazapi_service
            )
            if sync_result.get('updated_sent') or sync_result.get('updated_failed'):
                print(f"  🔄 [Rollover] Campaign '{campaign['name']}': sync Uazapi → {sync_result}")
            elif sync_result.get('sent', 0) > 0 and sync_result.get('updated_sent', 0) == 0:
                print(f"  ⚠️ [Rollover] Campaign '{campaign['name']}': API retornou {sync_result.get('sent')} Sent mas 0 atualizados no DB (verificar match de telefone)")
        except Exception as e:
            print(f"  ⚠️ [Rollover] Campaign '{campaign['name']}': sync Uazapi falhou: {e}")

    # Verificar se campanha inicial terminou (Scheduled=0) e obter sent_phones da API
    if not campaign.get('uazapi_folder_id') or not instance.get('apikey'):
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': sem uazapi_folder_id ou apikey, pulando.")
        return
    counts = get_uazapi_campaign_counts(uazapi_service, instance['apikey'], campaign['uazapi_folder_id'])
    if not is_initial_campaign_finished(counts):
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': campanha inicial ainda enviando (scheduled={counts.get('scheduled', 0)}). Aguardando.")
        return

    # API como fonte de verdade: obter sent_phones de list_messages(Sent)
    sent_phones = fetch_all_phones_by_status(
        uazapi_service, instance['apikey'], campaign['uazapi_folder_id'], "Sent"
    )
    sent_normalized = set()
    for ph in sent_phones:
        sent_normalized |= normalize_phone_for_match(ph)

    # Buscar leads em Inicial (current_step=1), sem filtro de status. Exclui converted/lost.
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT cl.id, cl.phone, cl.name, cl.whatsapp_link, cl.sent_at
            FROM campaign_leads cl
            WHERE cl.campaign_id = %s
              AND cl.current_step = 1
              AND (cl.cadence_status IS NULL OR cl.cadence_status IN ('snoozed', 'pending'))
              AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
            LIMIT 100
        """, (cid,))
        initial_leads = cur.fetchall()

    # Match por normalização: lead elegível se phone/whatsapp_link intersecta sent_normalized
    rollover_leads = []
    for lead in initial_leads:
        lead_variants = normalize_phone_for_match(lead.get('phone')) | normalize_phone_for_match(
            lead.get('whatsapp_link')
        )
        if lead_variants and (lead_variants & sent_normalized):
            rollover_leads.append(lead)

    # Modo teste + delay: só rollover se MIN(sent_at) >= N minutos entre elegíveis
    rollover_test_delay_minutes = int(cadence_config.get('rollover_test_delay_minutes', 5))
    if rollover_test_mode and rollover_test_delay_minutes > 0 and rollover_leads:
        sent_ats = [l.get('sent_at') for l in rollover_leads if l.get('sent_at')]
        if sent_ats:
            min_sent = min(sent_ats)
            if getattr(min_sent, 'tzinfo', None) is None:
                min_sent = BRAZIL_TZ.localize(min_sent)
            elapsed_min = (now_brazil - min_sent).total_seconds() / 60
            if elapsed_min < rollover_test_delay_minutes:
                return  # Aguardar delay

    if not rollover_leads:
        if sent_phones:
            print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': API retornou {len(sent_phones)} Sent mas 0 leads em Inicial deram match.")
        else:
            print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': 0 Sent na API, nenhum lead elegível.")
        return

    print(f"  🔄 [Rollover] Campaign '{campaign['name']}': {len(rollover_leads)} leads elegíveis, criando campanha FU1...")

    # Step 2 config (incl. media_path para Uazapi type image/video)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT message_template, delay_days, media_path, media_type FROM campaign_steps WHERE campaign_id = %s AND step_number = 2 LIMIT 1",
            (cid,),
        )
        step2 = cur.fetchone()
    if not step2:
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': step 2 (Follow-up 1) não configurado.")
        return

    send_hour = int(campaign.get('send_hour_start') or 8)
    send_sat = bool(campaign.get('send_saturday'))
    send_sun = bool(campaign.get('send_sunday'))
    delay_days = step2.get('delay_days')
    delay_days = 1 if delay_days is None else int(delay_days)

    target_dt = _next_send_datetime(now_brazil, delay_days, send_hour, send_sat, send_sun)
    scheduled_ts = int(target_dt.timestamp() * 1000)  # Uazapi espera ms

    # Gate superadmin para mídia
    user_id = campaign.get('user_id')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    is_sa = row and row.get('email') == SUPER_ADMIN_EMAIL

    # Mídia step 2 (superadmin only)
    media_file_data = None
    media_type = 'image'
    if is_sa and step2.get('media_path'):
        mp = step2['media_path']
        if mp and _is_media_path_safe(mp, user_id) and os.path.exists(mp):
            try:
                with open(mp, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                ext = os.path.splitext(mp)[1].lower()
                mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.mp4': 'video/mp4', '.webm': 'video/webm'}
                mime = mime_map.get(ext, 'application/octet-stream')
                media_file_data = f"data:{mime};base64,{b64}"
                media_type = step2.get('media_type') or 'image'
            except Exception as e:
                print(f"  ⚠️ [Rollover] Erro ao ler mídia step 2: {e}")

    # Re-query imediatamente antes de criar campanha (excluir leads movidos para Convertido/Perdido)
    lead_ids = [l['id'] for l in rollover_leads]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT cl.id, cl.phone, cl.name, cl.whatsapp_link
            FROM campaign_leads cl
            WHERE cl.id = ANY(%s)
              AND cl.campaign_id = %s
              AND cl.current_step = 1
              AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
        """, (lead_ids, cid))
        rollover_leads = cur.fetchall()

    if not rollover_leads:
        return

    raw_tpl = step2.get('message_template') or '[]'
    try:
        parsed = json.loads(raw_tpl)
        msg_text = random.choice(parsed) if isinstance(parsed, list) else str(parsed)
    except Exception:
        msg_text = str(raw_tpl)

    messages = []
    for lead in rollover_leads:
        phone = lead.get('phone') or ''
        if not phone and lead.get('whatsapp_link'):
            match = re.search(r'(\d{10,})', str(lead['whatsapp_link']))
            if match:
                phone = match.group(1)
        if not phone:
            continue
        clean = re.sub(r'\D', '', str(phone))
        if len(clean) <= 11 and not clean.startswith('55'):
            clean = '55' + clean
        name = lead.get('name') or 'Visitante'
        text = msg_text.replace('{{nome}}', name).replace('{{name}}', name).replace('{nome}', name).replace('{name}', name)
        if media_file_data:
            messages.append({'number': clean, 'type': media_type, 'file': media_file_data, 'text': text})
        else:
            messages.append({'number': clean, 'type': 'text', 'text': text})

    if not messages:
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': nenhum telefone válido nos leads.")
        return

    token = instance.get('apikey')
    if not token:
        print(f"  ⏭️ [Rollover] Campaign '{campaign['name']}': instância sem apikey.")
        return

    result = uazapi_service.create_advanced_campaign(
        token=token,
        delay_min_sec=60,
        delay_max_sec=120,
        messages=messages,
        info=f"Rollover FU1 c{cid}",
        scheduled_for=scheduled_ts,
    )

    if not result:
        print(f"  ❌ [Rollover] Campaign '{campaign['name']}': Uazapi create_advanced_campaign falhou. Leads NÃO movidos.")
        return

    folder_id = result.get('folder_id') or result.get('folderId')
    # Sucesso: mover leads para Follow-up 1 (current_step=2, cadence_status=snoozed, status=sent para consistência)
    lead_ids = [l['id'] for l in rollover_leads]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaign_leads
            SET current_step = 2, cadence_status = 'snoozed', snooze_until = %s, status = 'sent'
            WHERE id = ANY(%s)
            """,
            (target_dt, lead_ids),
        )
        if folder_id:
            cur.execute(
                """
                UPDATE campaigns SET cadence_config = COALESCE(cadence_config, '{}')::jsonb || %s::jsonb
                WHERE id = %s
                """,
                (json.dumps({'rollover_fu1_folder_id': str(folder_id)}), cid),
            )
    conn.commit()
    print(f"  🔄 [Rollover] Campaign '{campaign['name']}': {len(lead_ids)} leads Inicial → Follow-up 1, agendado {target_dt.strftime('%d/%m %H:%M')} BRT")


def process_rollover_fu_next(campaign, conn, from_step, to_step, step_label):
    """
    Rollover FU1→FU2 ou FU2→Despedida: leads em from_step com snooze_until<=NOW()
    → criar campanha Uazapi para to_step e mover leads.
    """
    cid = campaign['id']
    instance = get_campaign_instance(cid, conn)
    if not instance or instance.get('api_provider') != 'uazapi' or not uazapi_service:
        return

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT cl.id, cl.phone, cl.name, cl.whatsapp_link
            FROM campaign_leads cl
            WHERE cl.campaign_id = %s
              AND cl.current_step = %s
              AND cl.status = 'sent'
              AND cl.cadence_status = 'snoozed'
              AND cl.snooze_until <= NOW()
              AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
            LIMIT 100
        """, (cid, from_step))
        rollover_leads = cur.fetchall()

    if not rollover_leads:
        return

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT message_template, delay_days, media_path, media_type FROM campaign_steps WHERE campaign_id = %s AND step_number = %s LIMIT 1",
            (cid, to_step),
        )
        step_cfg = cur.fetchone()
    if not step_cfg:
        print(f"  ⏭️ [Rollover {step_label}] Campaign '{campaign['name']}': step {to_step} não configurado.")
        return

    send_hour = int(campaign.get('send_hour_start') or 8)
    send_sat = bool(campaign.get('send_saturday'))
    send_sun = bool(campaign.get('send_sunday'))
    delay_days = step_cfg.get('delay_days')
    delay_days = 1 if delay_days is None else int(delay_days)
    now_brazil = datetime.now(BRAZIL_TZ)
    target_dt = _next_send_datetime(now_brazil, delay_days, send_hour, send_sat, send_sun)
    scheduled_ts = int(target_dt.timestamp() * 1000)

    # Gate superadmin para mídia
    user_id = campaign.get('user_id')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    is_sa = row and row.get('email') == SUPER_ADMIN_EMAIL

    # Mídia do step (superadmin only)
    media_file_data = None
    media_type = 'image'
    if is_sa and step_cfg.get('media_path'):
        mp = step_cfg['media_path']
        if mp and _is_media_path_safe(mp, user_id) and os.path.exists(mp):
            try:
                with open(mp, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                ext = os.path.splitext(mp)[1].lower()
                mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.mp4': 'video/mp4', '.webm': 'video/webm'}
                mime = mime_map.get(ext, 'application/octet-stream')
                media_file_data = f"data:{mime};base64,{b64}"
                media_type = step_cfg.get('media_type') or 'image'
            except Exception as e:
                print(f"  ⚠️ [Rollover {step_label}] Erro ao ler mídia: {e}")

    # Re-query imediatamente antes de criar campanha (excluir leads movidos para Convertido/Perdido)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT cl.id, cl.phone, cl.name, cl.whatsapp_link
            FROM campaign_leads cl
            WHERE cl.campaign_id = %s
              AND cl.current_step = %s
              AND cl.status = 'sent'
              AND cl.cadence_status = 'snoozed'
              AND cl.snooze_until <= NOW()
              AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
            LIMIT 100
        """, (cid, from_step))
        rollover_leads = cur.fetchall()

    if not rollover_leads:
        return

    raw_tpl = step_cfg.get('message_template') or '[]'
    try:
        parsed = json.loads(raw_tpl)
        msg_text = random.choice(parsed) if isinstance(parsed, list) else str(parsed)
    except Exception:
        msg_text = str(raw_tpl)

    messages = []
    for lead in rollover_leads:
        phone = lead.get('phone') or ''
        if not phone and lead.get('whatsapp_link'):
            match = re.search(r'(\d{10,})', str(lead['whatsapp_link']))
            if match:
                phone = match.group(1)
        if not phone:
            continue
        clean = re.sub(r'\D', '', str(phone))
        if len(clean) <= 11 and not clean.startswith('55'):
            clean = '55' + clean
        name = lead.get('name') or 'Visitante'
        text = msg_text.replace('{{nome}}', name).replace('{{name}}', name).replace('{nome}', name).replace('{name}', name)
        if media_file_data:
            messages.append({'number': clean, 'type': media_type, 'file': media_file_data, 'text': text})
        else:
            messages.append({'number': clean, 'type': 'text', 'text': text})

    if not messages:
        return

    token = instance.get('apikey')
    if not token:
        return

    result = uazapi_service.create_advanced_campaign(
        token=token,
        delay_min_sec=60,
        delay_max_sec=120,
        messages=messages,
        info=f"Rollover {step_label} c{cid}",
        scheduled_for=scheduled_ts,
    )

    if not result:
        print(f"  ❌ [Rollover {step_label}] Campaign '{campaign['name']}': Uazapi create_advanced_campaign falhou.")
        return

    folder_id = result.get('folder_id') or result.get('folderId')
    config_key = {3: 'rollover_fu2_folder_id', 4: 'rollover_fu3_folder_id'}.get(to_step)

    lead_ids = [l['id'] for l in rollover_leads]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE campaign_leads
            SET current_step = %s, cadence_status = 'snoozed', snooze_until = %s
            WHERE id = ANY(%s)
            """,
            (to_step, target_dt, lead_ids),
        )
        if folder_id and config_key:
            cur.execute(
                """
                UPDATE campaigns SET cadence_config = COALESCE(cadence_config, '{}')::jsonb || %s::jsonb
                WHERE id = %s
                """,
                (json.dumps({config_key: str(folder_id)}), cid),
            )
    conn.commit()
    print(f"  🔄 [Rollover] Campaign '{campaign['name']}': {len(lead_ids)} leads → {step_label}, agendado {target_dt.strftime('%d/%m %H:%M')} BRT")


def bootstrap_pending_leads(campaign, conn):
    """
    Handles leads that were sent by worker_sender but never entered the cadence cycle.
    These leads have status='sent' and cadence_status='pending' (or NULL).
    Sets them to 'snoozed' with snooze_until = now, so they are immediately
    picked up by process_campaign_sends on the next poll.
    Also tries to discover their Chatwoot conversation ID if missing.
    """
    cid = campaign['id']
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, phone, name, chatwoot_conversation_id
            FROM campaign_leads
            WHERE campaign_id = %s
              AND status = 'sent'
              AND (cadence_status IS NULL OR cadence_status = 'pending')
            LIMIT 50
        """, (cid,))
        pending_leads = cur.fetchall()
    
    if not pending_leads:
        return
    
    print(f"  🔄 Campaign '{campaign['name']}': Bootstrapping {len(pending_leads)} pending sent leads into cadence...")
    
    # Get step 2 delay for snooze calculation
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT delay_days FROM campaign_steps WHERE campaign_id = %s AND step_number = 2 LIMIT 1",
            (cid,)
        )
        step2 = cur.fetchone()
    
    delay_days = step2.get('delay_days') if step2 else None
    delay_days = 1 if delay_days is None else int(delay_days)
    now_br = datetime.now(BRAZIL_TZ)
    snooze_until = now_br + timedelta(minutes=2) if delay_days <= 0 else now_br + timedelta(days=delay_days)
    
    for lead in pending_leads:
        lead_id = lead['id']
        conv_id = lead['chatwoot_conversation_id']
        
        # Try to discover Chatwoot conversation if missing
        if not conv_id:
            conv_id = discover_chatwoot_conversation(lead['phone'], lead.get('name'))
            if conv_id:
                with conn.cursor() as cur:
                    cur.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (conv_id, lead_id))
                conn.commit()
                print(f"    🔗 Lead #{lead_id}: Linked to Chatwoot conv {conv_id}")
                time.sleep(0.3)  # Rate limit
        
        # Set to snoozed so cadence worker picks them up
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE campaign_leads 
                SET current_step = 1, 
                    cadence_status = 'snoozed', 
                    snooze_until = %s,
                    last_message_sent_at = COALESCE(last_message_sent_at, sent_at, NOW())
                WHERE id = %s
            """, (snooze_until, lead_id))
        conn.commit()
    
    print(f"  ✅ {len(pending_leads)} leads bootstrapped into cadence (snoozed until {snooze_until.strftime('%d/%m %H:%M')}).")


def process_campaign_sends(campaign, conn):
    cid = campaign['id']
    user_id = campaign.get('user_id')
    instance = get_campaign_instance(cid, conn)
    if not instance:
        return

    # Gate superadmin (mídia Uazapi apenas para superadmin)
    is_sa = False
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    if row and row.get('email') == SUPER_ADMIN_EMAIL:
        is_sa = True

    # Respeitar limite diário antes de enviar follow-ups
    plan_limit = get_user_daily_limit(user_id)
    if not check_daily_limit(user_id, plan_limit):
        return

    instance_name = instance['name']
    api_provider = instance.get('api_provider') or 'megaapi'

    # Get steps
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM campaign_steps WHERE campaign_id = %s ORDER BY step_number ASC", (cid,))
        steps = cur.fetchall()
    
    if not steps: return
    steps_by_number = {s['step_number']: s for s in steps}
    max_step = max(s['step_number'] for s in steps)

    # Find leads ready for follow-up (snooze expired)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, phone, name, current_step, cadence_status, whatsapp_link, chatwoot_conversation_id
            FROM campaign_leads
            WHERE campaign_id = %s
              AND cadence_status = 'snoozed'
              AND snooze_until <= NOW()
            ORDER BY snooze_until ASC
            LIMIT 20
        """, (cid,))
        ready_leads = cur.fetchall()

    if not ready_leads: return

    print(f"  📨 Campaign '{campaign['name']}': {len(ready_leads)} leads ready for follow-up")

    for lead in ready_leads:
        lead_id = lead['id']
        conv_id = lead['chatwoot_conversation_id']
        current_step = lead['current_step'] or 1
        next_step = current_step + 1

        # If no Chatwoot conversation ID, try to discover it
        if not conv_id:
            conv_id = discover_chatwoot_conversation(lead['phone'], lead.get('name'))
            if conv_id:
                with conn.cursor() as cur:
                    cur.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (conv_id, lead_id))
                conn.commit()

        state_stop = False
        state_reason = ""

        # --- DECISION MATRIX (Pre-Send) ---
        cw_data = get_chatwoot_conversation_details(conv_id)
        unread = cw_data.get('unread_count', 0) if cw_data else 0

        if cw_data:
            cw_labels = cw_data.get('labels', [])
            cw_status = cw_data.get('status')  # open, snoozed, resolved

            # A. Check Labels (Hard Stop)
            stop_labels = ['01-interessado', '02-demo', '03-negociacao', '04-ganho']
            lost_labels = ['05-perdido']

            if any(l in cw_labels for l in stop_labels):
                state_stop = True
                state_reason = f"Label Stop: {list(set(cw_labels) & set(stop_labels))}"

            elif any(l in cw_labels for l in lost_labels):
                state_stop = True
                state_reason = "Label Lost"

            # B. Check Context (Smart Pause)
            if not state_stop:
                if unread > 0:
                    print(f"    ⏸️ Lead #{lead_id}: Has {unread} unread messages. Pausing.")
                    continue

                # Check last message sender
                messages = get_chatwoot_conversation_messages(conv_id)
                last_sender_is_contact = False
                if messages:
                    for msg in reversed(messages):
                        mtype = msg.get('message_type')
                        if mtype in [0, 1]:
                            if mtype == 0:
                                last_sender_is_contact = True
                            break
                
                if last_sender_is_contact:
                    print(f"    ⏸️ Lead #{lead_id}: Last message is from contact. Pausing.")
                    continue
        else:
            if not conv_id:
                pass  # No Chatwoot ID yet, proceed with WhatsApp-only send
            else:
                print(f"    ⚠️ Lead #{lead_id}: Chatwoot fetch failed. Proceeding anyway.")

        # Handle Stop State
        if state_stop:
            with conn.cursor() as cur:
                cur.execute("UPDATE campaign_leads SET cadence_status = 'stopped', log = %s WHERE id = %s", (state_reason, lead_id))
            conn.commit()
            print(f"    🛑 Lead #{lead_id}: {state_reason}")
            continue

        # --- SENDING LOGIC ---
        step_config = steps_by_number.get(next_step)
        if not step_config:
            # End of cadence
            with conn.cursor() as cur:
                cur.execute("UPDATE campaign_leads SET cadence_status = 'completed' WHERE id = %s", (lead_id,))
            conn.commit()
            if conv_id:
                toggle_chatwoot_status(conv_id, 'resolved')
            print(f"    🏁 Lead #{lead_id}: Cadence completed (no more steps).")
            continue

        # Prepare Message
        phone = lead['phone']
        if not phone and lead.get('whatsapp_link'):
             match = re.search(r'(\d{10,})', str(lead['whatsapp_link']))
             if match: phone = match.group(1)
        
        if not phone:
             print(f"    ⚠️ Lead #{lead_id}: No phone.")
             continue
             
        phone_jid = format_jid(phone)
        
        raw_template = step_config['message_template']
        if not raw_template: continue

        message = ""
        try:
            parsed = json.loads(raw_template)
            if isinstance(parsed, list):
                message = random.choice(parsed)
            elif isinstance(parsed, str):
                message = parsed
            else:
                message = str(parsed)
        except:
            message = raw_template
        lead_name = lead.get('name', 'Visitante')
        message = message.replace('{{nome}}', lead_name).replace('{{name}}', lead_name)

        phone_num = re.sub(r'\D', '', str(phone))
        if len(phone_num) <= 11 and not phone_num.startswith('55'):
            phone_num = '55' + phone_num

        # Uazapi + superadmin + media: enviar APENAS mídia com caption (não enviar texto separado)
        sent_ok = False
        sent_via_uazapi_media = False
        if step_config.get('media_path') and api_provider == 'uazapi' and is_sa and uazapi_service and instance.get('apikey'):
            if _is_media_path_safe(step_config['media_path'], user_id) and os.path.exists(step_config['media_path']):
                result = uazapi_service.send_media(
                    instance['apikey'], phone_num,
                    step_config.get('media_type', 'image'),
                    step_config['media_path'],
                    caption=message
                )
                sent_ok = bool(result)
                sent_via_uazapi_media = True
        # MegaAPI + media: enviar mídia (comportamento existente; texto enviado em seguida)
        elif step_config.get('media_path') and api_provider != 'uazapi':
            send_media_message(instance_name, phone_jid, step_config['media_path'], step_config.get('media_type', 'image'))
            time.sleep(1)

        # Send Text (quando não enviou via mídia Uazapi)
        if not sent_via_uazapi_media:
            if api_provider == 'uazapi' and uazapi_service and instance.get('apikey'):
                result = uazapi_service.send_text(instance['apikey'], phone_num, message)
                sent_ok = bool(result)
            else:
                sent_ok = send_text_message(instance_name, phone_jid, message)

        if sent_ok:
            # SUCCESS: Enter MONITORING state (Safety Buffer)
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE campaign_leads
                    SET current_step = %s,
                        cadence_status = 'monitoring',
                        last_message_sent_at = NOW(),
                        snooze_until = NULL
                    WHERE id = %s
                """, (next_step, lead_id))
            conn.commit()
            print(f"    ✅ Lead #{lead_id}: Step {next_step} sent ({api_provider}). Entering 5m Safety Buffer.")
        else:
            print(f"    ❌ Lead #{lead_id}: Send failed.")

        # Cooldown
        time.sleep(random.randint(20, 40))

if __name__ == "__main__":
    process_cadence()
