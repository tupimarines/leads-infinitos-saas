from app import init_db
import psycopg2
import os

print("Initializing DB...")
try:
    init_db()
    print("DB Initialized.")
except Exception as e:
    print(f"Error initializing DB: {e}")

# Verify tables
conn = psycopg2.connect(
    host=os.environ.get('DB_HOST', 'localhost'),
    database=os.environ.get('DB_NAME', 'leads_infinitos'),
    user=os.environ.get('DB_USER', 'postgres'),
    password=os.environ.get('DB_PASSWORD', 'devpassword'),
    port=os.environ.get('DB_PORT', '5432')
)
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables = [t[0] for t in cur.fetchall()]
print("Tables:", tables)

if 'campaigns' in tables and 'campaign_leads' in tables:
    print("Campaign tables exist!")
else:
    print("Campaign tables MISSING!")
conn.close()
