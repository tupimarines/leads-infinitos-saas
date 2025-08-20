#!/usr/bin/env python3
"""
Script para testar webhook da Hubla
"""

import requests
import json
import sqlite3
import os
from datetime import datetime

def test_hubla_webhook():
    """Testa webhook da Hubla com dados simulados"""
    print("🧪 Testando Webhook da Hubla")
    print("=" * 50)
    
    # URL do webhook (ajuste conforme necessário)
    webhook_url = "http://localhost:8000/webhook/hubla"
    
    # Dados simulados de uma compra da Hubla
    # Baseado na documentação da Hubla, estrutura pode variar
    test_payload = {
        "event": "purchase.completed",
        "data": {
            "purchase": {
                "id": f"hubla-test-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "created_at": datetime.now().isoformat(),
                "approved_at": datetime.now().isoformat(),
                "price": {
                    "value": 297.00,
                    "currency": "BRL"
                }
            },
            "buyer": {
                "email": "teste@hubla.com",
                "name": "Cliente Teste Hubla"
            },
            "product": {
                "id": "12345",
                "name": "Leads Infinitos"
            }
        }
    }
    
    # Headers simulando Hubla
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer your-hubla-webhook-token',
        'User-Agent': 'Hubla-Webhook/1.0'
    }
    
    print(f"📤 Enviando webhook para: {webhook_url}")
    print(f"📋 Payload: {json.dumps(test_payload, indent=2)}")
    print(f"🔑 Headers: {json.dumps(headers, indent=2)}")
    
    try:
        # Enviar webhook
        response = requests.post(
            webhook_url,
            json=test_payload,
            headers=headers,
            timeout=10
        )
        
        print(f"\n📥 Resposta:")
        print(f"Status: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Body: {response.text}")
        
        if response.status_code == 200:
            print("✅ Webhook processado com sucesso!")
        else:
            print("❌ Erro no processamento do webhook")
            
    except Exception as e:
        print(f"❌ Erro ao enviar webhook: {e}")
    
    # Verificar se foi salvo no banco
    print("\n🔍 Verificando banco de dados...")
    check_database()

def test_member_added_v2():
    """Testa evento v2 customer.member_added (Acesso concedido) criando usuário automaticamente"""
    print("\n🧪 Testando Hubla v2: customer.member_added (Acesso concedido)")
    print("=" * 50)
    webhook_url = "http://localhost:8000/webhook/hubla"

    payload = {
        "type": "customer.member_added",
        "event": {
            "product": {
                "id": "prod_abc123",
                "name": "Leads Infinitos"
            },
            "subscription": {
                "id": f"sub-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "type": "recurring",
                "status": "active",
                "activatedAt": datetime.utcnow().isoformat() + "Z"
            },
            "user": {
                "email": "member.added@example.com",
                "firstName": "Member",
                "lastName": "Added"
            }
        },
        "version": "2.0.0"
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer your-hubla-webhook-token',
        'User-Agent': 'Hubla-Webhook/2.0'
    }

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")
    except Exception as e:
        print(f"❌ Erro ao enviar webhook v2: {e}")

    print("\n🔍 Verificando banco de dados para usuário criado via v2...")
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        u = conn.execute("SELECT * FROM users WHERE email=?", ("member.added@example.com",)).fetchone()
        print("Usuário criado?", bool(u))
    finally:
        conn.close()

def check_database():
    """Verifica se o webhook foi salvo no banco"""
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar webhooks da Hubla
        webhooks = conn.execute(
            "SELECT * FROM hubla_webhooks ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        
        print(f"📊 Webhooks da Hubla encontrados: {len(webhooks)}")
        
        for webhook in webhooks:
            created_date = datetime.fromisoformat(webhook['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"   • {webhook['event_type']} - {webhook['hubla_purchase_id']} - Processado: {webhook['processed']} - {created_date}")
        
        # Verificar licenças
        licenses = conn.execute(
            "SELECT * FROM licenses ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        
        print(f"\n📋 Licenças encontradas: {len(licenses)}")
        
        for license in licenses:
            created_date = datetime.fromisoformat(license['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"   • {license['license_type']} - {license['hotmart_purchase_id']} - Status: {license['status']} - {created_date}")
        
        # Verificar usuários
        users = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        
        print(f"\n👥 Usuários encontrados: {len(users)}")
        
        for user in users:
            created_date = datetime.fromisoformat(user['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"   • {user['email']} - {created_date}")
            
    except Exception as e:
        print(f"❌ Erro ao verificar banco: {e}")
    finally:
        conn.close()

def test_different_payloads():
    """Testa diferentes formatos de payload da Hubla"""
    print("\n🔄 Testando diferentes formatos de payload...")
    
    webhook_url = "http://localhost:8000/webhook/hubla"
    
    # Formato alternativo 1
    payload1 = {
        "event": "subscription.created",
        "purchase": {
            "id": f"hubla-sub-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "created_at": datetime.now().isoformat()
        },
        "customer": {
            "email": "cliente@hubla.com"
        },
        "product": {
            "id": "67890"
        }
    }
    
    # Formato alternativo 2
    payload2 = {
        "event": "purchase.approved",
        "data": {
            "transaction_id": f"hubla-txn-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "user": {
                "email": "usuario@hubla.com"
            },
            "product": {
                "id": "11111"
            },
            "amount": 297.00
        }
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer your-hubla-webhook-token'
    }
    
    for i, payload in enumerate([payload1, payload2], 1):
        print(f"\n📤 Teste {i}: {payload['event']}")
        try:
            response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                print("✅ Sucesso")
            else:
                print(f"❌ Erro: {response.text}")
        except Exception as e:
            print(f"❌ Erro: {e}")

if __name__ == "__main__":
    test_hubla_webhook()
    test_different_payloads()
    test_member_added_v2()
