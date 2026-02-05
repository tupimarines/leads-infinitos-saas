import os
import time
import json
import random
import requests
import re
from datetime import datetime, date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Configuração
MEGA_API_URL = os.environ.get('MEGA_API_URL', 'https://ruker.megaapi.com.br')
# Token might be needed if it's not passed per instance, but usually instance has its own key or we use a global token.
# User said "header Authorization e value token contido em .env".
MEGA_API_TOKEN = os.environ.get('MEGA_API_TOKEN')

# In-memory delay tracking for non-blocking concurrency
# struct: { user_id: datetime_when_user_can_send_next }
user_next_send_time = {}

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )
    return conn

def extract_phone_from_whatsapp_link(whatsapp_link):
    """
    Extrai o número de telefone de um link do WhatsApp.
    Exemplos de formatos aceitos:
    - https://wa.me/5511999999999
    - https://api.whatsapp.com/send?phone=5511999999999
    - wa.me/5511999999999
    Retorna o número ou None se não conseguir extrair.
    """
    if not whatsapp_link:
        return None
    
    # Regex para extrair número de links do WhatsApp
    patterns = [
        r'wa\.me/([0-9]+)',
        r'phone=([0-9]+)',
        r'whatsapp\.com/send\?phone=([0-9]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, str(whatsapp_link))
        if match:
            return match.group(1)
    
    # Se não encontrou padrão conhecido, tenta extrair qualquer sequência de dígitos longa
    digits = re.sub(r'\D', '', str(whatsapp_link))
    if len(digits) >= 10:  # Mínimo para um telefone válido
        return digits
    
    return None

def get_instance_status_api(instance_name):
    """
    Verifica o status da instância via API.
    GET /rest/instance/{instance_key}
    Retorna: 'connected', 'disconnected', ou None se erro
    """
    url = f"{MEGA_API_URL}/rest/instance/{instance_name}"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"❌ Status API Error: {response.status_code}")
            return None
            
        data = response.json()
        
        # Handle array response
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        
        # Check various status indicators
        is_connected = False
        
        if data.get('instance_data'):
            is_connected = data['instance_data'].get('phone_connected', False)
        elif 'phone_connected' in data:
            is_connected = data.get('phone_connected', False)
        elif data.get('status') in ['CONNECTED', 'open', 'connected']:
            is_connected = True
        elif isinstance(data.get('instance'), dict):
            status_val = data['instance'].get('status')
            if status_val in ['connected', 'CONNECTED', 'open']:
                is_connected = True
        
        if data.get('error'):
            is_connected = False
            
        return 'connected' if is_connected else 'disconnected'
        
    except Exception as e:
        print(f"❌ Exception checking instance status: {e}")
        return None

def restart_instance_api(instance_name):
    """
    Reinicia a instância via API.
    DELETE /rest/instance/{instance_key}/restart
    Retorna: True se sucesso, False caso contrário
    """
    url = f"{MEGA_API_URL}/rest/instance/{instance_name}/restart"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    try:
        print(f"🔄 Restarting instance {instance_name}...")
        response = requests.delete(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            print(f"✅ Restart command sent successfully")
            return True
        else:
            print(f"❌ Restart failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Exception restarting instance: {e}")
        return False

def verify_and_recover_instance(instance_name):
    """
    Verifica status da instância e tenta recovery se necessário.
    1. Verifica status atual
    2. Se desconectada/erro, tenta restart
    3. Aguarda e verifica novamente
    
    Retorna: True se instância está conectada, False caso contrário
    """
    print(f"🔍 Verifying instance {instance_name} status...")
    
    # 1. Check current status
    status = get_instance_status_api(instance_name)
    
    if status == 'connected':
        print(f"✅ Instance {instance_name} is connected")
        return True
    
    # 2. Try restart
    print(f"⚠️ Instance {instance_name} not connected (status: {status}). Attempting recovery...")
    
    if restart_instance_api(instance_name):
        # 3. Wait for restart
        print(f"⏳ Waiting 5s for instance recovery...")
        time.sleep(5)
        
        # 4. Verify again
        new_status = get_instance_status_api(instance_name)
        
        if new_status == 'connected':
            print(f"✅ Instance {instance_name} recovered successfully!")
            # Update DB to connected
            try:
                with psycopg2.connect(
                    host=os.environ.get('DB_HOST', 'localhost'),
                    database=os.environ.get('DB_NAME', 'leads_infinitos'),
                    user=os.environ.get('DB_USER', 'postgres'),
                    password=os.environ.get('DB_PASSWORD', 'devpassword'),
                    port=os.environ.get('DB_PORT', '5432')
                ) as conn_fix:
                    with conn_fix.cursor() as cur_fix:
                        cur_fix.execute("UPDATE instances SET status = 'connected', updated_at = NOW() WHERE name = %s", (instance_name,))
                    conn_fix.commit()
                print(f"✅ Instance {instance_name} marked as connected in DB")
            except Exception as e:
                print(f"⚠️ Failed to update DB status: {e}")
            return True
        else:
            print(f"❌ Instance {instance_name} recovery failed (status: {new_status})")
    
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

def check_daily_limit(user_id, plan_limit):
    """
    Verifica se o usuário já atingiu o limite diário de disparos.
    Retorna True se PODE enviar, False se atingiu o limite.
    """
    # Define Brazil timezone
    # Assuming the server/DB might be UTC, we want to count messages based on BRT day
    # Adjust logic to cast timestamp to 'America/Sao_Paulo'
    
    query = """
    SELECT COUNT(cl.id) as count 
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s 
    AND cl.status = 'sent' 
    AND date(cl.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo') = date(NOW() AT TIME ZONE 'America/Sao_Paulo')
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()
        
        current_sent = row['count']
        # print(f"User {user_id}: {current_sent}/{plan_limit} messages sent today.")
        return current_sent < plan_limit
    finally:
        conn.close()

def is_instance_error_message(error_msg):
    """
    Analisa a mensagem de erro da API para determinar se é erro de instância.
    
    Retorna True se o erro indica problema com a instância (desconectada, não encontrada).
    Retorna False se o erro indica problema com o número (não registrado, inválido).
    """
    if not error_msg:
        return True  # Se não há mensagem, assume erro de instância por segurança
    
    error_lower = str(error_msg).lower()
    
    # Padrões que indicam ERRO DE INSTÂNCIA (deve tentar recovery)
    instance_error_patterns = [
        'instance not found',
        'not connected',
        'disconnected',
        'connection closed',
        'session closed',
        'qr code',
        'not logged',
        'unauthorized',
        'authentication',
        'timeout',
        'socket',
        'network'
    ]
    
    # Padrões que indicam NÚMERO INVÁLIDO (não é erro de instância)
    number_error_patterns = [
        'not registered',
        'not on whatsapp',
        'invalid number',
        'number not found',
        'does not exist',
        'não registrado',
        'número inválido'
    ]
    
    # Primeiro verificar se é erro de número (mais específico)
    for pattern in number_error_patterns:
        if pattern in error_lower:
            return False  # Não é erro de instância, é número inválido
    
    # Depois verificar se é erro de instância
    for pattern in instance_error_patterns:
        if pattern in error_lower:
            return True  # É erro de instância
    
    # Default: assume erro de instância por segurança
    return True


def check_phone_on_whatsapp(instance_name, phone_jid, retry_count=0):
    """
    Verifica se o número existe no WhatsApp usando Mega API.
    GET /rest/instance/isOnWhatsApp/{nome}?jid={jid}
    
    Retorna tupla: (exists, correct_jid, is_instance_error)
    - exists: True se número existe no WhatsApp
    - correct_jid: JID corrigido pela API
    - is_instance_error: True se foi erro de instância (não marcar número como inválido)
    """
    MAX_RETRIES = 1  # Permite 1 retry após recovery
    
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK] Checked existence for {phone_jid}: True")
        time.sleep(0.1)
        return True, phone_jid, False

    url = f"{MEGA_API_URL}/rest/instance/isOnWhatsApp/{instance_name}"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"jid": phone_jid}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        # Parse response first to analyze error type
        resp_json = None
        try:
            resp_json = response.json()
        except:
            pass
        
        # Detect API/Instance errors
        is_error = False
        error_message = None
        
        if response.status_code == 404:
            is_error = True
            error_message = "Instance not found (404)"
        elif response.status_code == 200 and resp_json:
            # Handle both dict and list responses
            data = resp_json
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            
            if isinstance(data, dict):
                # Check if API returned error flag
                if data.get('error') is True:
                    error_message = data.get('message', data.get('msg', str(data)))
                    
                    # CRITICAL FIX: Check if error is about the NUMBER, not the INSTANCE
                    if not is_instance_error_message(error_message):
                        # This is a number validation error, NOT an instance error
                        print(f"📱 Number {phone_jid} validation failed: {error_message}")
                        return False, None, False  # is_instance_error=False, so number will be marked invalid
                    else:
                        is_error = True
                        print(f"🔍 Instance error detected: {error_message}")

        if is_error:
            print(f"⚠️ Instance {instance_name} API error detected (retry {retry_count}/{MAX_RETRIES})...")
            print(f"   Error details: {error_message}")
            
            # Try recovery if haven't exceeded retries
            if retry_count < MAX_RETRIES:
                print(f"🔄 Attempting instance recovery...")
                
                if verify_and_recover_instance(instance_name):
                    # Recovery successful - retry the check
                    print(f"🔄 Retrying number verification after recovery...")
                    return check_phone_on_whatsapp(instance_name, phone_jid, retry_count + 1)
                else:
                    # Recovery failed - mark instance as disconnected but DON'T mark number as invalid
                    print(f"❌ Recovery failed. Marking instance as disconnected...")
                    try:
                        with psycopg2.connect(
                            host=os.environ.get('DB_HOST', 'localhost'),
                            database=os.environ.get('DB_NAME', 'leads_infinitos'),
                            user=os.environ.get('DB_USER', 'postgres'),
                            password=os.environ.get('DB_PASSWORD', 'devpassword'),
                            port=os.environ.get('DB_PORT', '5432')
                        ) as conn_fix:
                            with conn_fix.cursor() as cur_fix:
                                cur_fix.execute("UPDATE instances SET status = 'disconnected', updated_at = NOW() WHERE name = %s", (instance_name,))
                            conn_fix.commit()
                        print(f"✅ Instance {instance_name} marked as disconnected in DB.")
                    except Exception as e_db:
                        print(f"❌ Failed to update DB status: {e_db}")
                    
                    # Return with is_instance_error=True so caller doesn't mark number as invalid
                    print(f"⏭️ Skipping number {phone_jid} (will retry when instance reconnects)")
                    return False, None, True
            else:
                # Already retried - give up
                print(f"❌ Max retries reached. Instance {instance_name} still has errors.")
                return False, None, True

        if response.status_code == 200 and resp_json:
            data = resp_json
            
            # Handle list response: [{ "exists": true, "jid": "..." }]
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
                
            # Mega API response: { "exists": true, "jid": "..." }
            exists = data.get('exists', False)
            
            if not exists:
                print(f"📱 Number not on WhatsApp. API Response: {data}")
                
            correct_jid = data.get('jid', phone_jid) # Use API JID if available, else fallback
            return exists, correct_jid, False
        else:
            print(f"Error checking WhatsApp existence: {response.status_code} - {response.text}")
            return False, None, True  # Treat as instance error to be safe
            
    except Exception as e:
        print(f"Exception checking WhatsApp existence: {e}")
        return False, None, True  # Treat as instance error to be safe

def send_message(instance_name, phone_jid, message):
    """
    Envia mensagem usando a Mega API.
    POST /rest/sendMessage/{instance_key}/text
    """
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK] Sent message to {phone_jid}: {message[:20]}...")
        time.sleep(0.5)
        return True, {"key": "mocked_key"}

    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/text"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    payload = {
        "messageData": {
            "to": phone_jid,
            "text": message,
            "linkPreview": False
        }
    }
    
    # DETAILED LOGGING FOR DEBUGGING
    print(f"=== SENDING MESSAGE ===")
    print(f"URL: {url}")
    print(f"To: {phone_jid}")
    print(f"Message: {message}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        # LOG RESPONSE
        print(f"Response Status: {response.status_code}")
        print(f"Response Body: {response.text}")
        
        if response.status_code == 200:
            response_data = response.json()
            print(f"✅ Message sent successfully!")
            print(f"Response Data: {json.dumps(response_data, indent=2)}")
            return True, response_data
        else:
            error_msg = f"{response.status_code} - {response.text}"
            print(f"❌ Failed to send message: {error_msg}")
            return False, error_msg
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Exception sending message: {error_msg}")
        return False, error_msg


def process_campaigns():
    """
    Loop principal do Worker de Disparo.
    """
    print("Starting Sender Worker Loop (Mega API)...")
    
    while True:
        try:
            conn = get_db_connection()
            
            # 1. Buscar campanhas 'running' OU 'pending' que atingiram horário agendado
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM campaigns 
                    WHERE status IN ('pending', 'running')
                    AND (scheduled_start IS NULL OR scheduled_start <= NOW())
                """)
                campaigns = cur.fetchall()
            
            conn.close() # Close immediately to free connection, we'll reopen if needed
            
            # NEW: Auto-start scheduled campaigns that reached their time
            for campaign in campaigns:
                if campaign['status'] == 'pending':
                    # Campaign was scheduled and time has arrived, start it
                    conn_temp = get_db_connection()
                    with conn_temp.cursor() as cur_temp:
                        cur_temp.execute(
                            "UPDATE campaigns SET status = 'running' WHERE id = %s",
                            (campaign['id'],)
                        )
                    conn_temp.commit()
                    conn_temp.close()
                    campaign['status'] = 'running'  # Update local dict for this iteration
                    print(f"Campaign {campaign['id']} auto-started (was scheduled)")
            
            # Heartbeat (verbose) or just informative?
            # print(f"❤️ Worker Heartbeat: Checking {len(campaigns)} active campaigns...")
            
            if not campaigns:
                # print("No active campaigns. Sleeping...")
                time.sleep(5)
                continue
                
            active_users_processed = 0
            
            # Shuffle campaigns to give fairness if multiple campaigns per user? 
            # Or just iterate. The user-level lock is what matters.
            
            for campaign in campaigns:
                user_id = campaign['user_id']
                
                # --- CHECK COOLDOWN (Non-blocking delay) ---
                if user_id in user_next_send_time:
                    if datetime.now() < user_next_send_time[user_id]:
                        # Still in cooldown, skip this user
                        continue
                
                # --- PROCESS USER ---
                active_users_processed += 1
                
                conn = get_db_connection()
                try:
                    # 2. Verificar Daily Limit
                     # Determine limit based on licenses (cached or queried)
                    user_limit = 10 # Default
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT license_type FROM licenses WHERE user_id = %s AND status = 'active' AND expires_at > NOW()", 
                            (user_id,)
                        )
                        licenses = cur.fetchall()
                        for lic in licenses:
                            l_type = lic['license_type']
                            if l_type == 'scale': user_limit = max(user_limit, 30)
                            elif l_type == 'pro': user_limit = max(user_limit, 20)
                    
                    if not check_daily_limit(user_id, user_limit):
                        # print(f"User {user_id} limit reached. Skipping.")
                        # Should we pause the campaign? Maybe not, just wait for tomorrow.
                        continue
                    
                    # 3. Buscar instância conectada
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT name FROM instances WHERE user_id = %s AND status = 'connected' ORDER BY updated_at DESC LIMIT 1", 
                            (user_id,)
                        )
                        instance = cur.fetchone()
                    
                    if not instance:
                        # print(f"User {user_id} no connected instance.")
                        continue
                        
                    instance_name = instance['name']

                    # 4. Pegar 1 lead pendente (FIFO)
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT * FROM campaign_leads WHERE campaign_id = %s AND status = 'pending' ORDER BY id ASC LIMIT 1",
                            (campaign['id'],)
                        )
                        lead = cur.fetchone()
                    
                    if not lead:
                        # Mark campaign as completed?
                         with conn.cursor() as cur:
                            # check if any allowed pending leads remain (status 'pending')
                            cur.execute("SELECT count(*) FROM campaign_leads WHERE campaign_id = %s AND status = 'pending'", (campaign['id'],))
                            count = cur.fetchone()[0]
                            if count == 0:
                                cur.execute("UPDATE campaigns SET status = 'completed' WHERE id = %s", (campaign['id'],))
                                conn.commit()
                         continue
                    
                    # 5. Determinar número a usar (priorizar whatsapp_link)
                    phone_to_use = None
                    
                    # Primeiro tentar whatsapp_link
                    if lead.get('whatsapp_link'):
                        phone_to_use = extract_phone_from_whatsapp_link(lead['whatsapp_link'])
                        if phone_to_use:
                            print(f"Using phone from WhatsApp link: {phone_to_use}")
                    
                    # Se não conseguiu do link, usar phone_number
                    if not phone_to_use and lead.get('phone'):
                        phone_to_use = lead['phone']
                        print(f"Using phone_number field: {phone_to_use}")
                    
                    # Se mesmo assim não tem número, marcar como inválido
                    if not phone_to_use:
                        print(f"Lead {lead['id']} has no valid phone number. Marking as invalid.")
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE campaign_leads SET status = 'invalid', sent_at = NOW(), log = 'No phone number available' WHERE id = %s",
                                (lead['id'],)
                            )
                        conn.commit()
                        continue
                    
                    phone_jid = format_jid(phone_to_use)
                    
                    # 6. Check WhatsApp Existence (Mega API)
                    exists, correct_jid, is_instance_error = check_phone_on_whatsapp(instance_name, phone_jid)
                    
                    if is_instance_error:
                        # Instance error - don't mark number as invalid, just skip for now
                        # Number stays as 'pending' and will be retried when instance reconnects
                        print(f"⏭️ Skipping lead {lead['id']} due to instance error (will retry later)")
                        # Set a longer cooldown since instance has issues
                        user_next_send_time[user_id] = datetime.now() + timedelta(seconds=30)
                        continue
                    
                    if not exists:
                        print(f"Number {phone_jid} not on WhatsApp. Marking invalid.")
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE campaign_leads SET status = 'invalid', sent_at = NOW(), log = 'Not on WhatsApp' WHERE id = %s",
                                (lead['id'],)
                            )
                        conn.commit()
                        # Short delay for invalid numbers
                        user_next_send_time[user_id] = datetime.now() + timedelta(seconds=2) 
                        continue
                    
                    # Use the corrected JID from API (e.g. might handle the extra 9 digit automatically)
                    if correct_jid and correct_jid != phone_jid:
                        print(f"🔄 Correcting JID from {phone_jid} to {correct_jid}")
                        phone_jid = correct_jid

                    # 7. Preparar Mensagem (Variação + Substituição de Variáveis)
                    message_text = "Olá!"
                    if campaign['message_template']:
                        try:
                            templates = json.loads(campaign['message_template'])
                            if isinstance(templates, list):
                                message_text = random.choice(templates)
                        except:
                            pass
                    
                    # Replace variables
                    if lead.get('name'):
                        message_text = message_text.replace("{nome}", lead['name'])
                        message_text = message_text.replace("{name}", lead['name'])  # Suportar ambas as versões
                    
                    # 7. Enviar
                    print(f"Sending to {phone_jid} (User {user_id})...")
                    success, log = send_message(instance_name, phone_jid, message_text)
                    
                    # 8. Atualizar DB
                    new_status = 'sent' if success else 'failed'
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE campaign_leads SET status = %s, sent_at = NOW(), log = %s WHERE id = %s",
                            (new_status, str(log), lead['id'])
                        )
                    conn.commit()
                    
                    # 8.1 Post-send: Sync instance status if message was sent successfully
                    if success:
                        # Ensure instance is marked as connected in DB (message sent = instance working)
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE instances SET status = 'connected', updated_at = NOW() WHERE name = %s AND status != 'connected'",
                                    (instance_name,)
                                )
                            conn.commit()
                        except Exception as e_sync:
                            print(f"⚠️ Failed to sync instance status: {e_sync}")
                    
                    # 9. Set Random Delay for NEXT send (Antiban)
                    # User asked for: Math.floor(Math.random() * (600 - 300 + 1) + 300) -> 300 to 600s
                    # For testing we might want smaller. But let's respect the prompt's algorithm.
                    delay_seconds = random.randint(300, 600)
                    
                    # FOR TESTING: Override to smaller manually if needed, but I will stick to logic.
                    # Uncomment below for fast local testing:
                    # delay_seconds = random.randint(10, 30) 
                    
                    user_next_send_time[user_id] = datetime.now() + timedelta(seconds=delay_seconds)
                    print(f"User {user_id} cooldown set for {delay_seconds}s.")
                    
                except Exception as e_inner:
                    print(f"Error processing user {user_id}: {e_inner}")
                finally:
                    conn.close()
            
            # If no active users were processed (all in cooldown or no campaigns), sleep briefly
            if active_users_processed == 0:
                time.sleep(1)
                
        except Exception as e:
            print(f"Error in sender loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    process_campaigns()
