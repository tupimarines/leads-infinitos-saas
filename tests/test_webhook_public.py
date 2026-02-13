#!/usr/bin/env python3
"""
Script para testar o webhook da Hotmart usando URL pública
Útil quando o app está rodando com ngrok ou em produção
"""

import requests
import json
from datetime import datetime
import sys

def test_webhook_public(webhook_url):
    """Testa o webhook da Hotmart com URL pública"""
    
    # Dados simulados de uma venda completada
    webhook_payload = {
        "event": "SALE_COMPLETED",
        "data": {
            "purchase_id": f"PUBLIC-TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "product_id": "5974664",
            "buyer_email": "teste.publico@exemplo.com",
            "purchase_date": datetime.now().isoformat(),
            "price": "287.00",
            "currency": "BRL",
            "status": "approved",
            "buyer_name": "Cliente Público",
            "buyer_document": "987.654.321-00"
        },
        "event_date": datetime.now().isoformat()
    }
    
    # Headers simulados
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'test-signature-public',
        'User-Agent': 'Hotmart-Webhook/1.0'
    }
    
    print("🔍 Testando webhook público da Hotmart...")
    print(f"URL: {webhook_url}")
    print(f"Payload: {json.dumps(webhook_payload, indent=2)}")
    print("-" * 50)
    
    try:
        # Enviar requisição POST para o webhook
        response = requests.post(
            webhook_url,
            json=webhook_payload,
            headers=headers,
            timeout=30  # Timeout maior para URLs públicas
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook processado com sucesso!")
            return True
        else:
            print("❌ Erro no processamento do webhook")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Erro de conexão. Verifique se a URL está correta e acessível")
        return False
    except requests.exceptions.Timeout:
        print("❌ Timeout. A requisição demorou muito para responder")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return False

def test_webhook_with_real_data():
    """Testa com dados mais realistas baseados na documentação da Hotmart"""
    
    # URL do webhook (substitua pela sua URL real)
    webhook_url = input("Digite a URL do webhook (ex: https://seu-dominio.com/webhook/hotmart): ").strip()
    
    if not webhook_url:
        print("❌ URL não fornecida")
        return
    
    # Dados mais realistas baseados na documentação da Hotmart
    real_payload = {
        "event": "SALE_COMPLETED",
        "data": {
            "purchase_id": f"HM{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "product_id": "5974664",
            "buyer_email": "cliente.real@exemplo.com",
            "purchase_date": datetime.now().isoformat(),
            "price": "287.00",
            "currency": "BRL",
            "status": "approved",
            "buyer_name": "João Silva",
            "buyer_document": "123.456.789-00",
            "payment_type": "credit_card",
            "installments": 1,
            "commission_value": "28.70",
            "affiliate": {
                "name": "Afiliado Teste",
                "email": "afiliado@exemplo.com"
            }
        },
        "event_date": datetime.now().isoformat(),
        "webhook_id": f"webhook_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    }
    
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'real-signature-test',
        'User-Agent': 'Hotmart-Webhook/2.0'
    }
    
    print(f"\n🧪 Testando com dados realistas...")
    print(f"URL: {webhook_url}")
    print(f"Payload: {json.dumps(real_payload, indent=2)}")
    print("-" * 50)
    
    try:
        response = requests.post(
            webhook_url,
            json=real_payload,
            headers=headers,
            timeout=30
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook processado com sucesso!")
            print("\n📝 Próximos passos:")
            print("1. Verifique se a licença foi criada no banco de dados")
            print("2. Teste o registro do usuário com o email: cliente.real@exemplo.com")
            print("3. Confirme se a licença está sendo validada corretamente")
        else:
            print("❌ Erro no processamento do webhook")
            
    except Exception as e:
        print(f"❌ Erro: {e}")

def main():
    print("=" * 60)
    print("TESTE DE WEBHOOK PÚBLICO - HOTMART")
    print("=" * 60)
    
    print("\nEscolha uma opção:")
    print("1. Teste com URL fornecida")
    print("2. Teste com dados realistas")
    print("3. Sair")
    
    choice = input("\nDigite sua escolha (1-3): ").strip()
    
    if choice == "1":
        webhook_url = input("Digite a URL do webhook: ").strip()
        if webhook_url:
            test_webhook_public(webhook_url)
        else:
            print("❌ URL não fornecida")
    
    elif choice == "2":
        test_webhook_with_real_data()
    
    elif choice == "3":
        print("Saindo...")
        sys.exit(0)
    
    else:
        print("❌ Opção inválida")

if __name__ == "__main__":
    main()
