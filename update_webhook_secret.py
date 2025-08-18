#!/usr/bin/env python3
"""
Script para atualizar o webhook secret da Hotmart no banco de dados
Uso: python update_webhook_secret.py <webhook_secret>
"""

import sys
import sqlite3
import os

def update_webhook_secret(secret: str):
    """Atualiza o webhook secret no banco de dados"""
    db_path = os.path.join(os.getcwd(), "app.db")
    
    if not os.path.exists(db_path):
        print("❌ Banco de dados não encontrado. Execute o app.py primeiro.")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Verificar se existe configuração
        cursor.execute("SELECT COUNT(*) FROM hotmart_config")
        count = cursor.fetchone()[0]
        
        if count == 0:
            print("❌ Configuração da Hotmart não encontrada no banco.")
            return False
        
        # Atualizar webhook secret
        cursor.execute(
            "UPDATE hotmart_config SET webhook_secret = ?, updated_at = CURRENT_TIMESTAMP",
            (secret,)
        )
        
        conn.commit()
        conn.close()
        
        print("✅ Webhook secret atualizado com sucesso!")
        return True
        
    except Exception as e:
        print(f"❌ Erro ao atualizar webhook secret: {e}")
        return False

def main():
    if len(sys.argv) != 2:
        print("Uso: python update_webhook_secret.py <webhook_secret>")
        print("Exemplo: python update_webhook_secret.py abc123def456")
        sys.exit(1)
    
    secret = sys.argv[1].strip()
    
    if not secret:
        print("❌ Webhook secret não pode estar vazio")
        sys.exit(1)
    
    print("Atualizando webhook secret...")
    if update_webhook_secret(secret):
        print("✅ Configuração concluída!")
        print("\nPróximos passos:")
        print("1. Teste o webhook fazendo uma compra na Hotmart")
        print("2. Verifique se o webhook está sendo recebido em /webhook/hotmart")
        print("3. Teste o registro com o email da compra")
    else:
        print("❌ Falha na atualização")
        sys.exit(1)

if __name__ == "__main__":
    main()
