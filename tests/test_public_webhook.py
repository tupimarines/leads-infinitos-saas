#!/usr/bin/env python3
"""
Script para testar webhook com URL pública (ngrok)
"""

import requests
import json
from datetime import datetime

def test_public_webhook():
    """Testa webhook com URL pública"""
    
    # Solicitar URL do usuário
    webhook_url = input("Digite a URL do webhook (ex: https://abc123.ngrok.io/webhook/hotmart): ").strip()
    
    if not webhook_url:
        print("❌ URL não fornecida")
        return
    
    # Dados do webhook
    webhook_payload = {
        "id": "test-public-webhook",
        "creation_date": int(datetime.now().timestamp() * 1000),
        "event": "PURCHASE_COMPLETE",
        "version": "2.0.0",
        "data": {
            "product": {
                "id": 5974664,
                "name": "Potencialize sua Prospecção com Extração de Leads do Google Maps"
            },
            "buyer": {
                "email": "teste.publico@exemplo.com",
                "name": "Usuário Público"
            },
            "purchase": {
                "approved_date": int(datetime.now().timestamp() * 1000),
                "price": {
                    "value": 287.00,
                    "currency_value": "BRL"
                },
                "status": "COMPLETED",
                "transaction": f"HP-PUBLIC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            }
        },
        "hottok": "test-public-hottok"
    }
    
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'test-signature'
    }
    
    print(f"🔍 Testando webhook público...")
    print(f"URL: {webhook_url}")
    print(f"Transaction: {webhook_payload['data']['purchase']['transaction']}")
    print("-" * 50)
    
    try:
        response = requests.post(
            webhook_url,
            json=webhook_payload,
            headers=headers,
            timeout=30
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook processado com sucesso!")
        else:
            print("❌ Erro no processamento do webhook")
            
    except Exception as e:
        print(f"❌ Erro: {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("TESTE DE WEBHOOK PÚBLICO")
    print("=" * 60)
    test_public_webhook()
