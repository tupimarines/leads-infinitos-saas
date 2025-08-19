#!/usr/bin/env python3
"""
Script para testar o webhook da Hotmart
Simula o envio de dados de uma venda completada
"""

import requests
import json
from datetime import datetime

def test_webhook():
    """Testa o webhook da Hotmart com dados simulados"""
    
    # URL do webhook (ajuste conforme necessário)
    webhook_url = "http://localhost:8000/webhook/hotmart"
    
    # Dados simulados de uma venda completada
    webhook_payload = {
        "event": "SALE_COMPLETED",
        "data": {
            "purchase_id": f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "product_id": "5974664",
            "buyer_email": "teste@exemplo.com",
            "purchase_date": datetime.now().isoformat(),
            "price": "287.00",
            "currency": "BRL",
            "status": "approved",
            "buyer_name": "Usuário Teste",
            "buyer_document": "123.456.789-00"
        },
        "event_date": datetime.now().isoformat()
    }
    
    # Headers simulados (a assinatura real seria gerada pela Hotmart)
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'test-signature-12345',
        'User-Agent': 'Hotmart-Webhook/1.0'
    }
    
    print("🔍 Testando webhook da Hotmart...")
    print(f"URL: {webhook_url}")
    print(f"Payload: {json.dumps(webhook_payload, indent=2)}")
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
            
            # Verificar se os dados foram salvos no banco
            check_database()
            
        else:
            print("❌ Erro no processamento do webhook")
            
    except requests.exceptions.ConnectionError:
        print("❌ Erro de conexão. Certifique-se de que o servidor está rodando em http://localhost:8000")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")

def check_database():
    """Verifica se os dados do webhook foram salvos no banco"""
    import sqlite3
    import os
    
    print("\n🔍 Verificando banco de dados...")
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar webhooks recebidos
        webhooks = conn.execute("""
            SELECT event_type, hotmart_purchase_id, processed, created_at
            FROM hotmart_webhooks 
            ORDER BY created_at DESC 
            LIMIT 5
        """).fetchall()
        
        print(f"📊 Webhooks recebidos: {len(webhooks)}")
        for webhook in webhooks:
            print(f"✅ {webhook['event_type']} - {webhook['hotmart_purchase_id']} - Processado: {webhook['processed']}")
        
        # Verificar licenças criadas
        licenses = conn.execute("""
            SELECT l.hotmart_purchase_id, l.license_type, l.status, u.email
            FROM licenses l
            LEFT JOIN users u ON l.user_id = u.id
            ORDER BY l.created_at DESC 
            LIMIT 5
        """).fetchall()
        
        print(f"\n📊 Licenças criadas: {len(licenses)}")
        for license in licenses:
            user_email = license['email'] if license['email'] else "Usuário não registrado"
            print(f"✅ {license['hotmart_purchase_id']} - {license['license_type']} - {license['status']} - {user_email}")
            
    except Exception as e:
        print(f"❌ Erro ao verificar banco: {e}")
    finally:
        conn.close()

def test_different_scenarios():
    """Testa diferentes cenários de webhook"""
    
    scenarios = [
        {
            "name": "Venda Anual (R$ 287,00)",
            "payload": {
                "event": "SALE_COMPLETED",
                "data": {
                    "purchase_id": f"ANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "product_id": "5974664",
                    "buyer_email": "cliente.anual@exemplo.com",
                    "purchase_date": datetime.now().isoformat(),
                    "price": "287.00",
                    "currency": "BRL",
                    "status": "approved"
                }
            }
        },
        {
            "name": "Venda Semestral (R$ 147,00)",
            "payload": {
                "event": "SALE_COMPLETED",
                "data": {
                    "purchase_id": f"SEMESTRAL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "product_id": "5974664",
                    "buyer_email": "cliente.semestral@exemplo.com",
                    "purchase_date": datetime.now().isoformat(),
                    "price": "147.00",
                    "currency": "BRL",
                    "status": "approved"
                }
            }
        },
        {
            "name": "Venda Cancelada",
            "payload": {
                "event": "SALE_CANCELLED",
                "data": {
                    "purchase_id": f"CANCEL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "product_id": "5974664",
                    "buyer_email": "cliente.cancelado@exemplo.com",
                    "purchase_date": datetime.now().isoformat(),
                    "price": "287.00",
                    "currency": "BRL",
                    "status": "cancelled"
                }
            }
        }
    ]
    
    webhook_url = "http://localhost:8000/webhook/hotmart"
    headers = {
        'Content-Type': 'application/json',
        'X-Hotmart-Signature': 'test-signature-12345'
    }
    
    print("\n🧪 Testando diferentes cenários...")
    
    for scenario in scenarios:
        print(f"\n📋 Testando: {scenario['name']}")
        print("-" * 40)
        
        try:
            response = requests.post(
                webhook_url,
                json=scenario['payload'],
                headers=headers,
                timeout=10
            )
            
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text[:100]}...")
            
            if response.status_code == 200:
                print("✅ Sucesso")
            else:
                print("❌ Falha")
                
        except Exception as e:
            print(f"❌ Erro: {e}")

def main():
    print("=" * 60)
    print("TESTE DE WEBHOOK - HOTMART")
    print("=" * 60)
    
    # Teste básico
    test_webhook()
    
    # Teste de diferentes cenários
    test_different_scenarios()
    
    print("\n" + "=" * 60)
    print("TESTE CONCLUÍDO")
    print("=" * 60)
    print("\n📝 Próximos passos:")
    print("1. Verifique os logs do servidor para detalhes")
    print("2. Confirme se as licenças foram criadas corretamente")
    print("3. Teste o registro de usuários com os emails dos webhooks")
    print("4. Verifique se as licenças estão sendo validadas corretamente")

if __name__ == "__main__":
    main()
