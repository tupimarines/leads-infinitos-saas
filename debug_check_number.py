import os
import psycopg2
import requests
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database credentials from user provided image/info
DB_HOST = os.environ.get('DB_HOST', 'leads-infinitos-pyv1-postgresdb-yecnan')
DB_NAME = os.environ.get('DB_NAME', 'db-1')
DB_USER = os.environ.get('DB_USER', 'postgres-infinitos')
DB_PASS = os.environ.get('DB_PASSWORD', '37PaMSLKz9qwFgQ')
DB_PORT = os.environ.get('DB_PORT', '5432')

MEGA_API_URL = os.environ.get('MEGA_API_URL', 'https://ruker.megaapi.com.br')
MEGA_API_TOKEN = os.environ.get('MEGA_API_TOKEN')

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def check_number(phone_number):
    conn = get_db_connection()
    if not conn:
        print("Could not connect to DB to fetch instance.")
        return

    try:
        cur = conn.cursor()
        # Get a connected instance
        cur.execute("SELECT name, user_id FROM instances WHERE status = 'connected' LIMIT 1")
        instance = cur.fetchone()
        
        if not instance:
            print("No connected instances found in DB.")
            return

        instance_name = instance[0]
        user_id = instance[1]
        print(f"Using instance: {instance_name} (User ID: {user_id})")

        # Format number (simple check)
        if not "@s.whatsapp.net" in phone_number:
            if len(phone_number) <= 13: # Just digits
                 phone_number = f"{phone_number}@s.whatsapp.net"
        
        print(f"Checking number: {phone_number}")
        
        url = f"{MEGA_API_URL}/rest/instance/isOnWhatsApp/{instance_name}"
        headers = {
            "Authorization": MEGA_API_TOKEN,
            "Content-Type": "application/json"
        }
        params = {"jid": phone_number}

        print(f"Request URL: {url}")
        print(f"Params: {params}")

        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        print(f"Status Code: {response.status_code}")
        try:
            print(f"Response JSON: {response.json()}")
        except:
            print(f"Response Text: {response.text}")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        phone = sys.argv[1]
        check_number(phone)
    else:
        print("Usage: python debug_check_number.py <phone_number>")
        print("Example: python debug_check_number.py 5541996453236")
