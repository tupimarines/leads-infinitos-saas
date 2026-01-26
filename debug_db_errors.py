import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Force localhost for local check
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_PORT'] = '5432'
os.environ['DB_USER'] = 'postgres'
os.environ['DB_PASSWORD'] = 'devpassword'
os.environ['DB_NAME'] = 'leads_infinitos'

try:
    conn = psycopg2.connect(
        host=os.environ['DB_HOST'],
        database=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        port=os.environ['DB_PORT']
    )
    cur = conn.cursor()
    
    print("Checking for failed leads...")
    cur.execute("SELECT COUNT(*) FROM campaign_leads WHERE status = 'failed'")
    count = cur.fetchone()[0]
    print(f"Failed leads count: {count}")
    
    if count > 0:
        cur.execute("SELECT id, error_message, updated_at FROM campaign_leads WHERE status = 'failed' LIMIT 5")
        rows = cur.fetchall()
        for row in rows:
            print(f"ID: {row[0]}, Error: {row[1]}, Time: {row[2]}")
            
    # Also check campaign status
    cur.execute("SELECT id, status, sent_today FROM campaigns LIMIT 5")
    print("\nCampaigns:")
    for row in cur.fetchall():
        print(f"ID: {row[0]}, Status: {row[1]}, Sent Today: {row[2]}")

    conn.close()
except Exception as e:
    print(f"Error: {e}")
