#!/usr/bin/env python3
"""
Script para debugar o problema do webhook
"""

import sqlite3
import os

def debug_webhook():
    """Debuga o problema do webhook"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        print("🔍 DEBUG DO WEBHOOK")
        print("=" * 60)
        
        # Verificar usuários existentes
        users = conn.execute("SELECT id, email FROM users").fetchall()
        print(f"📊 Usuários existentes: {len(users)}")
        for user in users:
            print(f"   • {user['email']} (ID: {user['id']})")
        
        # Verificar webhooks SALE_COMPLETED
        webhooks = conn.execute("""
            SELECT event_type, hotmart_purchase_id, payload
            FROM hotmart_webhooks 
            WHERE event_type = 'SALE_COMPLETED'
            ORDER BY created_at DESC 
            LIMIT 3
        """).fetchall()
        
        print(f"\n📊 Webhooks SALE_COMPLETED:")
        for webhook in webhooks:
            print(f"   • {webhook['hotmart_purchase_id']}")
            # Extrair email do payload
            import json
            try:
                payload = json.loads(webhook['payload'])
                buyer_email = payload.get('data', {}).get('buyer_email', 'N/A')
                print(f"     Email: {buyer_email}")
                
                # Verificar se o usuário existe
                user_exists = conn.execute("SELECT id FROM users WHERE email = ?", (buyer_email,)).fetchone()
                if user_exists:
                    print(f"     ✅ Usuário existe (ID: {user_exists['id']})")
                else:
                    print(f"     ❌ Usuário NÃO existe")
                    
            except:
                print(f"     ❌ Erro ao parsear payload")
        
        # Verificar licenças
        licenses = conn.execute("SELECT hotmart_purchase_id, user_id FROM licenses").fetchall()
        print(f"\n📊 Licenças existentes: {len(licenses)}")
        for license in licenses:
            print(f"   • {license['hotmart_purchase_id']} (User ID: {license['user_id']})")
            
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    debug_webhook()
