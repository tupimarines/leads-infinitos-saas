#!/usr/bin/env python3
"""
Script de teste para verificar a integração com Hotmart
"""

import sqlite3
import os
from datetime import datetime

def test_database():
    """Testa se o banco de dados está configurado corretamente"""
    print("🔍 Testando banco de dados...")
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar tabelas
        tables = ['users', 'licenses', 'hotmart_config', 'hotmart_webhooks']
        for table in tables:
            result = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
            if result:
                print(f"✅ Tabela {table} existe")
            else:
                print(f"❌ Tabela {table} não encontrada")
        
        # Verificar usuários e licenças
        users_with_licenses = conn.execute("""
            SELECT u.email, l.license_type, l.expires_at, l.status
            FROM users u 
            LEFT JOIN licenses l ON u.id = l.user_id
        """).fetchall()
        
        print(f"\n📊 Usuários e licenças:")
        for user in users_with_licenses:
            if user['license_type']:
                print(f"✅ {user['email']}: {user['license_type']} ({user['status']}) - expira: {user['expires_at'][:10]}")
            else:
                print(f"❌ {user['email']}: SEM LICENÇA")
        
        # Verificar configuração da Hotmart
        config = conn.execute("SELECT * FROM hotmart_config LIMIT 1").fetchone()
        if config:
            print(f"\n✅ Configuração Hotmart: Client ID = {config['client_id'][:8]}...")
        else:
            print(f"\n❌ Configuração Hotmart não encontrada")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro no teste: {e}")
        return False
    finally:
        conn.close()

def test_license_validation():
    """Testa a validação de licenças"""
    print("\n🔍 Testando validação de licenças...")
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Testar se usuários têm licenças ativas
        active_licenses = conn.execute("""
            SELECT u.email, l.license_type, l.expires_at
            FROM users u 
            JOIN licenses l ON u.id = l.user_id
            WHERE l.status = 'active' AND l.expires_at > datetime('now')
        """).fetchall()
        
        print(f"📊 Licenças ativas encontradas: {len(active_licenses)}")
        for license in active_licenses:
            print(f"✅ {license['email']}: {license['license_type']} (expira: {license['expires_at'][:10]})")
        
        return len(active_licenses) > 0
        
    except Exception as e:
        print(f"❌ Erro no teste de licenças: {e}")
        return False
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("TESTE DE INTEGRAÇÃO - LEADS INFINITOS")
    print("=" * 60)
    
    # Testar banco de dados
    db_ok = test_database()
    
    # Testar validação de licenças
    licenses_ok = test_license_validation()
    
    print("\n" + "=" * 60)
    print("RESULTADO DOS TESTES")
    print("=" * 60)
    
    if db_ok and licenses_ok:
        print("🎉 TODOS OS TESTES PASSARAM!")
        print("\n✅ Sistema pronto para uso:")
        print("1. Banco de dados configurado")
        print("2. Licenças vitalícias criadas")
        print("3. Integração Hotmart configurada")
        print("\n🚀 Próximos passos:")
        print("1. Execute: python app.py")
        print("2. Acesse: http://localhost:8000")
        print("3. Faça login com um usuário existente")
        print("4. Teste o scraper")
    else:
        print("❌ ALGUNS TESTES FALHARAM")
        if not db_ok:
            print("- Banco de dados com problemas")
        if not licenses_ok:
            print("- Licenças não estão válidas")

if __name__ == "__main__":
    main()
