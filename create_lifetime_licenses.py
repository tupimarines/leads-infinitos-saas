#!/usr/bin/env python3
"""
Script para criar licenças vitalícias para todos os usuários existentes
Execute este script uma vez para migrar usuários antigos
"""

import sqlite3
import os
from datetime import datetime, timedelta

def get_db_connection():
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def create_lifetime_licenses():
    """Cria licenças vitalícias para todos os usuários existentes"""
    conn = get_db_connection()
    
    try:
        # Buscar todos os usuários
        users = conn.execute("SELECT id, email FROM users").fetchall()
        
        if not users:
            print("❌ Nenhum usuário encontrado na base de dados.")
            return False
        
        print(f"📋 Encontrados {len(users)} usuários na base de dados.")
        
        # Para cada usuário, verificar se já tem licença
        licenses_created = 0
        for user in users:
            # Verificar se já existe licença para este usuário
            existing_license = conn.execute(
                "SELECT id FROM licenses WHERE user_id = ?", 
                (user['id'],)
            ).fetchone()
            
            if existing_license:
                print(f"⚠️  Usuário {user['email']} já possui licença. Pulando...")
                continue
            
            # Criar licença vitalícia (expira em 50 anos)
            expires_at = datetime.now() + timedelta(days=365*50)
            
            conn.execute(
                """
                INSERT INTO licenses 
                (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user['id'],
                    f"LIFETIME-{user['id']}-{datetime.now().strftime('%Y%m%d')}",
                    '5974664',
                    'anual',  # Tipo anual mas com expiração muito longa
                    datetime.now().isoformat(),
                    expires_at.isoformat()
                )
            )
            
            licenses_created += 1
            print(f"✅ Licença vitalícia criada para {user['email']}")
        
        conn.commit()
        print(f"\n🎉 Processo concluído!")
        print(f"📊 Licenças criadas: {licenses_created}")
        print(f"📊 Usuários processados: {len(users)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Erro ao criar licenças: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("CRIAÇÃO DE LICENÇAS VITALÍCIAS")
    print("=" * 60)
    print("Este script criará licenças vitalícias para todos os usuários existentes.")
    print("⚠️  ATENÇÃO: Execute apenas uma vez!")
    print()
    
    # Verificar se o banco existe
    db_path = os.path.join(os.getcwd(), "app.db")
    if not os.path.exists(db_path):
        print("❌ Banco de dados não encontrado. Execute o app.py primeiro.")
        return
    
    # Confirmar execução
    response = input("Deseja continuar? (s/N): ").strip().lower()
    if response not in ['s', 'sim', 'y', 'yes']:
        print("❌ Operação cancelada.")
        return
    
    print("\n🔄 Criando licenças vitalícias...")
    
    if create_lifetime_licenses():
        print("\n✅ Migração concluída com sucesso!")
        print("\nPróximos passos:")
        print("1. Teste o login com um usuário existente")
        print("2. Verifique se a licença aparece em /licenses")
        print("3. Teste se consegue usar o scraper")
    else:
        print("\n❌ Falha na migração.")

if __name__ == "__main__":
    main()
