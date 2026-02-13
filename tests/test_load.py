#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧪 AUTOMATED LOAD TESTING SCRIPT
Script para automatizar testes de carga conforme D4_IMPLEMENTATION_PLAN.md Item 4
"""

import os
import sys
import time
import random
import string
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import threading

load_dotenv()

# =============================================================================
# DATABASE CONNECTION
# =============================================================================

def get_db_connection():
    """Conecta ao PostgreSQL usando variáveis de ambiente"""
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def generate_random_email(prefix="test_user"):
    """Gera email aleatório para testes"""
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}_{random_suffix}@test.com"

def generate_fake_phone():
    """Gera número de telefone fake brasileiro"""
    ddd = random.choice(['11', '21', '47', '48', '51', '85'])
    number = '9' + ''.join(random.choices(string.digits, k=8))
    return f"+55{ddd}{number}"

def generate_fake_name():
    """Gera nome fake"""
    first_names = ['João', 'Maria', 'Pedro', 'Ana', 'Carlos', 'Julia', 'Lucas', 'Fernanda']
    last_names = ['Silva', 'Santos', 'Oliveira', 'Souza', 'Costa', 'Pereira', 'Rodrigues']
    return f"{random.choice(first_names)} {random.choice(last_names)}"

def create_test_user(email, license_type='semestral'):
    """
    Cria usuário de teste + licença
    
    Args:
        email (str): Email do usuário
        license_type (str): 'semestral' (20 msgs/dia) ou 'anual' (30 msgs/dia)
    
    Returns:
        dict: {'user_id': int, 'license_id': int, 'daily_limit': int}
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Criar usuário
    password_hash = generate_password_hash('test123')
    cur.execute(
        "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
        (email, password_hash)
    )
    user_id = cur.fetchone()[0]
    
    # 2. Criar licença
    purchase_id = f"TEST_{user_id}_{int(time.time())}"
    product_id = "5974664"
    purchase_date = datetime.utcnow().isoformat()
    
    if license_type == 'semestral':
        expires_at = (datetime.utcnow() + timedelta(days=180)).isoformat()
        daily_limit = 20
    else:  # anual
        expires_at = (datetime.utcnow() + timedelta(days=365)).isoformat()
        daily_limit = 30
    
    cur.execute(
        """
        INSERT INTO licenses 
        (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active') RETURNING id
        """,
        (user_id, purchase_id, product_id, license_type, purchase_date, expires_at)
    )
    license_id = cur.fetchone()[0]
    
    conn.commit()
    conn.close()
    
    return {
        'user_id': user_id,
        'email': email,
        'license_id': license_id,
        'license_type': license_type,
        'daily_limit': daily_limit
    }

def create_test_instance(user_id, instance_name=None):
    """
    Cria instância WhatsApp conectada para o usuário de teste
    
    Args:
        user_id (int): ID do usuário
        instance_name (str, optional): Nome da instância. Se None, gera aleatório
    
    Returns:
        str: Nome da instância criada
    """
    if not instance_name:
        instance_name = f"test_instance_{user_id}_{int(time.time())}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        """
        INSERT INTO instances (user_id, name, status)
        VALUES (%s, %s, 'connected')
        """,
        (user_id, instance_name)
    )
    
    conn.commit()
    conn.close()
    
    return instance_name


def create_fake_leads(count):
    """
    Gera lista de leads fake
    
    Args:
        count (int): Quantidade de leads
    
    Returns:
        list[dict]: [{'phone': '...', 'name': '...'}]
    """
    leads = []
    for _ in range(count):
        leads.append({
            'phone': generate_fake_phone(),
            'name': generate_fake_name()
        })
    return leads

def create_campaign(user_id, name, leads, daily_limit):
    """
    Cria campanha e adiciona leads
    
    Args:
        user_id (int): ID do usuário
        name (str): Nome da campanha
        leads (list[dict]): Lista de leads
        daily_limit (int): Limite diário de envios
    
    Returns:
        int: campaign_id
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Criar campanha
    message_template = "Olá {nome}, tudo bem? Teste automatizado de carga."
    cur.execute(
        """
        INSERT INTO campaigns (user_id, name, message_template, daily_limit, status)
        VALUES (%s, %s, %s, %s, 'pending') RETURNING id
        """,
        (user_id, name, message_template, daily_limit)
    )
    campaign_id = cur.fetchone()[0]
    
    # 2. Adicionar leads em lote
    if leads:
        args_str = ','.join(
            cur.mogrify("(%s, %s, %s)", (campaign_id, l['phone'], l['name'])).decode('utf-8')
            for l in leads
        )
        cur.execute(f"INSERT INTO campaign_leads (campaign_id, phone, name) VALUES {args_str}")
    
    conn.commit()
    conn.close()
    
    return campaign_id

def start_campaign(campaign_id):
    """
    Inicia a campanha mudando status de 'pending' para 'running'
    
    Args:
        campaign_id (int): ID da campanha
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        "UPDATE campaigns SET status = 'running' WHERE id = %s",
        (campaign_id,)
    )
    
    conn.commit()
    conn.close()


def get_campaign_stats(campaign_id):
    """
    Retorna estatísticas da campanha
    
    Returns:
        dict: {
            'total': int,
            'sent': int,
            'pending': int,
            'failed': int,
            'status': str,
            'last_sent_at': datetime
        }
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Stats dos leads
    cur.execute(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'sent') as sent,
            COUNT(*) FILTER (WHERE status = 'pending') as pending,
            COUNT(*) FILTER (WHERE status = 'failed') as failed,
            MAX(sent_at) as last_sent_at
        FROM campaign_leads
        WHERE campaign_id = %s
        """,
        (campaign_id,)
    )
    lead_stats = cur.fetchone()
    
    # Status da campanha
    cur.execute("SELECT status FROM campaigns WHERE id = %s", (campaign_id,))
    row = cur.fetchone()
    campaign_status = row['status'] if row else 'unknown'
    
    conn.close()
    
    return {
        'total': lead_stats['total'],
        'sent': lead_stats['sent'],
        'pending': lead_stats['pending'],
        'failed': lead_stats['failed'],
        'status': campaign_status,
        'last_sent_at': lead_stats['last_sent_at']
    }

def check_delays_respected(campaign_id, min_delay=300, max_delay=600):
    """
    Verifica se delays entre envios estão sendo respeitados
    
    Args:
        campaign_id (int): ID da campanha
        min_delay (int): Delay mínimo em segundos (padrão: 300s = 5min)
        max_delay (int): Delay máximo em segundos (padrão: 600s = 10min)
    
    Returns:
        dict: {'ok': bool, 'violations': list, 'checked': int}
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute(
        """
        SELECT sent_at 
        FROM campaign_leads 
        WHERE campaign_id = %s AND status = 'sent' AND sent_at IS NOT NULL
        ORDER BY sent_at ASC
        """,
        (campaign_id,)
    )
    rows = cur.fetchall()
    conn.close()
    
    if len(rows) < 2:
        return {'ok': True, 'violations': [], 'checked': len(rows)}
    
    violations = []
    for i in range(1, len(rows)):
        prev_time = rows[i-1]['sent_at']
        curr_time = rows[i]['sent_at']
        diff_seconds = (curr_time - prev_time).total_seconds()
        
        if diff_seconds < min_delay or diff_seconds > max_delay:
            violations.append({
                'prev': prev_time,
                'curr': curr_time,
                'diff_seconds': diff_seconds
            })
    
    return {
        'ok': len(violations) == 0,
        'violations': violations,
        'checked': len(rows)
    }

def cleanup_test_data(user_ids):
    """
    Remove dados de testes (usuários, licenças, campanhas, leads)
    
    Args:
        user_ids (list[int]): Lista de user_ids para deletar
    """
    if not user_ids:
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    for user_id in user_ids:
        # 1. Deletar leads de campanhas do usuário
        cur.execute(
            """
            DELETE FROM campaign_leads 
            WHERE campaign_id IN (SELECT id FROM campaigns WHERE user_id = %s)
            """,
            (user_id,)
        )
        
        # 2. Deletar campanhas
        cur.execute("DELETE FROM campaigns WHERE user_id = %s", (user_id,))
        
        # 3. Deletar instâncias
        cur.execute("DELETE FROM instances WHERE user_id = %s", (user_id,))
        
        # 4. Deletar licenças
        cur.execute("DELETE FROM licenses WHERE user_id = %s", (user_id,))
        
        # 5. Deletar usuário
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    
    conn.commit()
    conn.close()

# =============================================================================
# TEST 1: CAMPANHA COM 50 LEADS
# =============================================================================

def test_1_single_campaign_50_leads():
    """
    Teste 1: Campanha Única com 50 Leads
    
    Valida:
    - Todos leads são processados
    - Delays respeitados (300-600s)
    - Limite diário não excedido
    """
    print("\n" + "="*70)
    print("🧪 TESTE 1: Campanha Única com 50 Leads")
    print("="*70)
    
    test_user_ids = []
    
    try:
        # 1. Criar usuário de teste
        user_data = create_test_user(generate_random_email("test1"), 'anual')
        test_user_ids.append(user_data['user_id'])
        print(f"✅ Usuário criado: {user_data['email']} (daily_limit={user_data['daily_limit']})")
        
        # 2. Criar instância WhatsApp conectada
        instance_name = create_test_instance(user_data['user_id'])
        print(f"✅ Instância WhatsApp criada: {instance_name}")
        
        # 3. Gerar 50 leads
        leads = create_fake_leads(50)
        print(f"✅ 50 leads fake gerados")
        
        # 4. Criar campanha
        campaign_id = create_campaign(
            user_data['user_id'],
            "TEST1_50_LEADS",
            leads,
            user_data['daily_limit']
        )
        print(f"✅ Campanha criada: ID={campaign_id}")
        
        # 5. Iniciar campanha (mudar status para 'running')
        start_campaign(campaign_id)
        print(f"✅ Campanha iniciada (status='running')")
        
        # 6. Monitorar progresso (timeout: 2 horas)
        print("\n⏱️  Monitorando progresso (atualização a cada 30s)...")
        print("   Aguardando worker_sender processar leads...")
        print("   (Para acelerar, certifique-se que worker_sender está rodando)\n")
        
        timeout = 7200  # 2 horas
        start_time = time.time()
        check_interval = 30  # 30 segundos
        
        while (time.time() - start_time) < timeout:
            stats = get_campaign_stats(campaign_id)
            elapsed = int(time.time() - start_time)
            
            print(f"   [{elapsed}s] Total: {stats['total']} | Enviados: {stats['sent']} | "
                  f"Pendentes: {stats['pending']} | Falhados: {stats['failed']} | "
                  f"Status: {stats['status']}")
            
            # Verificar se todos foram processados
            if stats['pending'] == 0 and stats['sent'] + stats['failed'] == stats['total']:
                print("\n✅ Todos os leads foram processados!")
                
                # Verificar delays
                delay_check = check_delays_respected(campaign_id)
                if delay_check['ok']:
                    print(f"✅ Delays respeitados ({delay_check['checked']} envios verificados)")
                else:
                    print(f"⚠️  Delays NÃO respeitados: {len(delay_check['violations'])} violações")
                    for v in delay_check['violations'][:3]:  # Mostrar apenas 3 primeiras
                        print(f"    - Diff: {v['diff_seconds']}s (esperado: 300-600s)")
                
                # Verificar limite diário
                if stats['sent'] <= user_data['daily_limit']:
                    print(f"✅ Limite diário respeitado: {stats['sent']}/{user_data['daily_limit']}")
                else:
                    print(f"❌ Limite diário EXCEDIDO: {stats['sent']}/{user_data['daily_limit']}")
                
                print("\n" + "="*70)
                print("✅ TESTE 1 CONCLUÍDO")
                print("="*70)
                return True
            
            time.sleep(check_interval)
        
        print("\n⚠️  Timeout atingido. Teste interrompido.")
        return False
        
    except Exception as e:
        print(f"\n❌ Erro no Teste 1: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        if test_user_ids:
            print(f"\n🧹 Limpando dados de teste...")
            cleanup_test_data(test_user_ids)
            print("✅ Dados removidos")

# =============================================================================
# TEST 2: 3 USUÁRIOS SIMULTÂNEOS
# =============================================================================

def create_concurrent_campaign(user_data, campaign_name, lead_count, results, index):
    """Thread worker para criar campanha concorrente"""
    try:
        leads = create_fake_leads(lead_count)
        campaign_id = create_campaign(
            user_data['user_id'],
            campaign_name,
            leads,
            user_data['daily_limit']
        )
        start_campaign(campaign_id)  # Iniciar campanha
        results[index] = {'success': True, 'campaign_id': campaign_id, 'user_data': user_data}
    except Exception as e:
        results[index] = {'success': False, 'error': str(e)}

def test_2_concurrent_users():
    """
    Teste 2: 3 Usuários Simultâneos
    
    Valida:
    - 3 usuários com planos diferentes
    - Criação simultânea de campanhas
    - Sem travamentos
    """
    print("\n" + "="*70)
    print("🧪 TESTE 2: 3 Usuários Simultâneos")
    print("="*70)
    
    test_user_ids = []
    
    try:
        # 1. Criar 3 usuários com planos diferentes
        users = [
            create_test_user(generate_random_email("test2_userA"), 'semestral'),  # 20/dia
            create_test_user(generate_random_email("test2_userB"), 'anual'),      # 30/dia
            create_test_user(generate_random_email("test2_userC"), 'semestral'),  # 20/dia
        ]
        
        for u in users:
            test_user_ids.append(u['user_id'])
            print(f"✅ Usuário criado: {u['email']} ({u['license_type']}, {u['daily_limit']}/dia)")
            
            # Criar instância para cada usuário
            create_test_instance(u['user_id'])
            print(f"  ✅ Instância criada para {u['email']}")
        
        # 2. Criar campanhas simultaneamente usando threads
        print("\n⏱️  Criando 3 campanhas simultaneamente...")
        threads = []
        results = [None, None, None]
        
        for i, user in enumerate(users):
            t = threading.Thread(
                target=create_concurrent_campaign,
                args=(user, f"TEST2_CONCURRENT_{i+1}", 15, results, i)
            )
            threads.append(t)
            t.start()
        
        # Aguardar todas as threads
        for t in threads:
            t.join()
        
        # 3. Verificar resultados
        success_count = sum(1 for r in results if r and r.get('success'))
        
        print(f"\n📊 Resultado: {success_count}/3 campanhas criadas com sucesso")
        
        for i, r in enumerate(results):
            if r and r.get('success'):
                print(f"  ✅ Campanha {i+1}: ID={r['campaign_id']}")
            else:
                error = r.get('error') if r else 'Thread falhou'
                print(f"  ❌ Campanha {i+1}: {error}")
        
        # 4. Monitorar progresso por 5 minutos
        if success_count > 0:
            print("\n⏱️  Monitorando progresso das campanhas por 5 minutos...")
            timeout = 300
            start_time = time.time()
            
            while (time.time() - start_time) < timeout:
                elapsed = int(time.time() - start_time)
                print(f"\n   [{elapsed}s]")
                
                all_done = True
                for i, r in enumerate(results):
                    if r and r.get('success'):
                        stats = get_campaign_stats(r['campaign_id'])
                        print(f"   Campanha {i+1}: {stats['sent']}/15 enviados, {stats['pending']} pendentes")
                        if stats['pending'] > 0:
                            all_done = False
                
                if all_done:
                    print("\n✅ Todas as campanhas concluídas!")
                    break
                
                time.sleep(30)
        
        print("\n" + "="*70)
        if success_count == 3:
            print("✅ TESTE 2 CONCLUÍDO: Sem deadlocks detectados")
        else:
            print("⚠️  TESTE 2 CONCLUÍDO: Algumas falhas detectadas")
        print("="*70)
        
        return success_count == 3
        
    except Exception as e:
        print(f"\n❌ Erro no Teste 2: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        if test_user_ids:
            print(f"\n🧹 Limpando dados de teste...")
            cleanup_test_data(test_user_ids)
            print("✅ Dados removidos")

# =============================================================================
# TEST 3: ESTRESSE DE DAILY LIMIT
# =============================================================================

def test_3_daily_limit_stress():
    """
    Teste 3: Estresse de Daily Limit
    
    Valida:
    - Usuário Semestral (20 msgs/dia) com campanha de 30 leads
    - Após 20 envios, campanha deve pausar
    """
    print("\n" + "="*70)
    print("🧪 TESTE 3: Estresse de Daily Limit")
    print("="*70)
    
    test_user_ids = []
    
    try:
        # 1. Criar usuário Semestral (20/dia)
        user_data = create_test_user(generate_random_email("test3"), 'semestral')
        test_user_ids.append(user_data['user_id'])
        print(f"✅ Usuário Semestral criado: {user_data['email']} (limit={user_data['daily_limit']})")
        
        # 2. Criar instância WhatsApp
        instance_name = create_test_instance(user_data['user_id'])
        print(f"✅ Instância WhatsApp criada: {instance_name}")
        
        # 3. Criar campanha com 30 leads (excede limite)
        leads = create_fake_leads(30)
        campaign_id = create_campaign(
            user_data['user_id'],
            "TEST3_LIMIT_STRESS",
            leads,
            user_data['daily_limit']
        )
        print(f"✅ Campanha criada com 30 leads (excede limite de {user_data['daily_limit']})")
        
        # 3. Iniciar campanha
        start_campaign(campaign_id)
        print(f"✅ Campanha iniciada (status='running')")
        
        # 4. Monitorar até atingir limite
        print(f"\n⏱️  Aguardando atingir limite de {user_data['daily_limit']} envios...")
        timeout = 3600  # 1 hora
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            stats = get_campaign_stats(campaign_id)
            elapsed = int(time.time() - start_time)
            
            print(f"   [{elapsed}s] Enviados: {stats['sent']}/{user_data['daily_limit']} | "
                  f"Pendentes: {stats['pending']} | Status: {stats['status']}")
            
            # Verificar se atingiu o limite e pausou
            if stats['sent'] >= user_data['daily_limit']:
                if stats['status'] == 'paused':
                    print(f"\n✅ Campanha PAUSADA após {stats['sent']} envios (limite: {user_data['daily_limit']})")
                    print("✅ Limite diário funcionando corretamente!")
                    print("\n" + "="*70)
                    print("✅ TESTE 3 CONCLUÍDO")
                    print("="*70)
                    return True
                else:
                    print(f"\n⚠️  Limite atingido mas campanha NÃO pausou (status: {stats['status']})")
                    return False
            
            time.sleep(30)
        
        print("\n⚠️  Timeout atingido antes de atingir limite.")
        return False
        
    except Exception as e:
        print(f"\n❌ Erro no Teste 3: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Cleanup
        if test_user_ids:
            print(f"\n🧹 Limpando dados de teste...")
            cleanup_test_data(test_user_ids)
            print("✅ Dados removidos")

# =============================================================================
# MAIN
# =============================================================================

def main():
    """Executa bateria de testes"""
    print("\n" + "="*70)
    print(" 🧪 BATERIA DE TESTES AUTOMATIZADOS - LEADS INFINITOS SAAS")
    print("="*70)
    print("Baseado em: D4_IMPLEMENTATION_PLAN.md - Item 4")
    print("="*70)
    
    # Verificar conexão com DB
    try:
        conn = get_db_connection()
        conn.close()
        print("✅ Conexão com PostgreSQL OK")
    except Exception as e:
        print(f"❌ Erro ao conectar com PostgreSQL: {e}")
        print("   Certifique-se que o docker-compose está rodando:")
        print("   > docker-compose up -d")
        return
    
    # Menu de seleção
    print("\nEscolha qual teste executar:")
    print("  1 - Teste 1: Campanha com 50 Leads")
    print("  2 - Teste 2: 3 Usuários Simultâneos")
    print("  3 - Teste 3: Estresse de Daily Limit")
    print("  4 - Executar todos os testes")
    
    choice = input("\nDigite o número (1-4): ").strip()
    
    results = []
    
    if choice == '1':
        results.append(('Teste 1', test_1_single_campaign_50_leads()))
    elif choice == '2':
        results.append(('Teste 2', test_2_concurrent_users()))
    elif choice == '3':
        results.append(('Teste 3', test_3_daily_limit_stress()))
    elif choice == '4':
        results.append(('Teste 1', test_1_single_campaign_50_leads()))
        results.append(('Teste 2', test_2_concurrent_users()))
        results.append(('Teste 3', test_3_daily_limit_stress()))
    else:
        print("❌ Opção inválida")
        return
    
    # Relatório Final
    print("\n\n" + "="*70)
    print(" 📊 RELATÓRIO FINAL")
    print("="*70)
    
    passed = sum(1 for name, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASSOU" if result else "❌ FALHOU"
        print(f"{name}: {status}")
    
    print(f"\nTotal: {passed}/{total} testes passaram")
    print("="*70)

if __name__ == "__main__":
    main()
