#!/usr/bin/env python3
"""
Script para consultar extrações realizadas ontem no banco de dados
"""

import sqlite3
import json
from datetime import datetime, timedelta
import os

def get_db_connection():
    """Conecta ao banco de dados"""
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def consultar_extracao_ontem():
    """Consulta extrações realizadas ontem"""
    conn = get_db_connection()
    
    try:
        # Calcular data de ontem
        ontem = datetime.now() - timedelta(days=1)
        data_ontem = ontem.strftime('%Y-%m-%d')
        
        print(f"🔍 Consultando extrações realizadas em: {data_ontem}")
        print("=" * 60)
        
        # Consultar jobs de ontem
        query = """
        SELECT 
            sj.id,
            sj.user_id,
            u.email,
            sj.keyword,
            sj.locations,
            sj.total_results,
            sj.status,
            sj.progress,
            sj.current_location,
            sj.results_path,
            sj.error_message,
            sj.started_at,
            sj.completed_at,
            sj.created_at
        FROM scraping_jobs sj
        JOIN users u ON sj.user_id = u.id
        WHERE DATE(sj.created_at) = ?
        ORDER BY sj.created_at DESC
        """
        
        rows = conn.execute(query, (data_ontem,)).fetchall()
        
        if not rows:
            print("❌ Nenhuma extração encontrada para ontem.")
            return
        
        print(f"✅ Encontradas {len(rows)} extração(ões) de ontem:")
        print()
        
        for i, job in enumerate(rows, 1):
            print(f"📊 Extração #{i}")
            print(f"   ID: {job['id']}")
            print(f"   Usuário: {job['email']} (ID: {job['user_id']})")
            print(f"   Palavra-chave: {job['keyword']}")
            
            # Parse locations
            try:
                locations = json.loads(job['locations']) if job['locations'] else []
                print(f"   Localizações: {', '.join(locations)}")
            except:
                print(f"   Localizações: {job['locations']}")
            
            print(f"   Total de resultados: {job['total_results']}")
            print(f"   Status: {job['status']}")
            print(f"   Progresso: {job['progress']}%")
            
            if job['current_location']:
                print(f"   Localização atual: {job['current_location']}")
            
            if job['results_path']:
                print(f"   Arquivo de resultados: {job['results_path']}")
            
            if job['error_message']:
                print(f"   Erro: {job['error_message']}")
            
            print(f"   Criado em: {job['created_at']}")
            if job['started_at']:
                print(f"   Iniciado em: {job['started_at']}")
            if job['completed_at']:
                print(f"   Concluído em: {job['completed_at']}")
            
            print("-" * 40)
        
        # Estatísticas resumidas
        print("\n📈 Estatísticas de ontem:")
        status_count = {}
        for job in rows:
            status = job['status']
            status_count[status] = status_count.get(status, 0) + 1
        
        for status, count in status_count.items():
            print(f"   {status}: {count} extração(ões)")
        
        # Verificar se há arquivos de resultados
        print("\n📁 Arquivos de resultados encontrados:")
        resultados_encontrados = 0
        for job in rows:
            if job['results_path'] and os.path.exists(job['results_path']):
                file_size = os.path.getsize(job['results_path'])
                print(f"   ✅ {job['results_path']} ({file_size} bytes)")
                resultados_encontrados += 1
            elif job['results_path']:
                print(f"   ❌ {job['results_path']} (arquivo não encontrado)")
        
        if resultados_encontrados == 0:
            print("   Nenhum arquivo de resultado encontrado.")
        
    except Exception as e:
        print(f"❌ Erro ao consultar banco de dados: {e}")
    finally:
        conn.close()

def consultar_extracao_por_data(data_str):
    """Consulta extrações por data específica (formato: YYYY-MM-DD)"""
    conn = get_db_connection()
    
    try:
        print(f"🔍 Consultando extrações realizadas em: {data_str}")
        print("=" * 60)
        
        # Consultar jobs da data específica
        query = """
        SELECT 
            sj.id,
            sj.user_id,
            u.email,
            sj.keyword,
            sj.locations,
            sj.total_results,
            sj.status,
            sj.progress,
            sj.current_location,
            sj.results_path,
            sj.error_message,
            sj.started_at,
            sj.completed_at,
            sj.created_at
        FROM scraping_jobs sj
        JOIN users u ON sj.user_id = u.id
        WHERE DATE(sj.created_at) = ?
        ORDER BY sj.created_at DESC
        """
        
        rows = conn.execute(query, (data_str,)).fetchall()
        
        if not rows:
            print(f"❌ Nenhuma extração encontrada para {data_str}.")
            return
        
        print(f"✅ Encontradas {len(rows)} extração(ões) em {data_str}:")
        print()
        
        for i, job in enumerate(rows, 1):
            print(f"📊 Extração #{i}")
            print(f"   ID: {job['id']}")
            print(f"   Usuário: {job['email']} (ID: {job['user_id']})")
            print(f"   Palavra-chave: {job['keyword']}")
            
            # Parse locations
            try:
                locations = json.loads(job['locations']) if job['locations'] else []
                print(f"   Localizações: {', '.join(locations)}")
            except:
                print(f"   Localizações: {job['locations']}")
            
            print(f"   Total de resultados: {job['total_results']}")
            print(f"   Status: {job['status']}")
            print(f"   Progresso: {job['progress']}%")
            
            if job['current_location']:
                print(f"   Localização atual: {job['current_location']}")
            
            if job['results_path']:
                print(f"   Arquivo de resultados: {job['results_path']}")
            
            if job['error_message']:
                print(f"   Erro: {job['error_message']}")
            
            print(f"   Criado em: {job['created_at']}")
            if job['started_at']:
                print(f"   Iniciado em: {job['started_at']}")
            if job['completed_at']:
                print(f"   Concluído em: {job['completed_at']}")
            
            print("-" * 40)
        
    except Exception as e:
        print(f"❌ Erro ao consultar banco de dados: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Se uma data foi fornecida como argumento
        data_especifica = sys.argv[1]
        consultar_extracao_por_data(data_especifica)
    else:
        # Consultar extrações de ontem
        consultar_extracao_ontem()
    
    print("\n💡 Dica: Para consultar uma data específica, use:")
    print("   python consultar_extracao_ontem.py 2025-01-15")
