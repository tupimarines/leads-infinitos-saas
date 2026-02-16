"""
Worker Cadence — Processes multi-step campaign cadence follow-ups.

Runs as a separate process alongside worker_sender.py.
For each cadence-enabled campaign:
  1. Finds leads whose snooze_until has elapsed and are ready for the next step
  2. Sends the step's message (with optional media) via Mega API
  3. Advances the lead to the next step or marks as 'completed'
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

CADENCE_POLL_INTERVAL = 60  # seconds between each poll cycle


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


def send_text_message(instance_name, phone_jid, message):
    """Send a text message via Mega API."""
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK-CADENCE] Text to {phone_jid}: {message[:40]}...")
        time.sleep(0.5)
        return True

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
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            print(f"  ✅ Text sent to {phone_jid}")
            return True
        else:
            print(f"  ❌ Text failed ({response.status_code}): {response.text[:120]}")
            return False
    except Exception as e:
        print(f"  ❌ Text exception: {e}")
        return False


def send_media_message(instance_name, phone_jid, media_path, media_type, caption=""):
    """Send a media message (image or video) via Mega API."""
    if os.environ.get('MOCK_SENDER'):
        print(f"[MOCK-CADENCE] Media ({media_type}) to {phone_jid}")
        time.sleep(0.5)
        return True

    if not os.path.exists(media_path):
        print(f"  ⚠️ Media file not found: {media_path}")
        return False

    # Read and encode as base64
    with open(media_path, 'rb') as f:
        file_data = base64.b64encode(f.read()).decode('utf-8')

    # Determine MIME type
    ext = os.path.splitext(media_path)[1].lower()
    mime_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp',
        '.mp4': 'video/mp4', '.avi': 'video/avi', '.mov': 'video/quicktime'
    }
    mime = mime_map.get(ext, 'application/octet-stream')

    # Use Mega API media endpoint
    endpoint_type = 'imageMessage' if media_type == 'image' else 'videoMessage'
    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/{endpoint_type}"
    headers = {
        "Authorization": MEGA_API_TOKEN,
        "Content-Type": "application/json"
    }
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
        if response.status_code == 200:
            print(f"  ✅ Media ({media_type}) sent to {phone_jid}")
            return True
        else:
            print(f"  ❌ Media failed ({response.status_code}): {response.text[:120]}")
            return False
    except Exception as e:
        print(f"  ❌ Media exception: {e}")
        return False


def get_campaign_instance(campaign_id, conn):
    """Get the first active instance for this campaign."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.name, i.apikey 
            FROM campaign_instances ci 
            JOIN instances i ON ci.instance_id = i.id
            WHERE ci.campaign_id = %s AND i.status = 'connected'
            LIMIT 1
        """, (campaign_id,))
        row = cur.fetchone()
        return row if row else None


def process_cadence():
    """Main loop for cadence worker."""
    print("🔄 Starting Cadence Worker...")

    while True:
        try:
            if not is_business_hours():
                now_brazil = datetime.now(BRAZIL_TZ)
                print(f"⏰ [Cadence] Off hours ({now_brazil.strftime('%H:%M')} BRT). Waiting...")
                time.sleep(60)
                continue

            conn = get_db_connection()

            # 1. Find cadence-enabled campaigns that are running
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

            print(f"\n📋 [Cadence] Found {len(campaigns)} active cadence campaign(s)")

            for campaign in campaigns:
                cid = campaign['id']
                cname = campaign['name']
                uid = campaign['user_id']

                # Get instance for this campaign
                instance = get_campaign_instance(cid, conn)
                if not instance:
                    print(f"  ⚠️ Campaign '{cname}' (#{cid}): No connected instance, skipping")
                    continue

                instance_name = instance['name']

                # 2. Get all steps for this campaign
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT step_number, step_label, message_template, media_path, media_type, delay_days
                        FROM campaign_steps
                        WHERE campaign_id = %s
                        ORDER BY step_number ASC
                    """, (cid,))
                    steps = cur.fetchall()

                if not steps:
                    print(f"  ⚠️ Campaign '{cname}' (#{cid}): No steps configured, skipping")
                    continue

                max_step = max(s['step_number'] for s in steps)
                steps_by_number = {s['step_number']: s for s in steps}

                # 3. Find leads ready for follow-up
                #    - cadence_status = 'snoozed' AND snooze_until <= NOW()
                #    - OR cadence_status = 'pending' AND current_step = 1 (initial send done by worker_sender)
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT id, phone, name, current_step, cadence_status, whatsapp_link
                        FROM campaign_leads
                        WHERE campaign_id = %s
                          AND cadence_status = 'snoozed'
                          AND snooze_until IS NOT NULL
                          AND snooze_until <= NOW()
                        ORDER BY snooze_until ASC
                        LIMIT 50
                    """, (cid,))
                    ready_leads = cur.fetchall()

                if not ready_leads:
                    continue

                print(f"  📨 Campaign '{cname}': {len(ready_leads)} lead(s) ready for follow-up")

                for lead in ready_leads:
                    lead_id = lead['id']
                    current_step = lead['current_step'] or 1
                    next_step = current_step + 1

                    # Check if we have a step config for next_step
                    step_config = steps_by_number.get(next_step)
                    if not step_config:
                        # No more steps — mark as completed
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE campaign_leads 
                                SET cadence_status = 'completed', snooze_until = NULL
                                WHERE id = %s
                            """, (lead_id,))
                        conn.commit()
                        print(f"    ✅ Lead #{lead_id} ({lead['name']}): Cadence completed (no more steps)")
                        continue

                    # Build JID
                    phone = lead['phone']
                    if not phone and lead.get('whatsapp_link'):
                        # Extract from link
                        match = re.search(r'(\d{10,})', str(lead['whatsapp_link']))
                        if match:
                            phone = match.group(1)
                    if not phone:
                        print(f"    ⚠️ Lead #{lead_id}: No phone, skipping")
                        continue

                    phone_jid = format_jid(phone)

                    # Select message template (random if multiple)
                    templates = json.loads(step_config['message_template']) if step_config['message_template'] else []
                    if not templates:
                        print(f"    ⚠️ Lead #{lead_id}: Step {next_step} has no message template, skipping")
                        continue

                    message = random.choice(templates)
                    # Replace {{nome}} placeholder
                    lead_name = lead.get('name', 'Visitante')
                    message = message.replace('{{nome}}', lead_name).replace('{{name}}', lead_name)

                    # Send media first if present
                    media_sent = True
                    if step_config.get('media_path') and os.path.exists(step_config['media_path']):
                        media_sent = send_media_message(
                            instance_name, phone_jid,
                            step_config['media_path'],
                            step_config.get('media_type', 'image'),
                            caption=""
                        )
                        if media_sent:
                            time.sleep(1)  # Brief pause between media and text

                    # Send text message
                    text_sent = send_text_message(instance_name, phone_jid, message)

                    if text_sent:
                        # Determine next state
                        if next_step >= max_step:
                            # This was the last step — mark completed
                            new_status = 'completed'
                            new_snooze = None
                        else:
                            # Schedule next follow-up
                            next_step_config = steps_by_number.get(next_step + 1)
                            delay = next_step_config['delay_days'] if next_step_config else 1
                            new_status = 'snoozed'
                            new_snooze = datetime.now(BRAZIL_TZ) + timedelta(days=delay)

                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE campaign_leads 
                                SET current_step = %s, cadence_status = %s, snooze_until = %s,
                                    last_message_sent_at = NOW()
                                WHERE id = %s
                            """, (next_step, new_status, new_snooze, lead_id))
                        conn.commit()

                        status_label = '🏁 completed' if new_status == 'completed' else f'⏸ snoozed (next in {delay}d)'
                        print(f"    ✅ Lead #{lead_id} ({lead_name}): Step {next_step} sent → {status_label}")
                    else:
                        print(f"    ❌ Lead #{lead_id} ({lead_name}): Failed to send step {next_step}")

                    # Delay between sends — MUST match worker_sender (300-600s) to avoid WhatsApp restrictions
                    delay_between = random.randint(300, 600)
                    print(f"    ⏳ Antiban cooldown: {delay_between}s before next send")
                    time.sleep(delay_between)

            conn.close()
            print(f"💤 [Cadence] Sleeping {CADENCE_POLL_INTERVAL}s...")
            time.sleep(CADENCE_POLL_INTERVAL)

        except Exception as e:
            print(f"❌ [Cadence] Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    process_cadence()
