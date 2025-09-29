#!/usr/bin/env python3
"""
Script para analisar e consultar extrações do banco de dados
"""

import sqlite3
import os
import json
import pandas as pd
from datetime import datetime, timedelta

def get_db_connection():
    """Conecta ao banco de dados"""
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def analisar_extracao_completa(job_id):
    """Analisa uma extração específica em detalhes"""
    conn = get_db_connection()
    
    try:
        # Buscar dados da extração
        query = """
        SELECT 
            sj.*,
            u.email
        FROM scraping_jobs sj
        JOIN users u ON sj.user_id = u.id
        WHERE sj.id = ?
        """
        
        job = conn.execute(query, (job_id,)).fetchone()
        
        if not job:
            print(f"❌ Extração com ID {job_id} não encontrada.")
            return
        
        print(f"📊 Análise da Extração ID: {job_id}")
        print("=" * 60)
        print(f"👤 Usuário: {job['email']}")
        print(f"🔍 Palavra-chave: {job['keyword']}")
        print(f"📍 Localizações: {job['locations']}")
        print(f"📈 Total de resultados esperados: {job['total_results']}")
        print(f"📊 Status: {job['status']}")
        print(f"⏱️  Progresso: {job['progress']}%")
        print(f"🕐 Criado em: {job['created_at']}")
        
        if job['started_at']:
            print(f"▶️  Iniciado em: {job['started_at']}")
        if job['completed_at']:
            print(f"✅ Concluído em: {job['completed_at']}")
        
        if job['error_message']:
            print(f"❌ Erro: {job['error_message']}")
        
        # Analisar arquivo de resultados
        if job['results_path'] and os.path.exists(job['results_path']):
            print(f"\n📁 Arquivo de resultados: {job['results_path']}")
            
            try:
                # Ler arquivo CSV
                df = pd.read_csv(job['results_path'])
                print(f"📊 Total de registros no arquivo: {len(df)}")
                print(f"📋 Colunas disponíveis: {', '.join(df.columns)}")
                
                # Estatísticas básicas
                print(f"\n📈 Estatísticas:")
                print(f"   - Média de avaliações: {df['reviews_average'].mean():.2f}")
                print(f"   - Total de avaliações: {df['reviews_count'].sum()}")
                print(f"   - Empresas com WhatsApp: {df['whatsapp_link'].notna().sum()}")
                print(f"   - Empresas com website: {df['website'].notna().sum()}")
                
                # Mostrar primeiras linhas
                print(f"\n📋 Primeiros 5 registros:")
                print(df.head().to_string(index=False))
                
            except Exception as e:
                print(f"❌ Erro ao ler arquivo: {e}")
        else:
            print("❌ Arquivo de resultados não encontrado ou não disponível.")
        
    except Exception as e:
        print(f"❌ Erro ao analisar extração: {e}")
    finally:
        conn.close()

def listar_extracao_por_periodo(dias=7):
    """Lista extrações de um período específico"""
    conn = get_db_connection()
    
    try:
        # Calcular data de início
        data_inicio = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')
        
        print(f"📅 Extrações dos últimos {dias} dias (desde {data_inicio}):")
        print("=" * 60)
        
        query = """
        SELECT 
            sj.id,
            sj.user_id,
            u.email,
            sj.keyword,
            sj.status,
            sj.progress,
            sj.created_at,
            sj.results_path
        FROM scraping_jobs sj
        JOIN users u ON sj.user_id = u.id
        WHERE DATE(sj.created_at) >= ?
        ORDER BY sj.created_at DESC
        """
        
        rows = conn.execute(query, (data_inicio,)).fetchall()
        
        if not rows:
            print(f"❌ Nenhuma extração encontrada nos últimos {dias} dias.")
            return
        
        for i, job in enumerate(rows, 1):
            print(f"{i}. ID: {job['id']} | {job['email']} | {job['keyword']} | {job['status']} | {job['created_at']}")
            
            if job['results_path'] and os.path.exists(job['results_path']):
                file_size = os.path.getsize(job['results_path'])
                print(f"   📁 Arquivo: {job['results_path']} ({file_size} bytes)")
            else:
                print(f"   ❌ Arquivo não encontrado")
            print()
        
        # Estatísticas do período
        print("📊 Estatísticas do período:")
        status_count = {}
        for job in rows:
            status = job['status']
            status_count[status] = status_count.get(status, 0) + 1
        
        for status, count in status_count.items():
            print(f"   {status}: {count} extração(ões)")
        
    except Exception as e:
        print(f"❌ Erro ao listar extrações: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "list":
            dias = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            listar_extracao_por_periodo(dias)
        else:
            # Analisar extração específica
            job_id = int(sys.argv[1])
            analisar_extracao_completa(job_id)
    else:
        print("💡 Uso:")
        print("   python analisar_extracao.py <job_id>     - Analisar extração específica")
        print("   python analisar_extracao.py list [dias]  - Listar extrações dos últimos X dias")
        print("\n📊 Exemplos:")
        print("   python analisar_extracao.py 1           - Analisar extração ID 1")
        print("   python analisar_extracao.py list 7      - Listar últimas 7 dias")
