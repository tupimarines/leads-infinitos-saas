#!/usr/bin/env python3
"""
Script para verificar o processamento dos webhooks
"""

import sqlite3
import os

def verify_webhook_processing():
    """Verifica se os webhooks estão sendo processados corretamente"""
    
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        print("🔍 VERIFICAÇÃO DO PROCESSAMENTO DE WEBHOOKS")
        print("=" * 60)
        
        # Verificar webhooks recebidos
        webhooks = conn.execute("""
            SELECT event_type, hotmart_purchase_id, processed, created_at
            FROM hotmart_webhooks 
            ORDER BY created_at DESC 
            LIMIT 10
        """).fetchall()
        
        print(f"📊 Total de webhooks recebidos: {len(webhooks)}")
        print("-" * 60)
        
        sale_completed_count = 0
        sale_cancelled_count = 0
        
        for webhook in webhooks:
            event_type = webhook['event_type']
            purchase_id = webhook['hotmart_purchase_id']
            processed = webhook['processed']
            created_at = webhook['created_at'][:19] if webhook['created_at'] else "N/A"
            
            if event_type == 'SALE_COMPLETED':
                sale_completed_count += 1
                status = "✅ DEVERIA PROCESSAR" if not processed else "❌ JÁ PROCESSADO"
            elif event_type == 'SALE_CANCELLED':
                sale_cancelled_count += 1
                status = "✅ CORRETO (não processa cancelamentos)"
            else:
                status = "❓ EVENTO DESCONHECIDO"
            
            print(f"📋 {event_type} - {purchase_id} - Processado: {processed} - {status} - {created_at}")
        
        print("-" * 60)
        print(f"📈 Resumo:")
        print(f"   • SALE_COMPLETED: {sale_completed_count} eventos")
        print(f"   • SALE_CANCELLED: {sale_cancelled_count} eventos")
        
        # Verificar se há licenças criadas para SALE_COMPLETED
        licenses = conn.execute("""
            SELECT l.hotmart_purchase_id, l.license_type, l.status, u.email
            FROM licenses l
            LEFT JOIN users u ON l.user_id = u.id
            WHERE l.hotmart_purchase_id LIKE 'TEST-%' OR l.hotmart_purchase_id LIKE 'ANUAL-%' OR l.hotmart_purchase_id LIKE 'SEMESTRAL-%'
            ORDER BY l.created_at DESC
        """).fetchall()
        
        print(f"\n📊 Licenças criadas por webhooks de teste: {len(licenses)}")
        print("-" * 60)
        
        for license in licenses:
            user_email = license['email'] if license['email'] else "Usuário não registrado"
            print(f"✅ {license['hotmart_purchase_id']} - {license['license_type']} - {license['status']} - {user_email}")
        
        # Verificar se há webhooks SALE_COMPLETED sem licenças correspondentes
        webhook_purchase_ids = [w['hotmart_purchase_id'] for w in webhooks if w['event_type'] == 'SALE_COMPLETED']
        license_purchase_ids = [l['hotmart_purchase_id'] for l in licenses]
        
        missing_licenses = [pid for pid in webhook_purchase_ids if pid not in license_purchase_ids]
        
        if missing_licenses:
            print(f"\n⚠️  ATENÇÃO: Webhooks SALE_COMPLETED sem licenças criadas:")
            for pid in missing_licenses:
                print(f"   • {pid}")
        else:
            print(f"\n✅ PERFEITO: Todos os webhooks SALE_COMPLETED geraram licenças!")
            
    except Exception as e:
        print(f"❌ Erro ao verificar: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    verify_webhook_processing()
