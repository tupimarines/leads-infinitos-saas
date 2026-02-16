from app import app, get_db_connection
import psycopg2

def migrate():
    print("Starting migration...")
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Address, Website, Category, Location
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS address TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS website TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS category TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS location TEXT;")
            
            # Metrics (Float/Int)
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS reviews_count FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS reviews_rating FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS latitude FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS longitude FLOAT;")
            
        conn.commit()
        print("Migration executed successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    migrate()
