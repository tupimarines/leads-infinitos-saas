#!/usr/bin/env python3
"""
Script para configurar webhook da Hotmart
Execute este script para obter as instruções de configuração
"""

import os
import requests
import base64
from datetime import datetime

def get_webhook_url():
    """Retorna a URL do webhook baseada no ambiente"""
    # Em produção, você deve usar sua URL real
    # Por exemplo: https://seudominio.com/webhook/hotmart
    return "http://localhost:8000/webhook/hotmart"

def test_hotmart_connection():
    """Testa a conexão com a API da Hotmart"""
    client_id = "cb6bcde6-24cd-464f-80f3-e4efce3f048c"
    client_secret = "7ee4a93d-1aec-473b-a8e6-1d0a813382e2"
    
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    
    headers = {
        'Authorization': f'Basic {encoded}',
        'Content-Type': 'application/json'
    }
    
    try:
        # Testar endpoint de produtos
        response = requests.get(
            "https://developers.hotmart.com/payments/api/v1/products",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Conexão com Hotmart API estabelecida com sucesso!")
            return True
        else:
            print(f"❌ Erro na conexão: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao conectar com Hotmart: {e}")
        return False

def main():
    print("=" * 60)
    print("CONFIGURAÇÃO DO WEBHOOK HOTMART")
    print("=" * 60)
    
    # Testar conexão
    print("\n1. Testando conexão com Hotmart API...")
    if not test_hotmart_connection():
        print("❌ Falha na conexão. Verifique suas credenciais.")
        return
    
    # Instruções para configurar webhook
    print("\n2. CONFIGURAÇÃO DO WEBHOOK:")
    print("-" * 40)
    print(f"URL do Webhook: {get_webhook_url()}")
    print("\nSiga estes passos:")
    print("1. Acesse: https://developers.hotmart.com/webhooks")
    print("2. Clique em 'Criar Webhook'")
    print("3. Configure:")
    print(f"   - Nome: Leads Infinitos Webhook")
    print(f"   - URL: {get_webhook_url()}")
    print("   - Versão: 2.0.0 (Recomendado)")
    print("   - Eventos: SALE_COMPLETED")
    print("4. Salve o webhook")
    print("5. Copie o 'Hottok de verificação' (webhook secret)")
    
    # Instruções para atualizar configuração
    print("\n3. ATUALIZAR CONFIGURAÇÃO NO BANCO:")
    print("-" * 40)
    print("Após criar o webhook, execute:")
    print("python update_webhook_secret.py <SEU_WEBHOOK_SECRET>")
    
    # Instruções para testar
    print("\n4. TESTE A INTEGRAÇÃO:")
    print("-" * 40)
    print("1. Faça uma compra de teste na Hotmart")
    print("2. Tente se registrar com o email da compra")
    print("3. Verifique se a licença foi criada automaticamente")
    
    print("\n" + "=" * 60)
    print("Configuração concluída!")
    print("=" * 60)

if __name__ == "__main__":
    main()
