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
    today_str = date.today().isoformat()
    
    query = """
    SELECT COUNT(cl.id) as count 
    FROM campaign_leads cl
    JOIN campaigns c ON cl.campaign_id = c.id
    WHERE c.user_id = %s 
    AND cl.status = 'sent' 
    AND date(cl.sent_at) = %s
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (user_id, today_str))
            row = cur.fetchone()
        
        current_sent = row['count']
        # print(f"User {user_id}: {current_sent}/{plan_limit} messages sent today.")
        return current_sent < plan_limit
    finally:
        conn.close()

def check_phone_on_whatsapp(instance_name, phone_jid):
    """
    Verifica se o número existe no WhatsApp usando Mega API.
    GET /rest/instance/isOnWhatsApp/{nome}?jid={jid}
    """
    url = f"{MEGA_API_URL}/rest/instance/isOnWhatsApp/{instance_name}"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"jid": phone_jid}
    
    try:
        # print(f"Checking existence for {phone_jid} on instance {instance_name}...")
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Mega API response matches: { "exists": true, "jid": "..." }
            return data.get('exists', False)
        else:
            print(f"Error checking WhatsApp existence: {response.status_code} - {response.text}")
            return False # Assume false or handle error differently? Safe to assume false to avoid ban on non-existent numbers.
            
    except Exception as e:
        print(f"Exception checking WhatsApp existence: {e}")
        return False

def send_message(instance_name, phone_jid, message):
    """
    Envia mensagem usando a Mega API.
    POST /rest/sendMessage/{instance_key}/text
    """
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
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return True, response.json() # Mega API returns 200 for success
        else:
            return False, f"{response.status_code} - {response.text}"
    except Exception as e:
        return False, str(e)

def process_campaigns():
    """
    Loop principal do Worker de Disparo.
    """
    print("Starting Sender Worker Loop (Mega API)...")
    
    while True:
        try:
            conn = get_db_connection()
            
            # 1. Buscar todas as campanhas 'running'
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM campaigns WHERE status = 'running'")
                campaigns = cur.fetchall()
            
            conn.close() # Close immediately to free connection, we'll reopen if needed
            
            if not campaigns:
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
                            if lic['license_type'] == 'anual': user_limit = max(user_limit, 30)
                            elif lic['license_type'] == 'semestral': user_limit = max(user_limit, 20)
                    
                    if not check_daily_limit(user_id, user_limit):
                        # print(f"User {user_id} limit reached. Skipping.")
                        # Should we pause the campaign? Maybe not, just wait for tomorrow.
                        continue
                    
                    # 3. Buscar instância conectada
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT name FROM instances WHERE user_id = %s AND status = 'connected'", 
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
                    
                    phone_jid = format_jid(lead['phone'])
                    
                    # 5. Check WhatsApp Existence (Mega API)
                    exists = check_phone_on_whatsapp(instance_name, phone_jid)
                    
                    if not exists:
                        print(f"Number {phone_jid} not on WhatsApp. Marking invalid.")
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE campaign_leads SET status = 'invalid', sent_at = NOW(), log = 'Not on WhatsApp' WHERE id = %s",
                                (lead['id'],)
                            )
                        conn.commit()
                        # Do not set delay for invalid numbers? Or set small delay?
                        # Let's set a small delay to avoid spamming the check API too fast
                        user_next_send_time[user_id] = datetime.now() + timedelta(seconds=2) 
                        continue

                    # 6. Preparar Mensagem (Variação)
                    message_text = "Olá!"
                    if campaign['message_template']:
                        try:
                            templates = json.loads(campaign['message_template'])
                            if isinstance(templates, list):
                                message_text = random.choice(templates)
                        except:
                            pass
                    
                    # Replace variables (basic)
                    # if lead['name']: message_text = message_text.replace("{name}", lead['name'])
                    
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
