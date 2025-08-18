#!/usr/bin/env python3
"""
Script para inicializar o banco de dados com as novas tabelas
"""

import sqlite3
import os

def init_db():
    """Inicializa o banco de dados com todas as tabelas necessárias"""
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Tabela de usuários (já existente)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        
        # Tabela de licenças
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                hotmart_purchase_id TEXT UNIQUE NOT NULL,
                hotmart_product_id TEXT NOT NULL,
                license_type TEXT NOT NULL CHECK (license_type IN ('semestral', 'anual')),
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'cancelled')),
                purchase_date TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            """
        )
        
        # Tabela de webhooks da Hotmart
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hotmart_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                hotmart_purchase_id TEXT,
                payload TEXT NOT NULL,
                processed BOOLEAN DEFAULT FALSE,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        
        # Tabela de configurações da Hotmart
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hotmart_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                client_secret TEXT NOT NULL,
                webhook_secret TEXT,
                product_id TEXT NOT NULL,
                sandbox_mode BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        
        # Inserir configuração inicial da Hotmart se não existir
        conn.execute(
            """
            INSERT OR IGNORE INTO hotmart_config 
            (client_id, client_secret, product_id, sandbox_mode) 
            VALUES (?, ?, ?, ?)
            """,
            ('cb6bcde6-24cd-464f-80f3-e4efce3f048c', '7ee4a93d-1aec-473b-a8e6-1d0a813382e2', '5974664', True)
        )
        
        conn.commit()
        print("✅ Banco de dados inicializado com sucesso!")
        return True
        
    except Exception as e:
        print(f"❌ Erro ao inicializar banco: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔄 Inicializando banco de dados...")
    init_db()
