import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Force localhost for local execution
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
    
    print("Migrating: Adding sent_today to campaigns...")
    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sent_today INTEGER DEFAULT 0;")
    conn.commit()
    print("Migration successful.")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")
