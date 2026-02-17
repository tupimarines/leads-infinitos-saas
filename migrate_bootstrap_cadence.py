"""
ONE-TIME MIGRATION SCRIPT: Bootstrap Cadence for Existing Leads
================================================================

This script runs ONCE to fix leads that were sent their initial message
but never entered the cadence cycle. It will:

1. Find all leads in step 1 (Inicial) for the super admin's campaigns
2. Discover their Chatwoot conversation IDs (multiple fallback strategies)
3. Check Chatwoot for replies — if replied, mark as 'stopped'
4. Send Follow-up 1 (step 2 message) to all remaining leads
5. Set cadence state: current_step=2, cadence_status='snoozed', snooze_until based on step 3 delay

After this script runs, the normal cadence worker will continue from step 2 onward.

Usage: python migrate_bootstrap_cadence.py [--dry-run]
"""

import os
import sys
import time
import json
import random
import requests
import re
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import pytz

load_dotenv()

# Config
BRAZIL_TZ = pytz.timezone('America/Sao_Paulo')
SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'

MEGA_API_URL = os.environ.get('MEGA_API_URL', 'https://ruker.megaapi.com.br')
MEGA_API_TOKEN = os.environ.get('MEGA_API_TOKEN')

CHATWOOT_API_URL = os.environ.get('CHATWOOT_API_URL', 'https://chatwoot.wbtech.dev')
CHATWOOT_ACCESS_TOKEN = os.environ.get('CHATWOOT_ACCESS_TOKEN')
CHATWOOT_ACCOUNT_ID = os.environ.get('CHATWOOT_ACCOUNT_ID', '2')

DRY_RUN = '--dry-run' in sys.argv

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )

def format_jid(phone):
    clean = re.sub(r'\D', '', str(phone))
    if len(clean) <= 11 and not clean.startswith('55'):
        clean = '55' + clean
    return clean + '@s.whatsapp.net'


# ── Chatwoot Discovery ──────────────────────────────────────────────

def discover_chatwoot_conversation(phone, name=None):
    """
    Discovers the Chatwoot conversation ID for a lead.
    Searches by phone (multiple formats) and name as fallbacks.
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
        except Exception:
            pass

    if not contact_id:
        return None

    try:
        conv_url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/{contact_id}/conversations"
        resp = requests.get(conv_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            conv_data = resp.json()
            if conv_data.get('payload') and len(conv_data['payload']) > 0:
                conv_id = conv_data['payload'][0]['id']
                print(f"    🔗 Found conversation {conv_id} (via {matched_via})")
                return conv_id
    except Exception as e:
        print(f"    ⚠️ Conv fetch error: {e}")

    return None


def get_chatwoot_conversation_details(conversation_id):
    """Fetches conversation details including labels, status, and recent messages."""
    if not conversation_id:
        return None
    
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def toggle_chatwoot_status(conversation_id, status, snoozed_until=None):
    """Toggles Chatwoot conversation status with optional snoozed_until timestamp."""
    if not conversation_id:
        return False
    
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
    headers = {"api_access_token": CHATWOOT_ACCESS_TOKEN, "Content-Type": "application/json"}
    payload = {"status": status}
    
    if status == 'snoozed' and snoozed_until:
        if hasattr(snoozed_until, 'timestamp'):
            payload["snoozed_until"] = int(snoozed_until.timestamp())
        else:
            payload["snoozed_until"] = int(snoozed_until)
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


# ── Mega API Send ────────────────────────────────────────────────────

def send_text_message(instance_name, phone_jid, message):
    if DRY_RUN or os.environ.get('MOCK_SENDER'):
        print(f"    [{'DRY-RUN' if DRY_RUN else 'MOCK'}] Would send to {phone_jid}: {message[:60]}...")
        return True

    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/text"
    headers = {"Authorization": MEGA_API_TOKEN, "Content-Type": "application/json"}
    payload = {"messageData": {"to": phone_jid, "text": message, "linkPreview": False}}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"    ❌ Send error: {e}")
        return False


def send_media_message(instance_name, phone_jid, media_path, media_type, caption=""):
    if DRY_RUN or os.environ.get('MOCK_SENDER'):
        print(f"    [{'DRY-RUN' if DRY_RUN else 'MOCK'}] Would send media to {phone_jid}")
        return True
    
    import base64
    if not os.path.exists(media_path):
        return False
    with open(media_path, 'rb') as f:
        file_data = base64.b64encode(f.read()).decode('utf-8')
    ext = os.path.splitext(media_path)[1].lower()
    mime_map = {'.jpg': 'image/jpeg', '.png': 'image/png', '.mp4': 'video/mp4'}
    mime = mime_map.get(ext, 'application/octet-stream')
    endpoint_type = 'imageMessage' if media_type == 'image' else 'videoMessage'
    url = f"{MEGA_API_URL}/rest/sendMessage/{instance_name}/{endpoint_type}"
    headers = {"Authorization": MEGA_API_TOKEN, "Content-Type": "application/json"}
    payload = {"messageData": {"to": phone_jid, "media": f"data:{mime};base64,{file_data}", "caption": caption, "fileName": os.path.basename(media_path)}}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.status_code == 200
    except Exception:
        return False


# ── Main Migration ───────────────────────────────────────────────────

def run_migration():
    now = datetime.now(BRAZIL_TZ)
    print(f"\n{'='*70}")
    print(f"  BOOTSTRAP CADENCE MIGRATION")
    print(f"  Time: {now.strftime('%d/%m/%Y %H:%M:%S')} BRT")
    print(f"  Mode: {'🔍 DRY RUN (no messages sent)' if DRY_RUN else '🚀 LIVE (messages will be sent!)'}")
    print(f"{'='*70}\n")

    conn = get_db_connection()

    # 1. Get super admin user
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, email FROM users WHERE email = %s", (SUPER_ADMIN_EMAIL,))
        admin = cur.fetchone()
    
    if not admin:
        print(f"❌ Super admin {SUPER_ADMIN_EMAIL} not found!")
        return
    
    print(f"👤 Admin: {admin['email']} (ID: {admin['id']})")

    # 2. Get cadence-enabled campaigns for this admin
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT c.id, c.name, c.status
            FROM campaigns c
            WHERE c.user_id = %s AND c.enable_cadence = TRUE
            ORDER BY c.id DESC
        """, (admin['id'],))
        campaigns = cur.fetchall()

    if not campaigns:
        print("❌ No cadence-enabled campaigns found for super admin!")
        return

    print(f"\n📋 Found {len(campaigns)} cadence campaigns:")
    for c in campaigns:
        print(f"   - [{c['id']}] {c['name']} (status: {c['status']})")

    # Process each campaign
    total_discovered = 0
    total_sent = 0
    total_skipped = 0
    total_stopped = 0

    for campaign in campaigns:
        cid = campaign['id']
        print(f"\n{'─'*50}")
        print(f"📌 Campaign: {campaign['name']} (ID: {cid})")

        # 3. Get campaign instance
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.name, i.apikey FROM campaign_instances ci 
                JOIN instances i ON ci.instance_id = i.id
                WHERE ci.campaign_id = %s AND i.status = 'connected' LIMIT 1
            """, (cid,))
            instance = cur.fetchone()

        if not instance:
            print("  ⚠️ No connected instance. Skipping.")
            continue

        instance_name = instance['name']
        print(f"  📱 Instance: {instance_name}")

        # 4. Get campaign steps
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM campaign_steps WHERE campaign_id = %s ORDER BY step_number ASC", (cid,))
            steps = cur.fetchall()

        if not steps:
            print("  ⚠️ No steps configured. Skipping.")
            continue

        steps_by_number = {s['step_number']: s for s in steps}
        step_numbers = [str(s['step_number']) for s in steps]
        print(f"  📝 Steps: {len(steps)} (Step {', '.join(step_numbers)})")

        # Step 2 is Follow-up 1
        step2 = steps_by_number.get(2)
        if not step2:
            print("  ⚠️ No step 2 (Follow-up 1) configured. Skipping.")
            continue
        
        # Get step 3 delay for snooze calculation after sending follow-up 1
        step3 = steps_by_number.get(3)
        snooze_delay = (step3['delay_days'] or 1) if step3 else 4  # Default 4 days if no step 3

        # DEBUG: Analyze leads in this campaign to understand why we aren't finding them
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT status, cadence_status, current_step, COUNT(*) as count
                FROM campaign_leads
                WHERE campaign_id = %s
                GROUP BY status, cadence_status, current_step
            """, (cid,))
            stats = cur.fetchall()
            print(f"  📊 Campaign Lead Stats (Debug):")
            for row in stats:
                print(f"     - status={row['status']}, cadence={row['cadence_status']}, step={row['current_step']}: {row['count']} leads")

        # 5. Get leads in step 1 that were sent (snoozed, active, or pending)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone, name, status, cadence_status, current_step, 
                       chatwoot_conversation_id, whatsapp_link, sent_at
                FROM campaign_leads
                WHERE campaign_id = %s
                  AND status = 'sent'
                  AND (cadence_status IN ('snoozed', 'active', 'pending') OR cadence_status IS NULL)
                  AND (current_step IS NULL OR current_step <= 1)
                ORDER BY id ASC
            """, (cid,))
            leads = cur.fetchall()

        if not leads:
            print("  ✅ No pending leads in step 1. All good.")
            continue

        print(f"\n  🎯 {len(leads)} leads in step 1 to process:")
        print(f"  {'─'*45}")

        # 6. Process each lead
        for i, lead in enumerate(leads, 1):
            lead_id = lead['id']
            lead_name = lead.get('name', 'Desconhecido')
            phone = lead['phone']
            conv_id = lead['chatwoot_conversation_id']
            original_status = lead['status']

            print(f"\n  [{i}/{len(leads)}] Lead #{lead_id}: {lead_name} ({phone}) [Status: {original_status}]")

            # 6a. Discover Chatwoot conversation ID if missing
            if not conv_id:
                conv_id = discover_chatwoot_conversation(phone, lead_name)
                if conv_id:
                    total_discovered += 1
                    if not DRY_RUN:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (conv_id, lead_id))
                        conn.commit()
                else:
                    print(f"    ⚠️ No Chatwoot conversation found")
                time.sleep(0.3)  # Rate limit
            else:
                print(f"    🔗 Already has conversation {conv_id}")

            # 6b. Check Chatwoot for replies (if we have conversation ID)
            should_stop = False
            if conv_id:
                cw_data = get_chatwoot_conversation_details(conv_id)
                if cw_data:
                    unread = cw_data.get('unread_count', 0)
                    cw_labels = cw_data.get('labels', [])
                    cw_status = cw_data.get('status')

                    # Check stop labels
                    stop_labels = ['01-interessado', '02-demo', '03-negociacao', '04-ganho', '05-perdido']
                    matched_labels = set(cw_labels) & set(stop_labels)
                    
                    if matched_labels:
                        should_stop = True
                        print(f"    🛑 STOP: Has labels {list(matched_labels)}")
                    elif unread > 0:
                        should_stop = True
                        print(f"    🛑 STOP: Has {unread} unread messages (replied)")
                    elif cw_status == 'open':
                        should_stop = True
                        print(f"    🛑 STOP: Conversation is OPEN (being handled)")
                    else:
                        print(f"    ✅ Chatwoot: status={cw_status}, unread=0, no stop labels")

            if should_stop:
                total_stopped += 1
                if not DRY_RUN:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE campaign_leads 
                            SET cadence_status = 'stopped', 
                                current_step = 1,
                                log = 'Bootstrap: Stopped (reply/label detected)'
                            WHERE id = %s
                        """, (lead_id,))
                    conn.commit()
                print(f"    → Marked as STOPPED")
                continue

            # 6c. Prepare and send Follow-up 1 message
            if not phone and lead.get('whatsapp_link'):
                match = re.search(r'(\d{10,})', str(lead['whatsapp_link']))
                if match:
                    phone = match.group(1)

            if not phone:
                print(f"    ⚠️ No phone number. Skipping.")
                total_skipped += 1
                continue

            phone_jid = format_jid(phone)

            # Parse message template
            raw_template = step2['message_template']
            if not raw_template:
                print(f"    ⚠️ Empty message template for step 2. Skipping.")
                total_skipped += 1
                continue

            message = ""
            try:
                parsed = json.loads(raw_template)
                if isinstance(parsed, list):
                    message = random.choice(parsed)
                elif isinstance(parsed, str):
                    message = parsed
                else:
                    message = str(parsed)
            except (json.JSONDecodeError, TypeError):
                message = raw_template

            message = message.replace('{{nome}}', lead_name).replace('{{name}}', lead_name)

            # Send media if configured
            if step2.get('media_path'):
                send_media_message(instance_name, phone_jid, step2['media_path'], step2.get('media_type', 'image'))
                time.sleep(1)

            # Send text message
            success = send_text_message(instance_name, phone_jid, message)

            if success:
                total_sent += 1
                snooze_until = datetime.now(BRAZIL_TZ) + timedelta(days=snooze_delay)

                if not DRY_RUN:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE campaign_leads 
                            SET status = 'sent',
                                current_step = 2, 
                                cadence_status = 'snoozed', 
                                snooze_until = %s,
                                last_message_sent_at = NOW(),
                                sent_at = COALESCE(sent_at, NOW())
                            WHERE id = %s
                        """, (snooze_until, lead_id))
                    conn.commit()

                    # Snooze in Chatwoot with timestamp
                    if conv_id:
                        toggle_chatwoot_status(conv_id, 'snoozed', snoozed_until=snooze_until)

                print(f"    ✅ Follow-up 1 SENT! Snoozed until {snooze_until.strftime('%d/%m %H:%M')}")
            else:
                total_skipped += 1
                print(f"    ❌ Send FAILED")

            # Anti-ban delay between messages
            delay = random.randint(120, 180)
            print(f"    ⏳ Waiting {delay}s...")
            if not DRY_RUN:
                time.sleep(delay)
            else:
                time.sleep(0.1)

    # Summary
    print(f"\n{'='*70}")
    print(f"  MIGRATION COMPLETE {'(DRY RUN)' if DRY_RUN else ''}")
    print(f"{'='*70}")
    print(f"  📊 Results:")
    print(f"     🔗 Conversations discovered: {total_discovered}")
    print(f"     ✅ Follow-up 1 sent:         {total_sent}")
    print(f"     🛑 Stopped (replied):         {total_stopped}")
    print(f"     ⚠️  Skipped (errors):          {total_skipped}")
    print(f"{'='*70}\n")

    conn.close()


if __name__ == "__main__":
    run_migration()
