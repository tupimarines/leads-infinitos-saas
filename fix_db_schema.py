import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            database=os.environ.get('DB_NAME', 'leads_infinitos'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', 'devpassword'),
            port=os.environ.get('DB_PORT', '5432')
        )
        return conn
    except Exception as e:
        print(f"❌ Erro ao conectar ao banco: {e}")
        return None

def fix_schema():
    print("🛠️ Iniciando correção manual do Schema...")
    
    conn = get_db_connection()
    if not conn:
        return

    cur = conn.cursor()
    
    try:
        # 1. Fix Users Table (is_admin)
        print("🔧 Verificando tabela 'users'...")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;")
        print("✅ Coluna 'is_admin' Verificada/Adicionada.")

        # 2. Fix Campaigns Table (closed_deals, sent_today)
        print("🔧 Verificando tabela 'campaigns'...")
        cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS closed_deals INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sent_today INTEGER DEFAULT 0;")
        print("✅ Colunas 'closed_deals' e 'sent_today' Verificadas/Adicionadas.")

        # 3. Fix Campaign Leads (whatsapp_link)
        print("🔧 Verificando tabela 'campaign_leads'...")
        cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS whatsapp_link TEXT;")
        print("✅ Coluna 'whatsapp_link' Verificada/Adicionada.")
        
        conn.commit()
        print("\n🎉 Correção aplicada com sucesso!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Erro ao aplicar correção: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    fix_schema()
