#!/usr/bin/env python3
"""
Script para verificar extrações recentes no banco de dados
"""

import sqlite3
import os
from datetime import datetime, timedelta

def verificar_extracao_recente():
    """Verifica as extrações mais recentes"""
    conn = sqlite3.connect('app.db')
    conn.row_factory = sqlite3.Row
    
    try:
        # Verificar as últimas 10 extrações
        query = """
        SELECT 
            sj.id,
            sj.user_id,
            u.email,
            sj.keyword,
            sj.status,
            sj.created_at,
            sj.results_path
        FROM scraping_jobs sj
        JOIN users u ON sj.user_id = u.id
        ORDER BY sj.created_at DESC
        LIMIT 10
        """
        
        rows = conn.execute(query).fetchall()
        print('📊 Últimas 10 extrações no sistema:')
        print('=' * 50)
        
        if not rows:
            print('❌ Nenhuma extração encontrada no sistema.')
        else:
            for i, job in enumerate(rows, 1):
                print(f'{i}. ID: {job["id"]} | Usuário: {job["email"]} | Status: {job["status"]} | Data: {job["created_at"]}')
        
        # Verificar extrações dos últimos 7 dias
        print('\n📅 Extrações dos últimos 7 dias:')
        print('=' * 50)
        
        query_recentes = """
        SELECT 
            DATE(sj.created_at) as data,
            COUNT(*) as total,
            GROUP_CONCAT(DISTINCT sj.status) as status_list
        FROM scraping_jobs sj
        WHERE sj.created_at >= date('now', '-7 days')
        GROUP BY DATE(sj.created_at)
        ORDER BY data DESC
        """
        
        rows_recentes = conn.execute(query_recentes).fetchall()
        
        if not rows_recentes:
            print('❌ Nenhuma extração encontrada nos últimos 7 dias.')
        else:
            for row in rows_recentes:
                print(f'📅 {row["data"]}: {row["total"]} extração(ões) - Status: {row["status_list"]}')
        
    except Exception as e:
        print(f'❌ Erro ao consultar banco de dados: {e}')
    finally:
        conn.close()

if __name__ == "__main__":
    verificar_extracao_recente()
