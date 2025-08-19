#!/usr/bin/env python3
"""
Script para testar o webhook com um usuário que existe no banco
"""

import requests
import json
from datetime import datetime

def test_webhook_with_existing_user():
    """Testa o webhook com um usuário que existe no banco"""
    
    # URL do webhook
    webhook_url = "http://localhost:8000/webhook/hotmart"
    
    # Dados simulados com um usuário que existe
    webhook_payload = {
        "event": "SALE_COMPLETED",
        "data": {
            "purchase_id": f"REAL-TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "product_id": "5974664",
            "buyer_email": "augustogumi@gmail.com",  # Usuário que existe no banco
            "purchase_date": datetime.now().isoformat(),
            "price": "287.00",
            "currency": "BRL",
            "status": "approved",
            "buyer_name": "Augusto Gumi",
            "buyer_document": "123.456.789-00"
        },
        "event_date": datetime.now().isoformat()
    }
    
    # Headers simulados
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'test-signature-real',
        'User-Agent': 'Hotmart-Webhook/1.0'
    }
    
    print("🔍 Testando webhook com usuário existente...")
    print(f"URL: {webhook_url}")
    print(f"Email: {webhook_payload['data']['buyer_email']}")
    print(f"Purchase ID: {webhook_payload['data']['purchase_id']}")
    print("-" * 50)
    
    try:
        # Enviar requisição POST para o webhook
        response = requests.post(
            webhook_url,
            json=webhook_payload,
            headers=headers,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook processado com sucesso!")
            
            # Verificar se a licença foi criada
            check_license_creation(webhook_payload['data']['purchase_id'])
            
        else:
            print("❌ Erro no processamento do webhook")
            
    except requests.exceptions.ConnectionError:
        print("❌ Erro de conexão. Certifique-se de que o servidor está rodando em http://localhost:8000")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")

def check_license_creation(purchase_id):
    """Verifica se a licença foi criada"""
    import sqlite3
    import os
    
    print("\n🔍 Verificando criação da licença...")
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar se a licença foi criada
        license = conn.execute("""
            SELECT l.hotmart_purchase_id, l.license_type, l.status, u.email
            FROM licenses l
            LEFT JOIN users u ON l.user_id = u.id
            WHERE l.hotmart_purchase_id = ?
        """, (purchase_id,)).fetchone()
        
        if license:
            print(f"✅ Licença criada com sucesso!")
            print(f"   • Purchase ID: {license['hotmart_purchase_id']}")
            print(f"   • Tipo: {license['license_type']}")
            print(f"   • Status: {license['status']}")
            print(f"   • Usuário: {license['email']}")
        else:
            print(f"❌ Licença NÃO foi criada para {purchase_id}")
            
        # Verificar se o webhook foi marcado como processado
        webhook = conn.execute("""
            SELECT event_type, hotmart_purchase_id, processed
            FROM hotmart_webhooks 
            WHERE hotmart_purchase_id = ?
        """, (purchase_id,)).fetchone()
        
        if webhook:
            print(f"📋 Webhook: {webhook['event_type']} - Processado: {webhook['processed']}")
            
    except Exception as e:
        print(f"❌ Erro ao verificar licença: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("TESTE DE WEBHOOK COM USUÁRIO EXISTENTE")
    print("=" * 60)
    test_webhook_with_existing_user()
