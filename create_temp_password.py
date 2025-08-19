#!/usr/bin/env python3
"""
Script para criar uma nova senha temporária para o usuário
"""

import sqlite3
import os
import secrets
import string
from werkzeug.security import generate_password_hash

def generate_temp_password(length=12):
    """Gera uma senha temporária aleatória"""
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(characters) for _ in range(length))

def create_temp_password(email):
    """Cria uma nova senha temporária para o usuário"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar se o usuário existe
        user = conn.execute(
            "SELECT id, email FROM users WHERE email = ?",
            (email.lower(),)
        ).fetchone()
        
        if not user:
            print(f"❌ Usuário '{email}' não encontrado no banco de dados.")
            return None
        
        # Gerar nova senha temporária
        temp_password = generate_temp_password()
        password_hash = generate_password_hash(temp_password)
        
        # Atualizar senha no banco
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user['id'])
        )
        conn.commit()
        
        print(f"✅ Senha temporária criada com sucesso!")
        print(f"   Usuário: {user['email']}")
        print(f"   ID: {user['id']}")
        print(f"   Nova senha: {temp_password}")
        
        return temp_password
        
    except Exception as e:
        print(f"❌ Erro ao criar senha temporária: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("CRIAÇÃO DE SENHA TEMPORÁRIA")
    print("=" * 60)
    
    email = "augustogumi@gmail.com"
    print(f"\n🔧 Criando nova senha temporária para: {email}")
    print("-" * 40)
    
    temp_password = create_temp_password(email)
    
    if temp_password:
        print(f"\n" + "=" * 60)
        print("✅ SENHA TEMPORÁRIA CRIADA!")
        print("=" * 60)
        print(f"Email: {email}")
        print(f"Senha: {temp_password}")
        print("\n📝 INSTRUÇÕES:")
        print("1. Use esta senha para fazer login")
        print("2. Após o login, altere a senha na página de perfil")
        print("3. Esta senha é temporária e deve ser alterada")
        print("=" * 60)
    else:
        print(f"\n❌ Falha ao criar senha temporária")
        print("Verifique se o usuário existe no banco de dados")

if __name__ == "__main__":
    main()
