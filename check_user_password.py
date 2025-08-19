#!/usr/bin/env python3
"""
Script para verificar informações do usuário no banco de dados
"""

import sqlite3
import os
from datetime import datetime

def check_user_info(email):
    """Verifica informações do usuário no banco"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Primeiro, vamos verificar a estrutura da tabela users
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"📋 Colunas da tabela users: {columns}")
        
        # Buscar usuário pelo email (sem created_at por enquanto)
        user = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email.lower(),)
        ).fetchone()
        
        if not user:
            print(f"❌ Usuário '{email}' não encontrado no banco de dados.")
            return
        
        print(f"\n✅ Usuário encontrado:")
        print(f"   ID: {user['id']}")
        print(f"   Email: {user['email']}")
        print(f"   Password Hash: {user['password_hash']}")
        
        # Verificar licenças do usuário
        licenses = conn.execute(
            """
            SELECT id, hotmart_purchase_id, license_type, status, purchase_date, expires_at
            FROM licenses 
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user['id'],)
        ).fetchall()
        
        print(f"\n📋 Licenças ({len(licenses)} encontradas):")
        for license in licenses:
            print(f"   • ID: {license['id']}")
            print(f"     Purchase ID: {license['hotmart_purchase_id']}")
            print(f"     Tipo: {license['license_type']}")
            print(f"     Status: {license['status']}")
            print(f"     Compra: {license['purchase_date']}")
            print(f"     Expira: {license['expires_at']}")
            print()
        
        # Verificar webhooks relacionados
        webhooks = conn.execute(
            """
            SELECT id, event_type, hotmart_purchase_id, processed
            FROM hotmart_webhooks 
            WHERE hotmart_purchase_id IN (
                SELECT hotmart_purchase_id FROM licenses WHERE user_id = ?
            )
            ORDER BY id DESC
            """,
            (user['id'],)
        ).fetchall()
        
        if webhooks:
            print(f"🔗 Webhooks relacionados ({len(webhooks)} encontrados):")
            for webhook in webhooks:
                print(f"   • {webhook['event_type']} - {webhook['hotmart_purchase_id']} - Processado: {webhook['processed']}")
        
    except Exception as e:
        print(f"❌ Erro ao consultar banco: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("VERIFICAÇÃO DE USUÁRIO NO BANCO DE DADOS")
    print("=" * 60)
    
    email = "augustogumi@gmail.com"
    print(f"\n🔍 Verificando usuário: {email}")
    print("-" * 40)
    
    check_user_info(email)
    
    print("\n" + "=" * 60)
    print("IMPORTANTE:")
    print("• A senha é armazenada como hash (não é possível recuperar a senha original)")
    print("• Para resetar a senha, use a funcionalidade 'Esqueci minha senha'")
    print("• Ou execute o script de criação de usuário de teste")
    print("=" * 60)

if __name__ == "__main__":
    main()
