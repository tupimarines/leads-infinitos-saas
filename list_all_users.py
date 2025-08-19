#!/usr/bin/env python3
"""
Script para listar todos os usuários cadastrados na base de dados
"""

import sqlite3
import os
from datetime import datetime

def list_all_users():
    """Lista todos os usuários cadastrados no banco"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Buscar todos os usuários
        users = conn.execute(
            "SELECT id, email, password_hash FROM users ORDER BY id"
        ).fetchall()
        
        print(f"📊 TOTAL DE USUÁRIOS: {len(users)}")
        print("=" * 80)
        
        for i, user in enumerate(users, 1):
            print(f"\n👤 USUÁRIO {i}:")
            print(f"   ID: {user['id']}")
            print(f"   Email: {user['email']}")
            print(f"   Password Hash: {user['password_hash'][:50]}...")
            
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
            
            print(f"   📋 Licenças: {len(licenses)} encontradas")
            for license in licenses:
                print(f"      • ID: {license['id']} | {license['license_type']} | {license['status']}")
                print(f"        Purchase: {license['hotmart_purchase_id']}")
                print(f"        Expira: {license['expires_at']}")
            
            # Verificar webhooks relacionados
            webhooks = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM hotmart_webhooks 
                WHERE hotmart_purchase_id IN (
                    SELECT hotmart_purchase_id FROM licenses WHERE user_id = ?
                )
                """,
                (user['id'],)
            ).fetchone()
            
            webhook_count = webhooks['count'] if webhooks else 0
            print(f"   🔗 Webhooks relacionados: {webhook_count}")
            
            print("-" * 60)
        
        # Resumo geral
        print(f"\n📈 RESUMO GERAL:")
        print(f"   • Total de usuários: {len(users)}")
        
        total_licenses = conn.execute("SELECT COUNT(*) as count FROM licenses").fetchone()['count']
        print(f"   • Total de licenças: {total_licenses}")
        
        active_licenses = conn.execute("SELECT COUNT(*) as count FROM licenses WHERE status = 'active'").fetchone()['count']
        print(f"   • Licenças ativas: {active_licenses}")
        
        total_webhooks = conn.execute("SELECT COUNT(*) as count FROM hotmart_webhooks").fetchone()['count']
        print(f"   • Total de webhooks: {total_webhooks}")
        
        processed_webhooks = conn.execute("SELECT COUNT(*) as count FROM hotmart_webhooks WHERE processed = 1").fetchone()['count']
        print(f"   • Webhooks processados: {processed_webhooks}")
        
    except Exception as e:
        print(f"❌ Erro ao consultar banco: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def main():
    print("=" * 80)
    print("LISTAGEM DE TODOS OS USUÁRIOS CADASTRADOS")
    print("=" * 80)
    
    list_all_users()
    
    print("\n" + "=" * 80)
    print("IMPORTANTE:")
    print("• As senhas são armazenadas como hash (não é possível ver a senha original)")
    print("• Para resetar senhas, use o script create_temp_password.py")
    print("• Para verificar usuário específico, use check_user_password.py")
    print("=" * 80)

if __name__ == "__main__":
    main()
