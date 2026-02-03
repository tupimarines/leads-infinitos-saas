import psycopg2
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )

def fix_all_users():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users")
            users = cur.fetchall()
            print(f"🔍 Verificando {len(users)} usuários...")
            fixed_count = 0
            for user in users:
                user_id = user[0]
                email = user[1]
                cur.execute("SELECT id, name FROM instances WHERE user_id = %s", (user_id,))
                if not cur.fetchone():
                    base_name = email.split('@')[0]
                    safe_name = "".join(c for c in base_name if c.isalnum())
                    timestamp = datetime.now().strftime('%H%M%S')
                    instance_name = f"autofix_{safe_name}_{user_id}_{timestamp}"
                    
                    # Corrected Schema: Removed created_at/updated_at if they auto-update or don't exist
                    # Based on user feedback that these columns were missing in the INSERT attempts
                    cur.execute(
                        """
                        INSERT INTO instances (user_id, name, apikey, status)
                        VALUES (%s, %s, %s, 'disconnected')
                        """,
                        (user_id, instance_name, instance_name)
                    )
                    fixed_count += 1
                    print(f"   🔧 CORRIGIDO: Usuário {email} -> Instância '{instance_name}'")
        conn.commit()
        print(f"✅ Concluído. {fixed_count} usuários corrigidos.")
    except Exception as e:
        print(f"❌ Erro: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_all_users()
