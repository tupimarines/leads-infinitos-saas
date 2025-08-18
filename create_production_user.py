#!/usr/bin/env python3
"""
Script para criar usuário de teste no ambiente de produção
Execute este script no Dokploy após o deploy
"""

import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

def create_production_user():
    """Cria usuário de teste no ambiente de produção"""
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar se o usuário já existe
        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ?", 
            ('augustogumi@gmail.com',)
        ).fetchone()
        
        if existing_user:
            print("⚠️  Usuário augustogumi@gmail.com já existe.")
            user_id = existing_user['id']
        else:
            # Criar usuário
            password_hash = generate_password_hash('q1w2e3r4t5')
            cur = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                ('augustogumi@gmail.com', password_hash)
            )
            user_id = cur.lastrowid
            print("✅ Usuário augustogumi@gmail.com criado com sucesso!")
        
        # Verificar se já tem licença
        existing_license = conn.execute(
            "SELECT id FROM licenses WHERE user_id = ?", 
            (user_id,)
        ).fetchone()
        
        if existing_license:
            print("⚠️  Usuário já possui licença vitalícia.")
        else:
            # Criar licença vitalícia (expira em 50 anos)
            expires_at = datetime.now() + timedelta(days=365*50)
            
            conn.execute(
                """
                INSERT INTO licenses 
                (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
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
