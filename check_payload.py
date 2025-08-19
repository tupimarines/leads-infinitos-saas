#!/usr/bin/env python3
"""
Script para verificar o payload real dos webhooks
"""

import sqlite3
import os

def check_payload():
    """Verifica o payload real dos webhooks"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        print("🔍 VERIFICANDO PAYLOAD DOS WEBHOOKS")
        print("=" * 60)
        
        # Verificar webhooks SALE_COMPLETED
        webhooks = conn.execute("""
            SELECT event_type, hotmart_purchase_id, payload
            FROM hotmart_webhooks 
            WHERE event_type = 'SALE_COMPLETED'
            ORDER BY created_at DESC 
            LIMIT 2
        """).fetchall()
        
        for i, webhook in enumerate(webhooks, 1):
            print(f"\n📋 Webhook {i}: {webhook['hotmart_purchase_id']}")
            print("-" * 40)
            print(f"Payload (primeiros 200 chars):")
            print(webhook['payload'][:200])
            print("...")
            
            # Tentar fazer parse
            import json
            try:
                payload = json.loads(webhook['payload'])
                print(f"✅ Parse JSON bem-sucedido!")
                print(f"Event: {payload.get('event')}")
                print(f"Buyer Email: {payload.get('data', {}).get('buyer_email')}")
                print(f"Price: {payload.get('data', {}).get('price')}")
            except json.JSONDecodeError as e:
                print(f"❌ Erro no parse JSON: {e}")
            except Exception as e:
                print(f"❌ Erro inesperado: {e}")
            
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_payload()
