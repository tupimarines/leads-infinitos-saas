#!/usr/bin/env python3
"""
Script para criar usuário de teste no ambiente de produção
Execute este script no Dokploy após o deploy
"""

import psycopg2
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

def create_production_user():
    """Cria usuário de teste no ambiente de produção"""
    conn = get_db_connection()
    target_email = 'augustogumi@gmail.com'
    
    try:
        with conn.cursor() as cur:
            # Verificar se o usuário já existe
            cur.execute("SELECT id FROM users WHERE email = %s", (target_email,))
            existing_user = cur.fetchone()
            
            user_id = None
            if existing_user:
                print(f"⚠️  Usuário {target_email} já existe.")
                user_id = existing_user[0]
            else:
                # Criar usuário
                password_hash = generate_password_hash('q1w2e3r4t5')
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (target_email, password_hash)
                )
                user_id = cur.fetchone()[0]
                print(f"✅ Usuário {target_email} criado com sucesso! ID: {user_id}")
            
            # Verificar se já tem licença
            cur.execute("SELECT id FROM licenses WHERE user_id = %s", (user_id,))
            if cur.fetchone():
                print("⚠️  Usuário já possui licença.")
            else:
                expires_at = datetime.now() + timedelta(days=365*50)
                cur.execute(
                    """
                    INSERT INTO licenses 
                    (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        f"LIFETIME-PROD-{datetime.now().strftime('%Y%m%d')}",
                        '5974664',
                        'anual',
                        datetime.now().isoformat(),
                        expires_at.isoformat()
                    )
                )
                print(f"✅ Licença vitalícia criada para {target_email}")
                
            # Verificar Instância WhatsApp (FIX)
            cur.execute("SELECT id FROM instances WHERE user_id = %s", (user_id,))
            if cur.fetchone():
                print("⚠️  Usuário já possui instância vinculada.")
            else:
                # Criar instância fake ou real? Como é produção, vamos assumir que o usuário
                # deve criar uma instância com nome seguro para evitar conflitos se não existir.
                # Mas para evitar 404, vamos criar o registro "disconnected"
                instance_name = f"inst_{user_id}_{datetime.now().strftime('%H%M%S')}"
                cur.execute(
                    "INSERT INTO instances (user_id, name, apikey, status) VALUES (%s, %s, %s, 'disconnected')",
                    (user_id, instance_name, instance_name)
                )
                print(f"✅ Instância {instance_name} criada (disconnected). Conecte via painel.")
        
        conn.commit()
            print("✅ Licença vitalícia criada para augustogumi@gmail.com")
        
        conn.commit()
        print(f"\n🎉 Usuário de produção configurado!")
        print(f"📧 Email: augustogumi@gmail.com")
        print(f"🔑 Senha: q1w2e3r4t5")
        print(f"📅 Licença: Vitalícia (expira em 2075)")
        print(f"\n🌐 Agora você pode testar em produção!")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao criar usuário: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔄 Criando usuário de teste para produção...")
    create_production_user()
