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

# Chatwoot Config
CHATWOOT_API_URL = os.environ.get('CHATWOOT_API_URL', 'https://chatwoot.wbtech.dev')
CHATWOOT_ACCESS_TOKEN = os.environ.get('CHATWOOT_ACCESS_TOKEN')
CHATWOOT_ACCOUNT_ID = os.environ.get('CHATWOOT_ACCOUNT_ID', '2')

CADENCE_POLL_INTERVAL = 60  # seconds between each poll cycle
SAFETY_BUFFER_MINUTES = 5

def get_db_connection():
    return psycopg2.connect(
        os.environ.get('DATABASE_URL'),
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

def toggle_chatwoot_status(conversation_id, status):
    """
    Toggles conversation status ('snoozed', 'open', 'resolved').
    """
    if not conversation_id: return False
    
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN, "Content-Type": "application/json"}
    payload = {"status": status}
    
    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
        return True
    except:
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
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.name, i.apikey FROM campaign_instances ci 
            JOIN instances i ON ci.instance_id = i.id
            WHERE ci.campaign_id = %s AND i.status = 'connected' LIMIT 1
        """, (campaign_id,))
        row = cur.fetchone()
        return row if row else None

# --- MAIN LOGIC ---

def process_cadence():
    print("🔄 Starting Intelligent Cadence Worker...")

    while True:
        try:
            conn = get_db_connection()

            # --- PART A: SAFETY BUFFER CHECK (Monitoring Phase) ---
            # Finds leads sent > 5 mins ago that are still in 'monitoring' status
            check_monitoring_leads(conn)

            # --- PART B: SENDING PHASE ---
            if not is_business_hours():
                now_brazil = datetime.now(BRAZIL_TZ)
                print(f"⏰ [Cadence] Off hours ({now_brazil.strftime('%H:%M')} BRT). Waiting...")
                conn.close()
                time.sleep(60)
                continue

            # 1. Find active cadence campaigns
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.id, c.name, c.user_id 
                    FROM campaigns c
                    WHERE c.enable_cadence = TRUE
                      AND c.status IN ('running', 'pending')
                      AND (c.scheduled_start IS NULL OR c.scheduled_start <= NOW())
                """)
                campaigns = cur.fetchall()

            if not campaigns:
                conn.close()
                time.sleep(CADENCE_POLL_INTERVAL)
                continue

            for campaign in campaigns:
                process_campaign_sends(campaign, conn)

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
    buffer_time = datetime.now() - timedelta(minutes=SAFETY_BUFFER_MINUTES)
    
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Fetch leads in monitoring that are 'ripe' (sent > 5 mins ago)
        # Note: 'monitoring' status might be custom, ensure it maps to cadence_status column or use a specific flag
        # We'll assume cadence_status can be 'monitoring'
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
        
        # 1. Check Chatwoot Context
        cw_data = get_chatwoot_conversation_details(conv_id)
        
        abort_snooze = False
        abort_reason = ""

        if cw_data:
            # Check unread count
            unread = cw_data.get('unread_count', 0)
            status = cw_data.get('status')
            messages = cw_data.get('messages', [])
            
            # Check last message sender
            last_msg_is_contact = False
            if messages:
                last_msg = messages[-1] # Usually sorted, verify API behavior if needed
                if last_msg.get('message_type') == 0: # 0 = Incoming, 1 = Outgoing
                    last_msg_is_contact = True
            
            if unread > 0:
                abort_snooze = True
                abort_reason = f"Unread count is {unread}"
            elif last_msg_is_contact:
                abort_snooze = True
                abort_reason = "Last message is from Contact"
        else:
            # If we can't check Chatwoot, what to do?
            # Safe bet: Assume safe OR retry? Let's assume safe to keep flow moving, but log warning.
            print(f"  ⚠️ Lead #{lead_id}: Could not fetch Chatwoot details. Proceeding with snooze.")

        with conn.cursor() as cur:
            if abort_snooze:
                # ABORT: Mark as stopped, leave conversation OPEN
                cur.execute("""
                    UPDATE campaign_leads SET cadence_status = 'stopped', log = %s WHERE id = %s
                """, (f"Safety Buffer Abort: {abort_reason}", lead_id))
                conn.commit()
                print(f"  🛑 Lead #{lead_id}: Snooze ABORTED. {abort_reason}")
            else:
                # SAFE: Execute Snooze + Schedule Next Step
                # Determine next step delay
                cur.execute("""
                    SELECT delay_days FROM campaign_steps 
                    WHERE campaign_id = %s AND step_number = %s
                """, (lead['campaign_id'], lead['current_step'] + 1))
                next_step_row = cur.fetchone()
                
                if next_step_row:
                    delay = next_step_row['delay_days']
                    snooze_until = datetime.now(BRAZIL_TZ) + timedelta(days=delay)
                    new_status = 'snoozed'
                    
                    # Update DB
                    cur.execute("""
                        UPDATE campaign_leads 
                        SET cadence_status = %s, snooze_until = %s 
                        WHERE id = %s
                    """, (new_status, snooze_until, lead_id))
                    
                    # Execute Chatwoot Snooze
                    toggle_chatwoot_status(conv_id, 'snoozed')
                    
                    print(f"  💤 Lead #{lead_id}: Safety Check passed. Snoozed until {snooze_until.strftime('%d/%m %H:%M')}.")
                else:
                    # No more steps? Mark completed
                    cur.execute("UPDATE campaign_leads SET cadence_status = 'completed' WHERE id = %s", (lead_id,))
                    toggle_chatwoot_status(conv_id, 'snoozed') # Snooze anyway? Or resolve?
                    print(f"  🏁 Lead #{lead_id}: Cadence completed.")
            conn.commit()


def process_campaign_sends(campaign, conn):
    cid = campaign['id']
    instance = get_campaign_instance(cid, conn)
    if not instance: return

    instance_name = instance['name']

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

    print(f"  📨 Campaign '{campaign['name']}': {len(ready_leads)} leads ready checks")

    for lead in ready_leads:
        lead_id = lead['id']
        conv_id = lead['chatwoot_conversation_id']
        current_step = lead['current_step'] or 1
        next_step = current_step + 1

        state_stop = False
        state_reason = ""

        # --- DECISION MATRIX (Pre-Send) ---
        
        # 1. Fetch Chatwoot Details
        cw_data = get_chatwoot_conversation_details(conv_id)
        
        if cw_data:
            cw_labels = cw_data.get('labels', [])
            cw_status = cw_data.get('status') # open, snoozed, resolved
            
            # A. Check Labels (Hard Stop)
            stop_labels = ['01-interessado', '02-demo', '03-negociacao', '04-ganho']
            lost_labels = ['05-perdido']
            
            if any(l in cw_labels for l in stop_labels):
                state_stop = True
                state_reason = f"Label Stop: {list(set(cw_labels) & set(stop_labels))}"
                # Mark converted? 
                
            elif any(l in cw_labels for l in lost_labels):
                state_stop = True
                state_reason = "Label Lost"
            
            # B. Check Context (Smart Pause)
            if not state_stop:
                if cw_status == 'open':
                    # PAUSE (Skip this run, maybe check again later)
                    print(f"    ⏸️ Lead #{lead_id}: Status is OPEN. Pausing.")
                    continue 
                
                # If Resolved/Snoozed, check who sent last message
                messages = cw_data.get('messages', [])
                if messages:
                    last_msg = messages[-1]
                    if last_msg.get('message_type') == 0: # 0 = Incoming (Contact)
                         # Contact replied, but status is resolved/snoozed.
                         # Rule: "Treat as No Reply" -> PROCEED
                         pass 
        else:
            # If no Chatwoot ID or fetch failed
            if not conv_id:
                # No ID linked yet? Proceed blindly? Or skip?
                # Let's proceed, as we might be purely WhatsApp based if Sync hasn't run
                pass
            else:
                print(f"    ⚠️ Lead #{lead_id}: Chatwoot fetch failed. Skipping safety check.")

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
        
        templates = json.loads(step_config['message_template']) if step_config['message_template'] else []
        if not templates: continue
        
        message = random.choice(templates)
        lead_name = lead.get('name', 'Visitante')
        message = message.replace('{{nome}}', lead_name).replace('{{name}}', lead_name)

        # Send Media
        if step_config.get('media_path'):
            send_media_message(instance_name, phone_jid, step_config['media_path'], step_config.get('media_type', 'image'))
            time.sleep(1)

        # Send Text
        if send_text_message(instance_name, phone_jid, message):
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
            print(f"    ✅ Lead #{lead_id}: Step {next_step} sent. Entering 5m Safety Buffer.")
        else:
            print(f"    ❌ Lead #{lead_id}: Send failed.")

        # Cooldown
        time.sleep(random.randint(20, 40)) # Slightly faster for cadence than sender

if __name__ == "__main__":
    process_cadence()
