#!/usr/bin/env python3
"""
Script para configurar todos os usuários com licenças vitalícias
"""

import sqlite3
import os
import secrets
import string
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta

def generate_temp_password(length=12):
    """Gera uma senha temporária aleatória"""
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(characters) for _ in range(length))

def setup_all_users():
    """Configura todos os usuários com licenças vitalícias"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Lista de usuários para criar
        users_to_create = [
            {
                'email': 'admin@example.com',
                'password': 'admin123456',
                'purchase_id': 'LIFETIME-1-20250818'
            },
            {
                'email': 'testui@example.com', 
                'password': 'test123456',
                'purchase_id': 'LIFETIME-2-20250818'
            },
            {
                'email': 'augustogumi@gmail.com',
                'password': 'YGuuWqGiDluT',  # Senha temporária já criada
                'purchase_id': 'LIFETIME-3-20250818'
            }
        ]
        
        print("🔧 CONFIGURANDO USUÁRIOS COM LICENÇAS VITALÍCIAS")
        print("=" * 60)
        
        for user_data in users_to_create:
            email = user_data['email']
            password = user_data['password']
            purchase_id = user_data['purchase_id']
            
            print(f"\n👤 Processando: {email}")
            
            # Verificar se usuário já existe
            existing_user = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (email.lower(),)
            ).fetchone()
            
            if existing_user:
                print(f"   ✅ Usuário já existe (ID: {existing_user['id']})")
                user_id = existing_user['id']
                
                # Atualizar senha
                password_hash = generate_password_hash(password)
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id)
                )
                print(f"   🔄 Senha atualizada")
                
            else:
                # Criar novo usuário
                password_hash = generate_password_hash(password)
                cursor = conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                    (email.lower(), password_hash)
                )
                user_id = cursor.lastrowid
                print(f"   ✅ Usuário criado (ID: {user_id})")
            
            # Verificar se licença já existe
            existing_license = conn.execute(
                "SELECT id FROM licenses WHERE hotmart_purchase_id = ?",
                (purchase_id,)
            ).fetchone()
            
            if existing_license:
                print(f"   ✅ Licença já existe (ID: {existing_license['id']})")
            else:
                # Criar licença vitalícia
                expires_at = datetime.now() + timedelta(days=365*50)  # 50 anos
                cursor = conn.execute(
                    """
                    INSERT INTO licenses 
                    (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        purchase_id,
                        '5974664',  # Product ID do Leads Infinitos
                        'anual',
                        datetime.now().isoformat(),
                        expires_at.isoformat()
                    )
                )
                license_id = cursor.lastrowid
                print(f"   ✅ Licença vitalícia criada (ID: {license_id})")
                print(f"   📅 Expira em: {expires_at.strftime('%Y-%m-%d')}")
            
            print(f"   🔑 Senha: {password}")
        
        # Commit das alterações
        conn.commit()
        
        print(f"\n✅ TODOS OS USUÁRIOS CONFIGURADOS!")
        print("=" * 60)
        
        # Mostrar resumo
        total_users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()['count']
        total_licenses = conn.execute("SELECT COUNT(*) as count FROM licenses").fetchone()['count']
        active_licenses = conn.execute("SELECT COUNT(*) as count FROM licenses WHERE status = 'active'").fetchone()['count']
        
        print(f"📊 RESUMO:")
        print(f"   • Usuários: {total_users}")
        print(f"   • Licenças: {total_licenses}")
        print(f"   • Licenças ativas: {active_licenses}")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao configurar usuários: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("CONFIGURAÇÃO DE USUÁRIOS COM LICENÇAS VITALÍCIAS")
    print("=" * 60)
    
    success = setup_all_users()
    
    if success:
        print(f"\n" + "=" * 60)
        print("✅ CONFIGURAÇÃO CONCLUÍDA!")
        print("=" * 60)
        print("📝 PRÓXIMOS PASSOS:")
        print("1. Commit das alterações: git add app.db")
        print("2. Commit: git commit -m 'Add users with lifetime licenses'")
        print("3. Push: git push origin main")
        print("4. Deploy será atualizado automaticamente no Dokku")
        print("=" * 60)
    else:
        print(f"\n❌ Falha na configuração")

if __name__ == "__main__":
    main()
