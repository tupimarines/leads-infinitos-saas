#!/usr/bin/env python3
"""
Script para criar usuário de teste com licença vitalícia (Versão PostgreSQL)
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
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

def create_test_user():
    """Cria usuário de teste com licença vitalícia"""
    conn = get_db_connection()
    
    try:
        # Verificar se o usuário já existe
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM users WHERE email = %s", 
                ('augustogumi@gmail.com',)
            )
            existing_user = cur.fetchone()
        
        if existing_user:
            print("⚠️  Usuário augustogumi@gmail.com já existe.")
            user_id = existing_user['id']
        else:
            # Criar usuário
            password_hash = generate_password_hash('q1w2e3r4t5')
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    ('augustogumi@gmail.com', password_hash)
                )
                user_id = cur.fetchone()[0]
            print("✅ Usuário augustogumi@gmail.com criado com sucesso!")
        
        # Verificar se já tem licença
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM licenses WHERE user_id = %s", 
                (user_id,)
            )
            existing_license = cur.fetchone()
        
        if existing_license:
            print("⚠️  Usuário já possui licença vitalícia.")
        else:
            # Criar licença vitalícia (expira em 50 anos)
            expires_at = datetime.now() + timedelta(days=365*50)
            
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO licenses 
                    (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        f"LIFETIME-TEST-{datetime.now().strftime('%Y%m%d')}",
                        '5974664',
                        'anual',
                        datetime.now().isoformat(),
                        expires_at.isoformat()
                    )
                )
            print("✅ Licença vitalícia criada para augustogumi@gmail.com")
        
        conn.commit()
        print(f"\n🎉 Usuário de teste configurado!")
        print(f"📧 Email: augustogumi@gmail.com")
        print(f"🔑 Senha: q1w2e3r4t5")
        print(f"📅 Licença: Vitalícia (expira em 2075)")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao criar usuário: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔄 Criando usuário de teste...")
    create_test_user()
