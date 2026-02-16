
import os
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
CHATWOOT_API_URL = os.environ.get('CHATWOOT_API_URL', 'https://chatwoot.wbtech.dev')
CHATWOOT_ACCESS_TOKEN = os.environ.get('CHATWOOT_ACCESS_TOKEN')
CHATWOOT_ACCOUNT_ID = os.environ.get('CHATWOOT_ACCOUNT_ID', '2')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_NAME = os.environ.get('DB_NAME', 'leads_infinitos')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'devpassword')
DB_PORT = os.environ.get('DB_PORT', '5432')
SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT
        )
        return conn
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        return None

def snooze_conversation_in_chatwoot(conversation_id):
    """
    Calls Chatwoot API to toggle status to snoozed.
    POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status
    """
    url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/toggle_status"
    headers = {
        "api_access_token": CHATWOOT_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "status": "snoozed"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Chatwoot returns different structures, but success usually means status changed
            # We can check data['payload']['current_status'] or similar if detailed verification is needed
            print(f"✅ [Chatwoot] Conversation {conversation_id} snoozed successfully.")
            return True
        elif response.status_code == 404:
             print(f"⚠️ [Chatwoot] Conversation {conversation_id} not found (404).")
             return False
        else:
            print(f"❌ [Chatwoot] Failed to snooze {conversation_id}: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ [Chatwoot] Exception snoozing {conversation_id}: {e}")
        return False

def sync_snoozed_conversations():
    print("🔄 Starting Snoozed Conversations Sync...")
    
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Get User ID for Super Admin (Augusto)
            # We only want to process leads for this user as requested
            cur.execute("SELECT id FROM users WHERE email = %s", (SUPER_ADMIN_EMAIL,))
            user_row = cur.fetchone()
            
            if not user_row:
                print(f"❌ User {SUPER_ADMIN_EMAIL} not found.")
                return
            
            user_id = user_row['id']
            print(f"ℹ️ specific user_id: {user_id} ({SUPER_ADMIN_EMAIL})")

            # 2. Select leads that are snoozed locally but potentially open in Chatwoot
            # We assume if they are 'snoozed' in DB, they SHOULD be 'snoozed' in Chatwoot.
            # We fetch leads belonging to campaigns of this user.
            query = """
                SELECT cl.id, cl.chatwoot_conversation_id, cl.snooze_until, c.name as campaign_name
                FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s
                AND cl.cadence_status = 'snoozed'
                AND cl.snooze_until > NOW()
                AND cl.chatwoot_conversation_id IS NOT NULL
            """
            
            cur.execute(query, (user_id,))
            leads = cur.fetchall()
            
            print(f"🔍 Found {len(leads)} snoozed leads for validation in Chatwoot.")
            
            for lead in leads:
                conv_id = lead['chatwoot_conversation_id']
                print(f"👉 Syncing Lead #{lead['id']} (Conv {conv_id}) - Snoozed until {lead['snooze_until']}")
                
                # Perform the snooze action
                # Note: We are unconditionally forcing 'snoozed' status on Chatwoot 
                # because the local DB says it should be snoozed.
                snooze_conversation_in_chatwoot(conv_id)
                
                # Rate limit prevention
                time.sleep(0.5)

            print("✅ Sync completed.")

    except Exception as e:
        print(f"❌ detailed error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if not CHATWOOT_ACCESS_TOKEN:
        print("❌ CHATWOOT_ACCESS_TOKEN is missing in .env")
    else:
        sync_snoozed_conversations()
