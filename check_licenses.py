#!/usr/bin/env python3
"""
Script para verificar licenças no banco de dados
"""

import sqlite3
import os

def check_licenses():
    """Verifica as licenças no banco de dados"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar licenças criadas
        licenses = conn.execute("""
            SELECT l.hotmart_purchase_id, l.license_type, l.status, l.expires_at, u.email
            FROM licenses l
            LEFT JOIN users u ON l.user_id = u.id
            ORDER BY l.created_at DESC 
            LIMIT 10
        """).fetchall()
        
        print(f"📊 Licenças encontradas: {len(licenses)}")
        print("-" * 80)
        
        for license in licenses:
            user_email = license['email'] if license['email'] else "Usuário não registrado"
            expires_date = license['expires_at'][:10] if license['expires_at'] else "N/A"
            print(f"✅ {license['hotmart_purchase_id']} - {license['license_type']} - {license['status']} - Expira: {expires_date} - {user_email}")
        
        # Verificar webhooks recebidos
        webhooks = conn.execute("""
            SELECT event_type, hotmart_purchase_id, processed, created_at
            FROM hotmart_webhooks 
            ORDER BY created_at DESC 
            LIMIT 5
        """).fetchall()
        
        print(f"\n📊 Webhooks recebidos: {len(webhooks)}")
        print("-" * 80)
        
        for webhook in webhooks:
            created_date = webhook['created_at'][:19] if webhook['created_at'] else "N/A"
            print(f"✅ {webhook['event_type']} - {webhook['hotmart_purchase_id']} - Processado: {webhook['processed']} - {created_date}")
            
    except Exception as e:
        print(f"❌ Erro ao verificar banco: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("VERIFICAÇÃO DE LICENÇAS E WEBHOOKS")
    print("=" * 60)
    check_licenses()
