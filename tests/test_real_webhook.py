#!/usr/bin/env python3
"""
Script para testar com o formato real do webhook da Hotmart
"""

import requests
import json
from datetime import datetime

def test_real_webhook_format():
    """Testa o webhook com o formato real da Hotmart"""
    
    # URL do webhook
    webhook_url = "http://localhost:8000/webhook/hotmart"
    
    # Dados no formato real da Hotmart
    webhook_payload = {
        "id": "e1b99eab-668c-41fd-b169-4da986d7d12e",
        "creation_date": 1755543671489,
        "event": "PURCHASE_COMPLETE",
        "version": "2.0.0",
        "data": {
            "product": {
                "id": 5974664,  # ID do produto Leads Infinitos
                "ucode": "fb056612-bcc6-4217-9e6d-2a5d1110ac2f",
                "name": "Potencialize sua Prospecção com Extração de Leads do Google Maps",
                "warranty_date": "2017-12-27T00:00:00Z",
                "support_email": "support@hotmart.com.br",
                "has_co_production": False,
                "is_physical_product": False
            },
            "buyer": {
                "email": "augustogumi@gmail.com",  # Usuário que existe no banco
                "name": "Augusto Gumi",
                "first_name": "Augusto",
                "last_name": "Gumi",
                "checkout_phone_code": "999999999",
                "checkout_phone": "99999999900",
                "address": {
                    "city": "Curitiba",
                    "country": "Brasil",
                    "country_iso": "BR",
                    "state": "Paraná",
                    "neighborhood": "Centro",
                    "zipcode": "80000000",
                    "address": "Rua Teste",
                    "number": "123",
                    "complement": "Apto 1"
                },
                "document": "12345678900",
                "document_type": "CPF"
            },
            "purchase": {
                "approved_date": int(datetime.now().timestamp() * 1000),
                "full_price": {
                    "value": 287.00,  # Preço para licença anual
                    "currency_value": "BRL"
                },
                "price": {
                    "value": 287.00,
                    "currency_value": "BRL"
                },
                "checkout_country": {
                    "name": "Brasil",
                    "iso": "BR"
                },
                "order_bump": {
                    "is_order_bump": False
                },
                "event_tickets": {
                    "amount": int(datetime.now().timestamp() * 1000)
                },
                "original_offer_price": {
                    "value": 287.00,
                    "currency_value": "BRL"
                },
                "order_date": int(datetime.now().timestamp() * 1000),
                "status": "COMPLETED",
                "transaction": f"HP{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "payment": {
                    "installments_number": 1,
                    "type": "CREDIT_CARD"
                },
                "offer": {
                    "code": "test",
                    "coupon_code": None
                },
                "sckPaymentLink": "sckPaymentLinkTest",
                "is_funnel": False,
                "business_model": "I"
            }
        },
        "hottok": "7ULgSmd8ABlGrvIwJebPnbKEvA3Qut518b6463-fab8-418f-9a02-7af531711172"
    }
    
    # Headers simulados
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'real-signature-test',
        'User-Agent': 'Hotmart-Webhook/2.0'
    }
    
    print("🔍 Testando webhook com formato real da Hotmart...")
    print(f"URL: {webhook_url}")
    print(f"Event: {webhook_payload['event']}")
    print(f"Email: {webhook_payload['data']['buyer']['email']}")
    print(f"Transaction: {webhook_payload['data']['purchase']['transaction']}")
    print(f"Product ID: {webhook_payload['data']['product']['id']}")
    print(f"Price: R$ {webhook_payload['data']['purchase']['price']['value']}")
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
            check_license_creation(webhook_payload['data']['purchase']['transaction'])
            
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
    print("TESTE DE WEBHOOK COM FORMATO REAL DA HOTMART")
    print("=" * 60)
    test_real_webhook_format()
