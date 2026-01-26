from flask import Flask, render_template, request, redirect, url_for, send_file, flash, abort
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message

import os
import secrets
import string
import json
import threading
import time
from datetime import datetime, timedelta
import redis
from rq import Queue
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from main import run_scraper_with_progress
import requests
import re
import pandas as pd
import io
import csv
from openai import OpenAI
from functools import wraps


load_dotenv()

# Configuração Redis
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_conn = redis.from_url(REDIS_URL)
q = Queue(connection=redis_conn)


def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )
    return conn


def init_db() -> None:
    print("🔄 Iniciando migração do banco de dados...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Tabela de usuários
    print("➡️ Verificando tabela users...")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Adicionar coluna is_admin se não existir (migração)
    print("➡️ Adicionando coluna is_admin se necessário...")
    cur.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
        """
    )
    
    # Tabela de licenças
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS licenses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            hotmart_purchase_id TEXT UNIQUE NOT NULL,
            hotmart_product_id TEXT NOT NULL,
            license_type TEXT NOT NULL CHECK (license_type IN ('starter', 'pro', 'scale', 'semestral', 'anual')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'cancelled')),
            purchase_date TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de webhooks da Hotmart
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hotmart_webhooks (
            id SERIAL PRIMARY KEY,
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hotmart_config (
            id SERIAL PRIMARY KEY,
            client_id TEXT NOT NULL,
            client_secret TEXT NOT NULL,
            webhook_secret TEXT,
            product_id TEXT NOT NULL,
            sandbox_mode BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de webhooks da Hubla
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hubla_webhooks (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            hubla_purchase_id TEXT,
            payload TEXT NOT NULL,
            processed BOOLEAN DEFAULT FALSE,
            processed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de configurações da Hubla
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hubla_config (
            id SERIAL PRIMARY KEY,
            webhook_token TEXT NOT NULL,
            product_id TEXT NOT NULL,
            sandbox_mode BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de reset de senha
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de jobs de scraping
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scraping_jobs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            keyword TEXT NOT NULL,
            locations TEXT NOT NULL,
            total_results INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            progress INTEGER DEFAULT 0,
            current_location TEXT,
            results_path TEXT,
            error_message TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Tabela de instâncias do WhatsApp
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS instances (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            server_url TEXT,
            apikey TEXT,
            status TEXT DEFAULT 'disconnected',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Tabela de campanhas
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message_template TEXT,
            daily_limit INTEGER DEFAULT 0,
            closed_deals INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Adicionar coluna closed_deals se não existir (migração)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS closed_deals INTEGER DEFAULT 0;
        """
    )

    # Adicionar coluna sent_today se não existir (migração)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sent_today INTEGER DEFAULT 0;
        """
    )
    
    # Tabela de leads da campanha (Fila de Envio)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_leads (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            phone TEXT NOT NULL,
            name TEXT,
            whatsapp_link TEXT,
            status TEXT DEFAULT 'pending',
            sent_at TIMESTAMP,
            log TEXT
        );
        """
    )
    
    # Adicionar coluna whatsapp_link se não existir (migração)
    cur.execute(
        """
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS whatsapp_link TEXT;
        """
    )

    
    # Inserir configuração inicial da Hotmart se não existir
    cur.execute(
        """
        INSERT INTO hotmart_config 
        (client_id, client_secret, product_id, sandbox_mode) 
        VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        ('cb6bcde6-24cd-464f-80f3-e4efce3f048c', '7ee4a93d-1aec-473b-a8e6-1d0a813382e2', '5974664', True)
    )
    
    # Inserir configuração inicial da Hubla se não existir
    cur.execute(
        """
        INSERT INTO hubla_config 
        (webhook_token, product_id, sandbox_mode) 
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """,
        ('your-hubla-webhook-token', 'your-hubla-product-id', True)
    )
    
    conn.commit()
    conn.close()


class User(UserMixin):
    def __init__(self, id: int, email: str, password_hash: str, is_admin: bool = False):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin

    @staticmethod
    def get_by_id(user_id: int) -> "User | None":
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, password_hash, is_admin FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2], row[3])
        return None

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, password_hash, is_admin FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2], row[3])
        return None

    @staticmethod
    def create(email: str, password: str) -> "User":
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (email, password_hash),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return User(new_id, email, password_hash, False)

    def has_active_license(self) -> bool:
        """Verifica se o usuário tem uma licença ativa"""
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) as count FROM licenses 
                WHERE user_id = %s AND status = 'active' AND expires_at > NOW()
                """,
                (self.id,)
            )
            row = cur.fetchone()
        conn.close()
        return row['count'] > 0


class License:
    def __init__(self, id: int, user_id: int, hotmart_purchase_id: str, hotmart_product_id: str, 
                 license_type: str, status: str, purchase_date: str, expires_at: str):
        self.id = id
        self.user_id = user_id
        self.hotmart_purchase_id = hotmart_purchase_id
        self.hotmart_product_id = hotmart_product_id
        self.license_type = license_type
        self.status = status
        self.purchase_date = purchase_date
        self.expires_at = expires_at

    @property
    def daily_limit(self) -> int:
        if self.license_type == 'scale':
            return 30
        elif self.license_type == 'pro':
            return 20
        elif self.license_type == 'starter':
            return 10
        elif self.license_type == 'anual':
            return 30  # Legacy
        elif self.license_type == 'semestral':
            return 20  # Legacy
        return 10  # Fallback

    @staticmethod
    def create(user_id: int, hotmart_purchase_id: str, hotmart_product_id: str, 
               license_type: str, purchase_date: str) -> "License":
        # Calcular data de expiração baseada no tipo de licença
        from datetime import datetime, timedelta
        
        # Garantir formato correto do license_type
        license_type = license_type.strip().lower()
        
        purchase_dt = datetime.fromisoformat(purchase_date.replace('Z', '+00:00'))
        
        if license_type == 'semestral':
            expires_at = purchase_dt + timedelta(days=180)
        else:  # anual
            expires_at = purchase_dt + timedelta(days=365)
        
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO licenses 
                (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (user_id, hotmart_purchase_id, hotmart_product_id, license_type, 
                 purchase_date, expires_at.isoformat())
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        return License(new_id, user_id, hotmart_purchase_id, hotmart_product_id, 
                      license_type, 'active', purchase_date, expires_at.isoformat())

    @staticmethod
    def get_by_user_id(user_id: int) -> list["License"]:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM licenses WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,)
            )
            rows = cur.fetchall()
        conn.close()
        
        return [License(row['id'], row['user_id'], row['hotmart_purchase_id'], 
                       row['hotmart_product_id'], row['license_type'], row['status'],
                       row['purchase_date'], row['expires_at']) for row in rows]


# Decorator para rotas de admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Acesso não autorizado.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

class Campaign:
    def __init__(self, id, user_id, name, status, message_template, daily_limit, created_at, closed_deals=0, scheduled_start=None, sent_today=0):
        self.id = id
        self.user_id = user_id
        self.name = name
        self.status = status
        self.message_template = message_template
        self.daily_limit = daily_limit
        self.created_at = created_at
        self.closed_deals = closed_deals
        self.scheduled_start = scheduled_start
        self.sent_today = sent_today

    @staticmethod
    def create(user_id: int, name: str, message_template: str, daily_limit: int) -> "Campaign":
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaigns (user_id, name, message_template, daily_limit)
                VALUES (%s, %s, %s, %s) RETURNING id, created_at
                """,
                (user_id, name, message_template, daily_limit)
            )
            row = cur.fetchone()
            new_id = row[0]
            created_at = row[1]
        conn.commit()
        conn.close()
        return Campaign(new_id, user_id, name, 'pending', message_template, daily_limit, created_at)

    @staticmethod
    def get_by_user(user_id: int):
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM campaigns WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
            rows = cur.fetchall()
        conn.close()
        return [Campaign(**row) for row in rows]

    @staticmethod
    def get_by_id(campaign_id: int, user_id: int):
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM campaigns WHERE id = %s AND user_id = %s", (campaign_id, user_id))
            row = cur.fetchone()
        conn.close()
        if row:
            return Campaign(**row)
        return None

    def delete(self):
        """Exclui a campanha e seus leads associados"""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # 1. Excluir leads da campanha
                cur.execute("DELETE FROM campaign_leads WHERE campaign_id = %s", (self.id,))
                
                # 2. Excluir a campanha
                cur.execute("DELETE FROM campaigns WHERE id = %s", (self.id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"Erro ao excluir campanha {self.id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()


class CampaignLead:
    @staticmethod
    def add_leads(campaign_id: int, leads: list[dict]):
        """
        Adiciona leads à campanha em lote.
        leads = [{'phone': '...', 'name': '...', 'whatsapp_link': '...' (opcional)}]
        """
        if not leads:
            return
            
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                args_str = ','.join(
                    cur.mogrify("(%s, %s, %s, %s)", 
                               (campaign_id, l.get('phone'), l.get('name'), l.get('whatsapp_link'))).decode('utf-8') 
                    for l in leads
                )
                cur.execute("INSERT INTO campaign_leads (campaign_id, phone, name, whatsapp_link) VALUES " + args_str)
            conn.commit()
        except Exception as e:
            print(f"Erro ao adicionar leads: {e}")
            conn.rollback()
        finally:
            conn.close()



class CampaignLead:
    @staticmethod
    def add_leads(campaign_id: int, leads: list[dict]):
        """
        Adiciona leads à campanha em lote.
        leads = [{'phone': '...', 'name': '...', 'whatsapp_link': '...' (opcional)}]
        """
        if not leads:
            return
            
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                args_str = ','.join(
                    cur.mogrify("(%s, %s, %s, %s)", 
                               (campaign_id, l.get('phone'), l.get('name'), l.get('whatsapp_link'))).decode('utf-8') 
                    for l in leads
                )
                cur.execute("INSERT INTO campaign_leads (campaign_id, phone, name, whatsapp_link) VALUES " + args_str)
            conn.commit()
        except Exception as e:
            print(f"Erro ao adicionar leads: {e}")
            conn.rollback()
        finally:
            conn.close()


class HotmartService:
    def __init__(self):
        self.base_url = "https://developers.hotmart.com/payments/api/v1"
        self.config = self._get_config()
    
    def _get_config(self) -> dict:
        """Obtém configuração da Hotmart do banco"""
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM hotmart_config LIMIT 1")
            row = cur.fetchone()
        conn.close()
        
        if not row:
            raise Exception("Configuração da Hotmart não encontrada")
        
        return dict(row)
    
    def _get_auth_header(self) -> str:
        """Gera header de autenticação Basic"""
        import base64
        credentials = f"{self.config['client_id']}:{self.config['client_secret']}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"
    
    def verify_purchase(self, email: str) -> dict | None:
        """
        Verifica se o email tem uma compra válida do produto
        Retorna dados da compra ou None se não encontrada
        """
        import requests
        from datetime import datetime
        
        headers = {
            'Authorization': self._get_auth_header(),
            'Content-Type': 'application/json'
        }
        
        # Parâmetros para buscar vendas
        params = {
            'buyer_email': email,
            'product_id': self.config['product_id'],
            'status': 'approved'  # Apenas vendas aprovadas
        }
        
        try:
            response = requests.get(
                f"{self.base_url}/sales/history",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Verificar se há vendas aprovadas
                if data.get('items') and len(data['items']) > 0:
                    sale = data['items'][0]  # Pegar a venda mais recente
                    
                    return {
                        'purchase_id': sale.get('purchase_id'),
                        'product_id': sale.get('product_id'),
                        'buyer_email': sale.get('buyer_email'),
                        'purchase_date': sale.get('purchase_date'),
                        'status': sale.get('status'),
                        'price': sale.get('price'),
                        'currency': sale.get('currency')
                    }
            
            return None
            
        except Exception as e:
            print(f"Erro ao verificar compra na Hotmart: {e}")
            return None
    
    
    def process_webhook(self, payload: dict, signature: str) -> bool:
        """
        Processa webhook da Hotmart v2.0.0
        Retorna True se processado com sucesso
        """
        # Validação de Segurança (Hottok)
        expected_hottok = os.environ.get('HOTMART_HOTTOK')
        if not expected_hottok:
            print("⚠️ HOTMART_HOTTOK não configurado no .env")
            # Em dev/test, talvez permitir passar? Não, segurança primeiro.
            # Mas se não estiver configurado, não temos como validar.
            pass 
        elif signature != expected_hottok:
            print(f"❌ Assinatura inválida: Recebido={signature}, Esperado={expected_hottok}")
            return False

        # Extrair dados do Payload v2.0.0
        event = payload.get('event')
        data = payload.get('data', {})
        
        # Identificadores para Log
        purchase_id = data.get('purchase', {}).get('transaction')
        product_id = str(data.get('product', {}).get('id', ''))
        
        # Salvar webhook (Audit Log)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hotmart_webhooks (event_type, hotmart_purchase_id, payload)
                VALUES (%s, %s, %s)
                """,
                (event, purchase_id, json.dumps(payload))
            )
        conn.commit()
        conn.close()
        
        # Eventos de Interesse: PURCHASE_APPROVED ou PURCHASE_COMPLETE
        if event in ['PURCHASE_APPROVED', 'PURCHASE_COMPLETE']:
            return self._process_sale_approved(data)
        
        print(f"⚠️ Evento Hotmart ignorado: {event}")
        return True
    
    def _process_sale_approved(self, data: dict) -> bool:
        """
        Processa venda aprovada: 
        1. Cria usuário se não existir
        2. Cria/Atualiza licença
        """
        try:
            buyer = data.get('buyer', {})
            email = buyer.get('email')
            purchase = data.get('purchase', {})
            purchase_id = purchase.get('transaction')
            product = data.get('product', {})
            product_id = str(product.get('id', ''))
            
            # Data de Aprovação
            approved_date_ms = purchase.get('approved_date')
            if approved_date_ms:
                purchase_date = datetime.fromtimestamp(approved_date_ms / 1000).isoformat()
            else:
                purchase_date = datetime.utcnow().isoformat()

            if not email or not purchase_id:
                print("❌ Dados insuficientes no payload da Hotmart")
                return False
                
            email = email.lower().strip()
            
            # 1. Verificar/Criar Usuário
            user = User.get_by_email(email)
            if not user:
                # Gerar senha temporária segura
                temp_password = secrets.token_urlsafe(8)
                print(f"🆕 Criando usuário para {email} (Senha: {temp_password})")
                
                user = User.create(email, temp_password)
                
                # Enviar email com a senha
                print(f"📧 Enviando email de boas-vindas para {email}...")
                send_welcome_email(email, temp_password)

            
            # 2. Determinar Tipo de Licença (Preço)
            price_value = purchase.get('price', {}).get('value', 0)
            if price_value >= 287.00:
                license_type = 'anual'
            else:
                license_type = 'semestral'
                
            # 3. Verificar se licença já existe (Idempotência)
            existing_licenses = License.get_by_user_id(user.id)
            for lic in existing_licenses:
                if lic.hotmart_purchase_id == purchase_id:
                    print(f"ℹ️ Licença já existe para {email} (Purchase: {purchase_id})")
                    return True
            
            # 4. Criar Licença
            License.create(user.id, purchase_id, product_id, license_type, purchase_date)
            print(f"✅ Licença {license_type} criada com sucesso para {email}!")
            
            return True

        except Exception as e:
            print(f"❌ Erro ao processar venda aprovada: {e}")
            return False




class HublaService:
    def __init__(self):
        self.config = self._get_config()
    
    def _get_config(self) -> dict:
        """Obtém configuração da Hubla do banco"""
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM hubla_config LIMIT 1")
            row = cur.fetchone()
        conn.close()
        
        if not row:
            raise Exception("Configuração da Hubla não encontrada")
        
        return dict(row)
    
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        """
        Verifica a assinatura do webhook da Hubla
        Baseado na documentação da Hubla, eles usam um token de autenticação
        """
        # TODO: Implementar validação de assinatura específica da Hubla
        # Por enquanto, apenas verifica se o token está presente
        expected_token = self.config.get('webhook_token')
        if not expected_token or expected_token == 'your-hubla-webhook-token':
            print("⚠️ Token da Hubla não configurado, aceitando webhook sem validação")
            return True
        
        # Aqui você pode implementar a validação específica da Hubla
        # Por exemplo, verificar se o signature contém o token
        return signature == expected_token or expected_token in signature
    
    def process_webhook(self, payload: dict, signature: str) -> bool:
        """
        Processa webhook da Hubla
        Retorna True se processado com sucesso
        """
        # Verificar assinatura
        if not self.verify_webhook_signature(json.dumps(payload), signature):
            print("❌ Assinatura do webhook da Hubla inválida")
            return False
        
        # Normalizar tipo de evento: v2 usa 'type' (string); v1 pode usar 'event' (string)
        raw_event_type = payload.get('type') or payload.get('event') or ''
        event_type = raw_event_type.lower() if isinstance(raw_event_type, str) else ''

        # Extrair identificador (purchase.id em v1; subscription.id em v2)
        purchase_id = None
        if payload.get('data', {}).get('purchase', {}).get('id'):
            purchase_id = payload.get('data', {}).get('purchase', {}).get('id')
        elif payload.get('purchase', {}).get('id'):
            purchase_id = payload.get('purchase', {}).get('id')
        else:
            evt_obj = payload.get('event') if isinstance(payload.get('event'), dict) else {}
            if evt_obj.get('subscription', {}) and evt_obj.get('subscription', {}).get('id'):
                purchase_id = evt_obj.get('subscription', {}).get('id')
        
        # Salvar webhook no banco
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hubla_webhooks (event_type, hubla_purchase_id, payload)
                VALUES (%s, %s, %s)
                """,
                (event_type, purchase_id, json.dumps(payload))
            )
        conn.commit()
        conn.close()
        
        # v2: Membro > Acesso concedido → criar usuário automaticamente
        if event_type == 'customer.member_added':
            evt_obj = payload.get('event') if isinstance(payload.get('event'), dict) else {}
            return self._process_member_added_v2(evt_obj)

        # v2: confirmação financeira → criar licença
        if event_type in (
            'subscription.activated',
            'invoice.paid',
            'invoice.payment_succeeded',  # variação v2 observada na UI
            'payment_succeeded',          # fallback defensivo
        ):
            evt_obj = payload.get('event') if isinstance(payload.get('event'), dict) else {}
            return self._create_license_from_v2(evt_obj)

        # v1/várias integrações: eventos de compra com completed/approved
        if 'purchase' in event_type and ('completed' in event_type or 'approved' in event_type):
            return self._process_sale_completed(payload.get('data', {}))
        
        return True
    
    def _process_sale_completed(self, sale_data: dict) -> bool:
        """Processa evento de venda completada da Hubla"""
        try:
            # Extrair dados do formato da Hubla
            # A estrutura pode variar, então vamos ser flexíveis
            buyer_email = None
            purchase_id = None
            product_id = None
            purchase_date = None
            
            # Tentar diferentes estruturas possíveis
            if sale_data.get('buyer', {}).get('email'):
                buyer_email = sale_data.get('buyer', {}).get('email')
            elif sale_data.get('customer', {}).get('email'):
                buyer_email = sale_data.get('customer', {}).get('email')
            elif sale_data.get('user', {}).get('email'):
                buyer_email = sale_data.get('user', {}).get('email')
            
            if sale_data.get('purchase', {}).get('id'):
                purchase_id = sale_data.get('purchase', {}).get('id')
            
            if sale_data.get('product', {}).get('id'):
                product_id = str(sale_data.get('product', {}).get('id', ''))
            
            if sale_data.get('purchase', {}).get('created_at'):
                purchase_date = sale_data.get('purchase', {}).get('created_at')
            elif sale_data.get('purchase', {}).get('approved_at'):
                purchase_date = sale_data.get('purchase', {}).get('approved_at')
            
            if not all([buyer_email, purchase_id, product_id, purchase_date]):
                print(f"Dados insuficientes da Hubla: email={buyer_email}, purchase_id={purchase_id}, product_id={product_id}, date={purchase_date}")
                return False
            
            # Verificar se já existe licença para esta compra
            conn = get_db_connection()
            existing = conn.execute(
                "SELECT id FROM licenses WHERE hotmart_purchase_id = ?",
                (purchase_id,)
            ).fetchone()
            conn.close()
            
            if existing:
                return True  # Licença já existe
            
            # Determinar tipo de licença baseado no preço
            price = float(sale_data.get('purchase', {}).get('price', {}).get('value', 0))
            if price >= 287.00:  # Licença anual
                license_type = 'anual'
            else:  # Licença semestral
                license_type = 'semestral'
            
            # Buscar usuário pelo email
            user = User.get_by_email(buyer_email)
            if user:
                # Criar licença para usuário existente
                License.create(user.id, purchase_id, product_id, license_type, purchase_date)
                print(f"Licença Hubla criada para {buyer_email}: {license_type} - {purchase_id}")
            else:
                # Usuário ainda não se registrou, a licença será criada quando ele se registrar
                print(f"Usuário Hubla {buyer_email} não encontrado. Licença será criada no registro.")
                pass
            
            return True
            
        except Exception as e:
            print(f"Erro ao processar venda completada da Hubla: {e}")
            return False

    def _process_member_added_v2(self, event_data: dict) -> bool:
        """Cria o usuário (se não existir) ao receber Hubla v2 customer.member_added.
        Espera payload no formato: { "type": "customer.member_added", "event": { "user": {"email": ...}, ... } }
        """
        try:
            # Extrair email do usuário. Preferir subscription.payer.email
            # pois o teste da Hubla muitas vezes popula apenas esse campo.
            user_email = None
            if isinstance(event_data, dict):
                if event_data.get('subscription', {}) and event_data.get('subscription', {}).get('payer', {}) and event_data.get('subscription', {}).get('payer', {}).get('email'):
                    user_email = event_data.get('subscription', {}).get('payer', {}).get('email')
                elif event_data.get('user', {}) and event_data.get('user', {}).get('email'):
                    user_email = event_data.get('user', {}).get('email')
                elif event_data.get('customer', {}) and event_data.get('customer', {}).get('email'):
                    user_email = event_data.get('customer', {}).get('email')

            if not user_email:
                print('Evento customer.member_added sem email do usuário')
                return False

            user_email = user_email.strip().lower()
            existing = User.get_by_email(user_email)
            if existing:
                return True

            # Criar usuário com senha temporária aleatória
            temp_password = generate_temp_password()
            User.create(user_email, temp_password)
            print(f"Usuário criado via Hubla member_added: {user_email}")
            return True
        except Exception as e:
            print(f"Erro ao processar member_added v2: {e}")
            return False

    def _create_license_from_v2(self, event_data: dict) -> bool:
        """Cria licença a partir de eventos v2 (subscription.activated, invoice.paid)."""
        try:
            # Extrair email (ordem de prioridade v2): invoice.payer.email > subscription.payer.email > event.payer.email > user.email
            buyer_email = None
            if isinstance(event_data, dict):
                invoice = event_data.get('invoice', {}) or {}
                inv_payer = invoice.get('payer', {}) if isinstance(invoice, dict) else {}
                buyer_email = (inv_payer or {}).get('email') or None

                if not buyer_email:
                    sub = event_data.get('subscription', {}) or {}
                    payer = sub.get('payer', {}) or {}
                    buyer_email = payer.get('email') or None

                if not buyer_email:
                    # Fallback: payer no nível do evento
                    evt_payer = event_data.get('payer', {}) or {}
                    buyer_email = evt_payer.get('email') or None

                if not buyer_email:
                    user = event_data.get('user', {}) or {}
                    buyer_email = user.get('email') or None

            if not buyer_email:
                print('Evento v2 sem email do comprador')
                return False

            buyer_email = buyer_email.strip().lower()

            # Extrair identificadores
            product_id = str((event_data.get('product', {}) or {}).get('id', '') or '')
            invoice = event_data.get('invoice', {}) or {}
            subscription = event_data.get('subscription', {}) or {}
            purchase_id = invoice.get('id') or subscription.get('id') or None

            # Datas
            purchase_date = (
                subscription.get('activatedAt')
                or invoice.get('paidAt')
                or invoice.get('createdAt')
                or invoice.get('updatedAt')
                or invoice.get('completedAt')
                or subscription.get('modifiedAt')
            )

            # Preço (para determinar tipo de licença)
            price = None
            amount = (invoice.get('amount') or {}) if isinstance(invoice, dict) else {}
            total_cents = (
                amount.get('totalCents')
                or amount.get('totalcents')
                or amount.get('valueCents')
            )
            if total_cents is not None:
                try:
                    price = float(total_cents) / 100.0
                except Exception:
                    price = None
            # Outros formatos possíveis
            if price is None:
                try:
                    price = float((invoice.get('price') or {}).get('value'))
                except Exception:
                    price = None

            if price is None:
                price = 297.00  # fallback seguro para anual

            if not all([buyer_email, purchase_id, product_id]):
                print(f"Dados insuficientes v2: email={buyer_email}, purchase_id={purchase_id}, product_id={product_id}, date={purchase_date}")
                return False

            # Definir tipo de licença
            license_type = 'anual' if float(price) >= 287.00 else 'semestral'

            # Verificar se já existe licença para esta compra
            conn = get_db_connection()
            existing = conn.execute(
                "SELECT id FROM licenses WHERE hotmart_purchase_id = ?",
                (purchase_id,)
            ).fetchone()
            conn.close()
            if existing:
                return True

            # Garantir usuário
            user = User.get_by_email(buyer_email)
            if not user:
                temp_password = generate_temp_password()
                user = User.create(buyer_email, temp_password)

            # Normalizar purchase_date
            from datetime import datetime
            if not purchase_date:
                purchase_date = datetime.utcnow().isoformat() + 'Z'

            License.create(user.id, purchase_id, product_id, license_type, purchase_date)
            print(f"Licença v2 criada para {buyer_email}: {license_type} - {purchase_id}")
            return True
        except Exception as e:
            print(f"Erro ao criar licença v2: {e}")
            return False


def generate_temp_password(length=12):
    """Gera uma senha temporária aleatória"""
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(characters) for _ in range(length))


class ScrapingJob:
    """Manages scraping jobs in the database"""
    
    @staticmethod
    def create(user_id: int, keyword: str, locations: list, total_results: int) -> int:
        """Create a new scraping job"""
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scraping_jobs (user_id, keyword, locations, total_results)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (user_id, keyword, json.dumps(locations), total_results)
            )
            job_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return job_id
    
    @staticmethod
    def update_status(job_id: int, status: str, progress: int = None, 
                     current_location: str = None, error_message: str = None):
        """Update job status and progress"""
        conn = get_db_connection()
        
        update_fields = ["status = %s"]
        params = [status]
        
        if progress is not None:
            update_fields.append("progress = %s")
            params.append(progress)
        
        if current_location is not None:
            update_fields.append("current_location = %s")
            params.append(current_location)
        
        if error_message is not None:
            update_fields.append("error_message = %s")
            params.append(error_message)
        
        if status == 'running' and 'started_at' not in [f.split(' = ')[0] for f in update_fields]:
            update_fields.append("started_at = %s")
            params.append(datetime.now().isoformat())
        
        if status in ['completed', 'failed']:
            update_fields.append("completed_at = %s")
            params.append(datetime.now().isoformat())
        
        params.append(job_id)
        
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE scraping_jobs SET {', '.join(update_fields)} WHERE id = %s",
                tuple(params)
            )
        conn.commit()
        conn.close()
    
    @staticmethod
    def set_results(job_id: int, results_path: str):
        """Set the results file path for a completed job"""
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scraping_jobs SET results_path = %s WHERE id = %s",
                (results_path, job_id)
            )
        conn.commit()
        conn.close()
    
    def get_by_id(job_id: int) -> dict:
        """Get job by ID"""
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM scraping_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_by_user_id(user_id: int, limit: int = 10) -> list:
        """Get recent jobs for a user"""
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM scraping_jobs 
                WHERE user_id = %s 
                ORDER BY created_at DESC 
                LIMIT %s
                """,
                (user_id, limit)
            )
            rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]


def run_scraping_job(job_id: int):
    """Run scraping job in background thread"""
    try:
        job = ScrapingJob.get_by_id(job_id)
        if not job:
            return
        
        ScrapingJob.update_status(job_id, 'running', 0)
        
        # Parse job data
        locations = json.loads(job['locations'])
        keyword = job['keyword']
        total_results = job['total_results']
        user_id = job['user_id']
        
        # Create queries for each location
        queries = [f"{keyword} in {loc}" for loc in locations]
        
        # Set up user directory
        user_base_dir = os.path.join(STORAGE_ROOT, str(user_id), "GMaps Data")
        
        # Run scraper with progress tracking
        results = run_scraper_with_progress(
            queries, 
            total=total_results, 
            headless=True, 
            save_base_dir=user_base_dir, 
            concatenate_results=True,
            progress_callback=lambda progress, current_loc: ScrapingJob.update_status(
                job_id, 'running', progress, current_loc
            )
        )
        
        if results and len(results) > 0:
            # Set results path
            results_path = results[0].get('csv_path', '')
            ScrapingJob.set_results(job_id, results_path)
            ScrapingJob.update_status(job_id, 'completed', 100)
        else:
            ScrapingJob.update_status(job_id, 'failed', error_message="No results generated")
            
    except Exception as e:
        ScrapingJob.update_status(job_id, 'failed', error_message=str(e))
        print(f"Scraping job {job_id} failed: {e}")


def run_scraping_job_async(job_id: int):
    """Start scraping job in background thread"""
    thread = threading.Thread(target=run_scraping_job, args=(job_id,))
    thread.daemon = True
    thread.start()


def send_reset_email(email, token):
    """Enfileira o envio de email com link de redefinição de senha"""
    try:
        reset_url = url_for('reset_password', token=token, _external=True)
        
        # HTML Body
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #4f7cff;">Redefinição de Senha</h2>
            <p>Olá!</p>
            <p>Você solicitou a redefinição de sua senha no Leads Infinitos.</p>
            <p>Clique no botão abaixo para criar uma nova senha:</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}" style="background-color: #4f7cff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold;">Redefinir Minha Senha</a>
            </div>
            <p>Ou copie e cole o link abaixo no seu navegador:</p>
            <p style="background-color: #f5f5f5; padding: 10px; font-family: monospace; word-break: break-all;">{reset_url}</p>
            <p><strong>Importante:</strong> Este link expira em 1 hora.</p>
            <p>Se você não solicitou esta redefinição, ignore este email.</p>
            <p>Atenciosamente,<br>Equipe Leads Infinitos</p>
        </div>
        """
        
        # Enfileirar tarefa no RQ
        from worker_email import send_email_task
        q.enqueue(send_email_task, email, 'Redefinição de Senha - Leads Infinitos', html_body)
        
        return True
    except Exception as e:
        print(f"Erro ao enfileirar email: {e}")
        return False


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
STORAGE_ROOT = os.environ.get("STORAGE_DIR", "storage")

# Configuração do Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

mail = Mail(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.get_by_id(int(user_id))
    except Exception:
        return None


# Inicializa o banco na carga da aplicação (Flask 3 removeu before_first_request)
# init_db()


@app.route("/", methods=["GET"]) 
@login_required
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"]) 
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Preencha email e senha.")
            return redirect(url_for("register"))
        if User.get_by_email(email):
            flash("Email já registrado.")
            return redirect(url_for("register"))
        
        # Verificar se o email tem uma compra válida na Hotmart
        try:
            hotmart_service = HotmartService()
            purchase_data = hotmart_service.verify_purchase(email)
            
            if not purchase_data:
                flash("Email não encontrado em nossas vendas. Verifique se você comprou o produto Leads Infinitos na Hotmart.")
                return redirect(url_for("register"))
            
            # Criar usuário
            user = User.create(email, password)
            
            # Criar licença baseada na compra
            price = float(purchase_data.get('price', 0))
            if price >= 287.00:  # Licença anual
                license_type = 'anual'
            else:  # Licença semestral
                license_type = 'semestral'
            
            License.create(
                user.id, 
                purchase_data['purchase_id'], 
                purchase_data['product_id'], 
                license_type, 
                purchase_data['purchase_date']
            )
            
            login_user(user)
            flash(f"Conta criada com sucesso! Sua licença {license_type} está ativa.")
            return redirect(url_for("index"))
            
        except Exception as e:
            flash(f"Erro ao verificar compra: {str(e)}. Entre em contato com o suporte.")
            return redirect(url_for("register"))
    
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"]) 
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.get_by_email(email)
        if not user or not check_password_hash(user.password_hash, password):
            flash("Credenciais inválidas.")
            return redirect(url_for("login"))
        login_user(user)
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout") 
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/healthz", methods=["GET"]) 
def healthz():
    return "ok", 200


@app.route("/scrape", methods=["POST"]) 
@login_required
def scrape():
    # Verificar se usuário tem licença ativa
    if not current_user.has_active_license():
        flash("Sua licença expirou ou não está ativa. Entre em contato com o suporte para renovar.")
        return redirect(url_for("index"))
    
    palavra_chave = request.form.get("palavra_chave", "").strip()
    localizacoes = request.form.getlist("localizacoes[]")  # Lista de localizações
    total_raw = request.form.get("total", "").strip() or "100"
    
    try:
        total = int(total_raw)
    except Exception:
        total = 100
    
    # Guardrails: clamp total and inputs
    total = max(1, min(total, 500))
    if len(palavra_chave) > 100:
        palavra_chave = palavra_chave[:100]
    
    # Validar entrada
    if not palavra_chave:
        flash("Por favor, preencha 'Palavra-chave'.")
        return redirect(url_for("index"))
    
    if not localizacoes or not any(loc.strip() for loc in localizacoes):
        flash("Por favor, adicione pelo menos uma localização.")
        return redirect(url_for("index"))
    
    # Limitar a 15 localizações
    localizacoes = [loc.strip() for loc in localizacoes if loc.strip()][:15]
    
    # Create background job
    job_id = ScrapingJob.create(
        user_id=current_user.id,
        keyword=palavra_chave,
        locations=localizacoes,
        total_results=total
    )
    
    # Start job in background (Queue)
    from worker_scraper import run_scraper_task
    q.enqueue(run_scraper_task, job_id)
    
    flash(f"Scraping enfileirado! Job ID: {job_id}. Você pode acompanhar o progresso na página de jobs.")
    return redirect(url_for("jobs"))


def _is_path_owned_by_current_user(path: str) -> bool:
    if not current_user.is_authenticated:
        return False
    user_root = os.path.abspath(os.path.join(STORAGE_ROOT, str(current_user.id)))
    abs_path = os.path.abspath(path)
    try:
        return os.path.commonpath([abs_path, user_root]) == user_root
    except Exception:
        return False


@app.route("/download") 
@login_required
def download():
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        flash("Arquivo não encontrado para download.")
        return redirect(url_for("index"))
    if not _is_path_owned_by_current_user(path):
        flash("Acesso negado ao arquivo solicitado.")
        return redirect(url_for("index"))
    filename = os.path.basename(path)
    return send_file(path, as_attachment=True, download_name=filename)




@app.route("/webhook/hubla", methods=["POST"])
def hubla_webhook():
    """Endpoint para receber webhooks da Hubla"""
    try:
        payload = request.get_json()
        # Hubla envia o token no header 'x-hubla-token' na UI. Mantemos compatibilidade com 'Authorization'.
        signature = (
            request.headers.get('Authorization')
            or request.headers.get('x-hubla-token')
            or request.headers.get('X-Hubla-Token')
            or ''
        )
        
        hubla_service = HublaService()
        success = hubla_service.process_webhook(payload, signature)
        
        if success:
            return {"status": "success"}, 200
        else:
            return {"status": "error", "message": "Failed to process webhook"}, 400
            
    except Exception as e:
        print(f"Erro no webhook da Hubla: {e}")
        return {"status": "error", "message": str(e)}, 500


@app.route("/licenses")
@login_required
def licenses():
    """Página para visualizar licenças do usuário"""
    user_licenses = License.get_by_user_id(current_user.id)
    return render_template("licenses.html", licenses=user_licenses)


@app.route("/api/verify-license")
@login_required
def verify_license():
    """API para verificar status da licença (usado por JavaScript)"""
    has_license = current_user.has_active_license()
    return {"has_active_license": has_license}


def send_async_email(app_instance, msg):
    with app_instance.app_context():
        try:
            mail.send(msg)
            print(f"📧 Email enviado para {msg.recipients} via SMTP")
        except Exception as e:
            print(f"❌ Erro ao enviar email: {e}")

def send_reset_email(to_email, token):
    """Envia email de redefinição de senha"""
    reset_url = url_for('reset_password', token=token, _external=True)
    msg = Message("Redefinição de Senha - Leads Infinitos", recipients=[to_email])
    msg.body = f"""Olá!

Recebemos uma solicitação para redefinir sua senha.
Clique no link abaixo para criar uma nova senha:

{reset_url}

Se você não solicitou isso, ignore este email. O link expira em 1 hora.

Atenciosamente,
Equipe Leads Infinitos"""
    
    # Enviar em thread separada para não bloquear
    threading.Thread(target=send_async_email, args=(app._get_current_object(), msg)).start()

def send_welcome_email(to_email, password):
    """Envia email de boas-vindas com credenciais"""
    login_url = url_for('login', _external=True)
    msg = Message("Bem-vindo ao Leads Infinitos!", recipients=[to_email])
    msg.body = f"""Olá!

Sua conta foi criada com sucesso após a confirmação do pagamento.
Aqui estão suas credenciais de acesso:

Email: {to_email}
Senha: {password}

Acesse em: {login_url}

Recomendamos que altere sua senha após o primeiro login.

Atenciosamente,
Equipe Leads Infinitos"""

    # Enviar em thread separada
    threading.Thread(target=send_async_email, args=(app._get_current_object(), msg)).start()



@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Página para solicitar reset de senha"""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        
        if not email:
            flash("Por favor, informe seu email.")
            return redirect(url_for("forgot_password"))
        
        # Verificar se o usuário existe
        user = User.get_by_email(email)
        
        if user:
            # Gerar token único
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(hours=1)
            
            # Salvar token no banco
            conn = get_db_connection()
            with conn.cursor() as cur:
                # Invalidar tokens anteriores
                cur.execute("UPDATE password_resets SET used = TRUE WHERE user_id = %s", (user.id,))
                # Criar novo token
                cur.execute(
                    """
                    INSERT INTO password_resets (user_id, token, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (user.id, token, expires_at)
                )
            conn.commit()
            conn.close()
            
            # Enviar email (agora assíncrono)
            send_reset_email(email, token)
            
        # SEMPRE retornar mensagem de sucesso para evitar enumeração de usuários
        flash("Se o email informado estiver cadastrado, você receberá um link para redefinir sua senha em instantes.")
        return redirect(url_for("login"))
    
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Página para definir nova senha usando token"""
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    # Verificar token
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id, expires_at, used 
            FROM password_resets 
            WHERE token = %s
            """, 
            (token,)
        )
        reset_data = cur.fetchone()
    conn.close()
    
    if not reset_data:
        flash("Link inválido ou inexistente.")
        return redirect(url_for("login"))
    
    user_id, expires_at, used = reset_data
    
    if used:
        flash("Este link já foi utilizado.")
        return redirect(url_for("login"))
        
    if datetime.now() > expires_at:
        flash("Este link expirou. Solicite um novo.")
        return redirect(url_for("forgot_password"))
    
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        if not password or not confirm_password:
            flash("Preencha todos os campos.")
            return render_template("reset_password.html", token=token)
            
        if password != confirm_password:
            flash("As senhas não coincidem.")
            return render_template("reset_password.html", token=token)
            
        if len(password) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.")
            return render_template("reset_password.html", token=token)
        
        # Atualizar senha e invalidar token
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (generate_password_hash(password), user_id)
            )
            cur.execute(
                "UPDATE password_resets SET used = TRUE WHERE token = %s",
                (token,)
            )
        conn.commit()
        conn.close()
        
        flash("Senha alterada com sucesso! Faça login com sua nova senha.")
        return redirect(url_for("login"))
        
    return render_template("reset_password.html", token=token)


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Página para alterar senha"""
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        if not all([current_password, new_password, confirm_password]):
            flash("Por favor, preencha todos os campos.")
            return redirect(url_for("change_password"))
        
        if new_password != confirm_password:
            flash("As senhas não coincidem.")
            return redirect(url_for("change_password"))
        
        if len(new_password) < 6:
            flash("A nova senha deve ter pelo menos 6 caracteres.")
            return redirect(url_for("change_password"))
        
        # Verificar senha atual
        if not check_password_hash(current_user.password_hash, current_password):
            flash("Senha atual incorreta.")
            return redirect(url_for("change_password"))
        
        # Atualizar senha
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (generate_password_hash(new_password), current_user.id)
            )
        conn.commit()
        conn.close()
        
        flash("Senha alterada com sucesso!")
        return redirect(url_for("index"))
    
    return render_template("change_password.html")


@app.route('/account')
@login_required
def account():
    # 1. Obter Licenças
    licenses = License.get_by_user_id(current_user.id)
    active_license = None
    for lic in licenses:
        if lic.status == 'active':
            active_license = lic
            break
            
    # 2. Obter Instância WhatsApp
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM instances WHERE user_id = %s", (current_user.id,))
        instance = cur.fetchone()
    conn.close()
    
    return render_template('account.html', user=current_user, license=active_license, instance=instance)

@app.route('/campaigns')
@login_required
def campaigns():
    user_campaigns = Campaign.get_by_user(current_user.id)
    return render_template('campaigns_list.html', campaigns=user_campaigns)


@app.route('/campaigns/delete/<int:campaign_id>', methods=['POST'])
@login_required
def delete_campaign(campaign_id):
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        flash("Campanha não encontrada.", "error")
        return redirect(url_for('campaigns'))
    
    if campaign.delete():
        flash("Campanha excluída com sucesso!", "success")
    else:
        flash("Erro ao excluir campanha.", "error")
        
    return redirect(url_for('campaigns'))

# --- Rotas de Admin ---

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Estatísticas Gerais
        cur.execute("SELECT COUNT(*) as count FROM users")
        total_users = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM licenses WHERE status = 'active'")
        active_licenses = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM campaigns")
        total_campaigns = cur.fetchone()['count']

        cur.execute("SELECT COUNT(*) as count FROM campaign_leads WHERE status = 'sent'")
        total_sent = cur.fetchone()['count']
        
    conn.close()
    
    return render_template('admin/dashboard.html', 
                         total_users=total_users, 
                         active_licenses=active_licenses,
                         total_campaigns=total_campaigns,
                         total_sent=total_sent)

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Buscar usuários com info de licença - usando JOIN LATERAL ou DISTINCT ON
        # A duplicação ocorria pois usuários tinham multiplas instancias. Vamos pegar a mais recente.
        cur.execute("""
            SELECT u.id, u.email, u.is_admin, u.created_at,
                   l.license_type, l.status as license_status, l.expires_at,
                   i.name as instance_name, i.status as instance_status, i.apikey as instance_apikey
            FROM users u
            LEFT JOIN (
                SELECT DISTINCT ON (user_id) *
                FROM licenses
                ORDER BY user_id, created_at DESC
            ) l ON u.id = l.user_id
            LEFT JOIN (
                SELECT DISTINCT ON (user_id) *
                FROM instances
                ORDER BY user_id, updated_at DESC
            ) i ON u.id = i.user_id
            ORDER BY u.created_at DESC
        """)
        users = cur.fetchall()
    conn.close()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/<int:user_id>/toggle_admin', methods=['POST'])
@login_required
@admin_required
def admin_toggle_admin(user_id):
    if user_id == current_user.id:
        flash("Você não pode alterar seu próprio status de admin.", "error")
        return redirect(url_for('admin_users'))

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET is_admin = NOT is_admin WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    
    flash("Status de admin atualizado com sucesso!", "success")
    return redirect(url_for('admin_users'))

@app.route('/admin/licenses/create', methods=['POST'])
@login_required
@admin_required
def admin_create_license():
    user_id = request.form.get('user_id')
    license_type = request.form.get('license_type')
    
    if not user_id or not license_type:
        flash("Dados inválidos.", "error")
        return redirect(url_for('admin_users'))
        
    # Validar user_id
    user = User.get_by_id(user_id)
    if not user:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for('admin_users'))

    try:
        # Criar licença manual
        import datetime
        from datetime import datetime
        
        # Revogar licenças anteriores se houver
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE licenses SET status = 'cancelled' WHERE user_id = %s", (user.id,))
        conn.commit()
        conn.close()

        # Gerar IDs fictícios para compra manual
        purchase_id = f"MANUAL-{secrets.token_hex(8)}"
        product_id = "MANUAL-GRANT"
        purchase_date = datetime.utcnow().isoformat()
        
        License.create(user.id, purchase_id, product_id, license_type, purchase_date)
        
        flash(f"Plano {license_type} definido para {user.email}.", "success")
    except Exception as e:
        print(f"Erro ao criar licença manual: {e}")
        flash("Erro ao criar licença.", "error")
        
    return redirect(url_for('admin_users'))

@app.route('/admin/licenses/<int:license_id>/revoke', methods=['POST'])
@login_required
@admin_required
def admin_revoke_license(license_id):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE licenses SET status = 'cancelled' WHERE id = %s", (license_id,))
    conn.commit()
    conn.close()
    
    flash("Licença revogada com sucesso!", "success")
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/reset_password', methods=['POST'])
@login_required
@admin_required
def admin_reset_password(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
        
    try:
        # Gerar nova senha
        new_password = secrets.token_urlsafe(10)
        password_hash = generate_password_hash(new_password)
        
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
        conn.commit()
        conn.close()
        
        # TODO: Enviar email via SMTP (Simulado no print por enquanto)
        print(f"PASSWORD RESET FOR {user.email}: {new_password}")
        
        # Em produção, usar Flask-Mail aqui
        # msg = Message("Sua nova senha - Leads Infinitos", recipients=[user.email])
        # msg.body = f"Sua senha foi resetada. Nova senha: {new_password}"
        # mail.send(msg)
        
        return {"success": True, "message": "Senha resetada e enviada por email (simulado).", "new_password": new_password}
    except Exception as e:
        print(f"Erro no reset de senha: {e}")
        return {"error": str(e)}, 500

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    if user_id == current_user.id:
        return {"error": "Você não pode excluir a si mesmo."}, 400
        
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Excluir dependências MANUALMENTE (Cascata)
            
            # 1. Obter IDs das campanhas do usuário
            cur.execute("SELECT id FROM campaigns WHERE user_id = %s", (user_id,))
            campaign_ids = [row[0] for row in cur.fetchall()]
            
            if campaign_ids:
                # 2. Excluir leads das campanhas
                cur.execute("DELETE FROM campaign_leads WHERE campaign_id = ANY(%s)", (campaign_ids,))
                # Excluir steps das campanhas (se existir tabela, garantindo limpeza)
                # cur.execute("DELETE FROM campaign_steps WHERE campaign_id = ANY(%s)", (campaign_ids,))
            
            # 3. Excluir campanhas
            cur.execute("DELETE FROM campaigns WHERE user_id = %s", (user_id,))
            
            # 4. Outras dependências diretas
            cur.execute("DELETE FROM licenses WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM instances WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM scraping_jobs WHERE user_id = %s", (user_id,))
            
            # 5. Excluir o usuário
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        print(f"Erro ao excluir usuário: {e}")
        conn.rollback() if 'conn' in locals() and conn else None
        return {"error": str(e)}, 500

@app.route('/admin/users/<int:user_id>/details')
@login_required
@admin_required
def admin_user_details(user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Info básica e WhatsApp (mais recente)
        cur.execute("""
            SELECT u.id, u.email, u.created_at, u.is_admin,
                   i.name as instance_name, i.status as instance_status, i.apikey as instance_apikey
            FROM users u
            LEFT JOIN (
                 SELECT DISTINCT ON (user_id) *
                 FROM instances
                 ORDER BY user_id, updated_at DESC
            ) i ON u.id = i.user_id
            WHERE u.id = %s
        """, (user_id,))
        user = cur.fetchone()
        
        if not user:
            conn.close()
            return {"error": "User not found"}, 404
            
        # Info licença
        cur.execute("""
            SELECT * FROM licenses 
            WHERE user_id = %s AND status = 'active' 
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,))
        license = cur.fetchone()
        
    conn.close()
    
    return {
        "user": {
            "id": user['id'],
            "email": user['email'],
            "created_at": user['created_at'].isoformat() if user['created_at'] else None,
            "is_admin": user['is_admin'],
            "instance_name": user['instance_name'],
            "instance_status": user['instance_status'],
            "instance_apikey": user['instance_apikey']
        },
        "license": {
            "type": license['license_type'] if license else None,
            "expires_at": license['expires_at'].isoformat() if license and license['expires_at'] else None
        } if license else None
    }
    
@app.route('/admin/whatsapp/check_status/<instance_apikey>', methods=['POST'])
@login_required
@admin_required
def admin_check_whatsapp_status(instance_apikey):
    """Admin endpoint to check/update status of a whatsapp instance"""
    service = WhatsappService()
    result = service.get_status(instance_apikey)
    
    if not result:
        return {"error": "Failed to verify status"}, 400
        
    # Logic similar to get_whatsapp_status but for admin
    # MegaAPI Structure variations:
    # 1. { "instance_data": { "phone_connected": true, ... } }
    # 2. { "phone_connected": true, ... } (sometimes top-level in some versions)
    # 3. [ { ... } ] (Array if looking up by key)
    
    is_connected = False
    
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
        
    if result.get('instance_data'):
        is_connected = result['instance_data'].get('phone_connected', False)
    elif 'phone_connected' in result:
        is_connected = result.get('phone_connected', False)
    elif result.get('status') == 'CONNECTED': # Alternative API behavior
        is_connected = True
        
    if result.get('error'):
         is_connected = False
         
    new_status = 'connected' if is_connected else 'disconnected'
    
    # Update DB
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE instances SET status = %s WHERE apikey = %s", (new_status, instance_apikey))
    conn.commit()
    conn.close()
    
    # Debug print
    print(f"Admin Checked Status for {instance_apikey}: {new_status} (Raw: {is_connected})")
    
    return {"status": new_status, "result": result}
@app.route('/campaigns/new')
@login_required
def new_campaign():
    return render_template('campaigns_new.html')

@app.route('/api/scraping-jobs')
@login_required
def api_scraping_jobs():
    """Retorna jobs completados para o select na UI de Campanhas"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, keyword, locations, total_results, created_at 
                FROM scraping_jobs 
                WHERE user_id = %s AND status = 'completed' 
                ORDER BY created_at DESC
                """,
                (current_user.id,)
            )
            jobs = cur.fetchall()
        conn.close()
        return json.dumps([dict(j, created_at=j['created_at'].isoformat()) for j in jobs], default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}), 500

@app.route('/api/ai/generate-copy', methods=['POST'])
@login_required
def generate_ai_copy():
    """Gera mensagem persuasiva usando IA para campanhas WhatsApp"""
    try:
        data = request.json
        business_context = data.get('business_context', '').strip()
        
        if not business_context:
            return json.dumps({'error': 'Por favor, descreva seu negócio e produto/serviço'}), 400
        
        # Configurar cliente OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            return json.dumps({'error': 'API Key do OpenAI não configurada'}), 500
        
        client = OpenAI(api_key=api_key)
        
        # Prompt otimizado para cold-outreach no WhatsApp
        prompt = f"""Você é um especialista em copywriting para WhatsApp cold-outreach B2B.

CONTEXTO DO NEGÓCIO:
{business_context}

INSTRUÇÕES:
1. Crie uma mensagem de prospecção curta e direta (máximo 3-4 linhas)
2. Use linguagem natural, informal mas profissional (você/tu, não "vossa empresa")
3. OBRIGATÓRIO: Use {{nome}} no início para personalização
4. Foque em despertar curiosidade ou oferecer valor imediato
5. Evite palavras "spam" como "promoção", "desconto imperdível", "clique já"
6. Termine com uma pergunta ou CTA sutil
7. NÃO use emojis excessivos (máximo 1)
8. Seja específico sobre o benefício para o prospect

Retorne APENAS a mensagem, sem explicações ou aspas."""

        # Chamar API do OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Você é um expert em copywriting para WhatsApp B2B. Suas mensagens são curtas, naturais e altamente conversíveis."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,  # Criatividade balanceada
            max_tokens=150
        )
        
        generated_message = response.choices[0].message.content.strip()
        
        # Garantir que {nome} está presente
        if '{nome}' not in generated_message.lower():
            # Adicionar {nome} no início se não estiver presente
            generated_message = f"Olá {{nome}}, {generated_message[0].lower()}{generated_message[1:]}"
        
        return json.dumps({'message': generated_message})
        
    except Exception as e:
        print(f"Erro ao gerar copy com IA: {e}")
        return json.dumps({'error': f'Erro na geração de IA: {str(e)}'}), 500

@app.route('/api/upload-csv-leads', methods=['POST'])
@login_required
def upload_csv_leads():
    if 'file' not in request.files:
        return json.dumps({'error': 'Nenhum arquivo enviado'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return json.dumps({'error': 'Arquivo vazio'}), 400
        
    if not file.filename.endswith('.csv'):
        return json.dumps({'error': 'Apenas arquivos .csv são permitidos'}), 400
        
    try:
        # Salvar arquivo
        user_dir = os.path.join(os.environ.get("STORAGE_DIR", "storage"), str(current_user.id), "Uploads")
        os.makedirs(user_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"upload_{timestamp}_{file.filename}"
        filepath = os.path.join(user_dir, filename)
        
        file.save(filepath)
        
        # Analisar o arquivo para contar leads
        df = pd.read_csv(filepath)
        
        # Adicionar coluna 'status' se não existir (valor 1 = pronto para envio)
        if 'status' not in [c.lower() for c in df.columns]:
            df['status'] = 1
            # Salvar novamente com a coluna status
            df.to_csv(filepath, index=False)
        
        # Tentar identificar colunas
        cols = [c.lower() for c in df.columns]
        phone_col = next((c for c in cols if 'phone' in c or 'tel' in c or 'cel' in c or 'whatsapp' in c), None)
        
        count = 0
        if phone_col:
             # Contar válidos (apenas estimativa rápida)
             count = int(df[df.columns[cols.index(phone_col)]].notna().sum())
        else:
             count = int(len(df))
        
        # Criar registro de Job "Fake" para rastreabilidade
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scraping_jobs 
                (user_id, keyword, locations, total_results, status, results_path, progress, completed_at)
                VALUES (%s, %s, %s, %s, 'completed', %s, 100, NOW())
                RETURNING id
                """,
                (current_user.id, f"Upload: {file.filename}", "Arquivo Local", count, filepath)
            )
            job_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        return json.dumps({
            'success': True, 
            'job_id': job_id, 
            'total_leads': int(count)
        })
        
    except Exception as e:
        print(f"Erro no upload: {e}")
        return json.dumps({'error': str(e)}), 500

@app.route('/api/campaigns', methods=['POST'])
@login_required
def create_campaign():
    data = request.json
    name = data.get('name')
    job_id = data.get('job_id')
    # Pode receber 'message_template' (string única) ou 'message_templates' (lista)
    # Vamos padronizar salvando como JSON se for lista, ou string se for único.
    # Mas para "rotação", o ideal seria salvar uma lista JSON.
    message_templates = data.get('message_templates', [])
    if not message_templates and data.get('message_template'):
        message_templates = [data.get('message_template')]
        
    # Serializar para salvar no banco
    message_template_json = json.dumps(message_templates)
    
    # NEW: Get scheduled_start from request (optional)
    scheduled_start = data.get('scheduled_start')  # ISO format string or None
    
    # NEW: Validate scheduled_start if provided
    if scheduled_start:
        try:
            from datetime import datetime
            # Just validate format, don't check if future (browser already does this)
            # Parse to ensure it's valid ISO format
            datetime.fromisoformat(scheduled_start.replace('Z', ''))
        except Exception as e:
            return json.dumps({'error': f'Data inválida: {str(e)}'}), 400
    
    if not name or not job_id:
        return json.dumps({'error': 'Nome e Job são obrigatórios'}), 400
        
    try:
        # 1. Obter leads do Job
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT results_path FROM scraping_jobs WHERE id = %s AND user_id = %s", (job_id, current_user.id))
            job = cur.fetchone()
        conn.close()
        
        if not job or not job['results_path'] or not os.path.exists(job['results_path']):
            return json.dumps({'error': 'Arquivo de leads não encontrado'}), 404
            
        # 2. Ler arquivo de resultados (CSV ou XLSX, mas o path aponta pra CSV geralmente)
        # Fix: O scraper salva em CSV, não JSON.
        file_path = job['results_path']
        valid_leads = []
        
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            elif file_path.endswith('.xlsx'):
                df = pd.read_excel(file_path)
            else:
                 # Fallback: tentar ler como CSV
                df = pd.read_csv(file_path)
            
            # Normalizar colunas
            # O scraper gera: name, phone_number, etc.
            # O CSV pode ter colunas diferentes se vier de upload.
            
            # Map columns to expected: name, phone
            # Scraper output: 'name', 'phone_number'
            # Upload possible: 'nome', 'telefone', 'celular', 'phone'
            
            cols = [c.lower() for c in df.columns]
            df.columns = cols
            
            # Identificar coluna de telefone
            phone_col = next((c for c in cols if 'phone' in c or 'tel' in c or 'cel' in c), None)
            # Não incluir 'whatsapp' genérico na busca de phone_col, pois whatsapp_link é separado
            name_col = next((c for c in cols if 'name' in c or 'nome' in c or 'title' in c), None)
            whatsapp_link_col = next((c for c in cols if c == 'whatsapp_link'), None)
            status_col = next((c for c in cols if c == 'status'), None)
            
            if not phone_col:
                 return json.dumps({'error': 'Coluna de telefone não encontrada no arquivo'}), 400
            
            # Filtrar apenas leads com status = 1 (ou sem coluna status)
            if status_col:
                df_filtered = df[df[status_col] == 1]
            else:
                df_filtered = df
                 
            for _, row in df_filtered.iterrows():
                raw_phone = str(row[phone_col]) if pd.notna(row[phone_col]) else ""
                raw_name = str(row[name_col]) if name_col and pd.notna(row[name_col]) else "Visitante"
                raw_whatsapp_link = str(row[whatsapp_link_col]) if whatsapp_link_col and pd.notna(row[whatsapp_link_col]) else None
                
                if raw_phone:
                     clean_phone = re.sub(r'\D', '', raw_phone)
                     if len(clean_phone) >= 10:
                        valid_leads.append({
                            'phone': clean_phone,
                            'name': raw_name,
                            'whatsapp_link': raw_whatsapp_link
                        })

        except Exception as e:
            print(f"Erro ao ler arquivo: {e}")
            return json.dumps({'error': f'Erro ao ler arquivo: {str(e)}'}), 500
        
        if not valid_leads:
            return json.dumps({'error': 'Nenhum lead válido encontrado na lista'}), 400

        # 4. Criar Campanha
        # NEW: Create campaign with scheduled_start and dynamic status
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Determine initial status based on scheduled_start
            initial_status = 'pending' if scheduled_start else 'running'
            
            cur.execute(
                """
                INSERT INTO campaigns (user_id, name, message_template, daily_limit, scheduled_start, status)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at
                """,
                (current_user.id, name, message_template_json, 100, scheduled_start, initial_status)
            )
            row = cur.fetchone()
            campaign_id = row[0]
            created_at = row[1]
        conn.commit()
        conn.close()
        
        # 5. Adicionar Leads
        CampaignLead.add_leads(campaign_id, valid_leads)
        
        return json.dumps({'success': True, 'campaign_id': campaign_id, 'leads_count': len(valid_leads)})
        
    except Exception as e:
        print(f"Erro ao criar campanha: {e}")
        return json.dumps({'error': str(e)}), 500

@app.route("/campaigns")
@login_required
def campaigns_list():
    """Página para visualizar lista de campanhas"""
    campaigns = Campaign.get_by_user(current_user.id)
    return render_template("campaigns_list.html", campaigns=campaigns)


@app.route("/dashboard")
@login_required
def dashboard():
    """Página de dashboard geral"""
    return render_template("dashboard.html")


@app.route("/jobs")
@login_required
def jobs():
    """Página para visualizar jobs de scraping"""
    user_jobs = ScrapingJob.get_by_user_id(current_user.id, limit=20)
    
    # Processar jobs para o template
    processed_jobs = []
    for job in user_jobs:
        job_dict = dict(job)
        # Parse locations JSON
        try:
            locations = json.loads(job['locations']) if job['locations'] else []
            job_dict['locations_count'] = len(locations)
        except:
            job_dict['locations_count'] = 0
        processed_jobs.append(job_dict)
    
    return render_template("jobs.html", jobs=processed_jobs)


@app.route("/api/job/<int:job_id>")
@login_required
def get_job_status(job_id):
    """API para obter status de um job"""
    job = ScrapingJob.get_by_id(job_id)
    if not job or job['user_id'] != current_user.id:
        return {"error": "Job not found"}, 404
    
    return {
        "id": job['id'],
        "status": job['status'],
        "progress": job['progress'],
        "current_location": job['current_location'],
        "error_message": job['error_message'],
        "results_path": job['results_path'],
        "created_at": job['created_at'],
        "started_at": job['started_at'],
        "completed_at": job['completed_at']
    }


@app.route("/api/job/<int:job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id):
    """API para cancelar um job"""
    job = ScrapingJob.get_by_id(job_id)
    if not job or job['user_id'] != current_user.id:
        return {"error": "Job not found"}, 404
        
    if job['status'] in ['completed', 'failed']:
        return {"error": "Job already finished"}, 400

    # Sinalizar cancelamento no Redis (se estiver usando RQ, podemos tentar cancelar o job)
    # Como o worker roda jobs do RQ, precisamos saber o Job ID do RQ.
    # Por simplificação, vamos setar status 'cancelled' no DB e o worker deve checar.
    ScrapingJob.update_status(job_id, 'cancelled', error_message='Cancelado pelo usuário')
    return {"status": "cancelled"}

@app.route("/api/job/<int:job_id>", methods=["DELETE"])
@login_required
def delete_job(job_id):
    """API para excluir um job"""
    job = ScrapingJob.get_by_id(job_id)
    if not job or job['user_id'] != current_user.id:
        return {"error": "Job not found"}, 404
        
    # Excluir arquivos se existirem
    if job['results_path'] and os.path.exists(job['results_path']):
        try:
            os.remove(job['results_path'])
            # Tentar remover .xlsx também se houver
            xlsx_path = job['results_path'].replace('.csv', '.xlsx')
            if os.path.exists(xlsx_path):
                os.remove(xlsx_path)
        except:
            pass
            
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM scraping_jobs WHERE id = %s", (job_id,))
    conn.commit()
    conn.close()
    
    return {"status": "deleted"}

@app.route("/download")
@login_required
def download_file():
    path = request.args.get('path')
    if not path:
        return "Path required", 400
        
    if not os.path.exists(path):
        return "File not found", 404
        
    return send_file(path, as_attachment=True)


class WhatsappService:
    def __init__(self):
        self.base_url = os.environ.get('MEGA_API_URL', 'https://ruker.megaapi.com.br')
        self.token = os.environ.get('MEGA_API_TOKEN', '')
        self.headers = {
            'Authorization': self.token,
            'Content-Type': 'application/json'
        }

    def create_instance(self, instance_name: str = None) -> dict:
        """Creates a new WhatsApp instance on Mega API"""
        url = f"{self.base_url}/rest/instance/init"
        params = {}
        if instance_name:
            params['instance_key'] = instance_name
        
        # Payload from user validation
        payload = {
            "messageData": {
                "webhookUrl": "",
                "webhookEnabled": True
            }
        }

        try:
            response = requests.post(url, params=params, json=payload, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            # Debug
            print(f"Mega API Response: {response.text}")
            
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error creating instance: {e}")
            if e.response:
                print(f"Response: {e.response.text}")
            return None

    def get_qr_code(self, instance_key: str) -> dict:
        """Gets QR Code for the instance"""
        url = f"{self.base_url}/rest/instance/qrcode/{instance_key}"
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            try:
                return response.json()
            except requests.exceptions.JSONDecodeError:
                # Some endpoints return raw strings or HTML
                return {"data": response.text}
        except requests.exceptions.RequestException as e:
            print(f"Error getting QR code: {e}")
            return None

    def get_status(self, instance_key: str) -> dict:
        """Gets instance connection status"""
        url = f"{self.base_url}/rest/instance/{instance_key}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            # API might return a list [ {InstanceObject} ]
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data
        except requests.exceptions.RequestException as e:
            print(f"Error getting status: {e}")
            return None


@app.route("/whatsapp")
@login_required
def whatsapp_config():
    """Page to configure WhatsApp instance"""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM instances WHERE user_id = %s", (current_user.id,))
        instance = cur.fetchone()
    conn.close()
    
    return render_template("whatsapp_config.html", instance=instance)


@app.route("/api/whatsapp/init", methods=["POST"])
@login_required
def init_whatsapp():
    """API to initialize a WhatsApp instance"""
    instance_name = request.json.get("instance_name") or ""
    
    # Check if user already has an instance
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT name, apikey FROM instances WHERE user_id = %s", (current_user.id,))
        existing_instance = cur.fetchone()
    conn.close()

    if existing_instance:
        # User already has an instance.
        # If the goal is to limit to 1, we should block creation of a new one unless they delete/disconnect.
        # However, "Reconfigurar Instancia" button in UI calls this same endpoint.
        # If the user is reconfiguring, they might be sending the SAME name or a NEW name.
        # Validating request intent might be complex without a flag.
        # But if the user simply clicked "Criar" (or in this case "Reconfigurar" which hits /init), 
        # and we want to prevent creating a *duplicate* or losing the old one unintentionally?
        
        # User request: "return a message of 'instance already created. Instance Name: XYZ'"
        # This implies stopping the action.
        
        # But wait, if I block this, the "Reconfigurar" button won't work if it hits this endpoint 
        # to Create a new instance (which overwrites the old one in DB due to UPSERT logic below).
        
        # If the UI button says "Reconfigurar", maybe we destroy the old one first? 
        # Current UI logic: 
        # <button ... onclick="createInstance()"> ... </button>
        # createInstance() calls /api/whatsapp/init.
        
        # If I return error here, reconfiguring becomes impossible without deleting first.
        # Maybe I should only check if the connection to Mega API implies existence? 
        
        # Interpretation: The User wants to avoid ACCIDENTAL creation or abuse.
        # But since we support "Reconfigurar", maybe we only block if the instance name is DIFFERENT?
        # Or maybe we explicitly require a "force" flag?
        
        # User said: "limit only one instance created per user... if user tries to create... return message"
        # I will implement the check. If the user wants to reconfigure, they might need to use a different flow 
        # or I'll assume for now this blocks it. 
        # NOTE: This WILL BREAK "Reconfigurar" unless we handle it. 
        # But user asked for this limitation. I will implement as requested. 
        # If they complain "I can't reconfigure", we can add a "force=true" param later.
        
        return {
            "error": f"Você já possui uma instância criada. Nome: {existing_instance[0]} ({existing_instance[1]})"
        }, 400

    
    # Sanitize if provided
    safe_name = ""
    if instance_name:
        safe_name = "".join(c for c in instance_name if c.isalnum() or c in ('-', '_'))
    
    service = WhatsappService()
    result = service.create_instance(safe_name if safe_name else None)
    
    if result:
        # Check API level error
        if result.get('error') is True:
             # Even if 200 OK, logic error might exist
             # But usually 'error': false means success.
             # Note: User json shows "error": false.
             pass

        # Save to DB
        # Extract key from data object
        # structure: { ..., "data": { "instance_key": "..." }, ... }
        instance_key = result.get('data', {}).get('instance_key')
        
        # Fallback 1: Top level (sometimes APIs vary)
        if not instance_key:
            instance_key = result.get('instance_key')
            
        # Fallback 2: safe_name if we sent it and API didn't return it but succeeded
        if not instance_key and safe_name:
             # Check if response implies success
             if result.get('message') == 'Instance created' or result.get('error') is False:
                 instance_key = safe_name

        if not instance_key:
             print(f"Warning: No key returned from Mega API. Result: {result}")
             return {"error": "Falha ao obter ID da instância. Resposta da API inválida."}, 500

        conn = get_db_connection()
        with conn.cursor() as cur:
            # Upsert instance for user (assuming 1 instance per user for MVP)
            cur.execute(
                """
                INSERT INTO instances (user_id, name, apikey, status)
                VALUES (%s, %s, %s, 'disconnected')
                ON CONFLICT (id) DO UPDATE 
                SET name = EXCLUDED.name, apikey = EXCLUDED.apikey, status = 'disconnected', updated_at = CURRENT_TIMESTAMP
                """,
                (current_user.id, instance_name, instance_key)
            )
            # If there's a constraint on user_id, we might want to check existence first or add unique constraint.
            # detailed implementation: Check if user already has an instance
            cur.execute("SELECT id FROM instances WHERE user_id = %s", (current_user.id,))
            existing = cur.fetchone()
            if existing:
                 cur.execute(
                    "UPDATE instances SET name = %s, apikey = %s, status = 'disconnected', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (instance_name, instance_key, existing[0])
                )
            else:
                 cur.execute(
                    "INSERT INTO instances (user_id, name, apikey, status) VALUES (%s, %s, %s, 'disconnected')",
                    (current_user.id, instance_name, instance_key)
                )

        conn.commit()
        conn.close()
        
        return {"status": "success", "key": instance_key, "data": result}
    
    return {"error": "Failed to create instance at provider"}, 500


@app.route("/api/whatsapp/qr/<instance_key>")
@login_required
def get_whatsapp_qr(instance_key):
    """API to get QR code"""
    # Verify ownership
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM instances WHERE apikey = %s", (instance_key,))
        row = cur.fetchone()
    conn.close()
    
    if not row or row[0] != current_user.id:
        return {"error": "Unauthorized"}, 403
        
    service = WhatsappService()
    result = service.get_qr_code(instance_key)
    
    if result:
        # Check if we received HTML in 'data' field (Mega API behavior seen in logs)
        # Format: {"data": "<!DOCTYPE html>... <img src=\"data:image/png;base64,...\"> ..."}
        html_data = result.get('data')
        if html_data and isinstance(html_data, str) and '<img' in html_data:
            # Extract base64 src
            match = re.search(r'src=["\']data:image/png;base64,([^"\']+)["\']', html_data)
            if match:
                return {"base64": match.group(1)}
        
        # Fallback: return result as is if it follows another format
        return result
    return {"error": "Failed to get QR code"}, 500


@app.route("/api/whatsapp/status/<instance_key>")
@login_required
def get_whatsapp_status(instance_key):
    """API to get status"""
    # Verify ownership
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id FROM instances WHERE apikey = %s", (instance_key,))
        row = cur.fetchone()
    conn.close()
    
    if not row or row[1] != current_user.id:
        return {"error": "Unauthorized"}, 403
        
    service = WhatsappService()
    result = service.get_status(instance_key)
    
    if result:
        # Update DB status if possible
        # Mega API result like: {"instance_key": "...", "phone_connected": true/false, "user": {...}}
        # We can try to infer status
        is_connected = result.get('instance_data', {}).get('phone_connected') 
        # Note: Structure might vary, trusting 'phone_connected' or analyzing payload.
        # Fallback: check if result has 'error' or 'status' field.
        
        new_status = 'connected' if is_connected else 'disconnected'
        
        # Also Update DB
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE instances SET status = %s WHERE id = %s", (new_status, row[0]))
        conn.commit()
        conn.close()
        
        return result
    return {"error": "Failed to get status"}, 500


@app.route("/api/campaigns/<int:campaign_id>/stats")
@login_required
def get_campaign_stats(campaign_id):
    """API para obter estatísticas de uma campanha"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Verificar se a campanha pertence ao usuário
            cur.execute(
                "SELECT id, closed_deals FROM campaigns WHERE id = %s AND user_id = %s",
                (campaign_id, current_user.id)
            )
            campaign = cur.fetchone()
            
            if not campaign:
                conn.close()
                return {"error": "Campaign not found"}, 404
            
            closed_deals = campaign['closed_deals'] or 0
            
            # Buscar estatísticas de leads
            cur.execute(
                """
                SELECT 
                    COUNT(*) as total_leads,
                    COUNT(CASE WHEN status = 'sent' THEN 1 END) as sent,
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COUNT(CASE WHEN status = 'invalid' THEN 1 END) as invalid,
                    MIN(sent_at) as started_at,
                    MAX(sent_at) as last_sent_at
                FROM campaign_leads
                WHERE campaign_id = %s
                """,
                (campaign_id,)
            )
            stats = cur.fetchone()
        
        conn.close()
        
        # Calcular taxa de conversão
        sent = stats['sent'] or 0
        conversion_rate = round((closed_deals / sent * 100), 1) if sent > 0 else 0
        
        return {
            "total_leads": stats['total_leads'] or 0,
            "sent": sent,
            "pending": stats['pending'] or 0,
            "failed": stats['failed'] or 0,
            "invalid": stats['invalid'] or 0,
            "closed_deals": closed_deals,
            "conversion_rate": conversion_rate,
            "started_at": stats['started_at'].isoformat() if stats['started_at'] else None,
            "last_sent_at": stats['last_sent_at'].isoformat() if stats['last_sent_at'] else None
        }
        
    except Exception as e:
        print(f"Erro ao obter stats da campanha: {e}")
        return {"error": str(e)}, 500


@app.route("/api/campaigns/<int:campaign_id>/deal", methods=["POST"])
@login_required
def update_campaign_deal(campaign_id):
    """API para incrementar/decrementar negócios fechados de uma campanha"""
    try:
        data = request.json
        action = data.get('action')  # 'increment' ou 'decrement'
        
        if action not in ['increment', 'decrement']:
            return {"error": "Invalid action. Use 'increment' or 'decrement'"}, 400
        
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Verificar se a campanha pertence ao usuário
            cur.execute(
                "SELECT id FROM campaigns WHERE id = %s AND user_id = %s",
                (campaign_id, current_user.id)
            )
            campaign = cur.fetchone()
            
            if not campaign:
                conn.close()
                return {"error": "Campaign not found"}, 404
            
            # Incrementar ou decrementar
            if action == 'increment':
                cur.execute(
                    "UPDATE campaigns SET closed_deals = closed_deals + 1 WHERE id = %s RETURNING closed_deals",
                    (campaign_id,)
                )
            else:  # decrement
                cur.execute(
                    "UPDATE campaigns SET closed_deals = GREATEST(closed_deals - 1, 0) WHERE id = %s RETURNING closed_deals",
                    (campaign_id,)
                )
            
            result = cur.fetchone()
            new_value = result['closed_deals']
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "closed_deals": new_value
        }
        
    except Exception as e:
        print(f"Erro ao atualizar deals da campanha: {e}")
        return {"error": str(e)}, 500


@app.route("/api/dashboard/overview")
@login_required
def get_dashboard_overview():
    """API para obter visão geral do dashboard do usuário"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Total de campanhas
            cur.execute(
                "SELECT COUNT(*) as total FROM campaigns WHERE user_id = %s",
                (current_user.id,)
            )
            total_campaigns = cur.fetchone()['total']
            
            # Campanhas ativas
            cur.execute(
                "SELECT COUNT(*) as active FROM campaigns WHERE user_id = %s AND status = 'running'",
                (current_user.id,)
            )
            active_campaigns = cur.fetchone()['active']
            
            # Leads extraídos hoje
            cur.execute(
                """
                SELECT COUNT(*) as count FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s AND DATE(c.created_at) = CURRENT_DATE
                """,
                (current_user.id,)
            )
            today_leads = cur.fetchone()['count']
            
            # Mensagens enviadas hoje
            cur.execute(
                """
                SELECT COUNT(*) as count FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s AND DATE(cl.sent_at) = CURRENT_DATE AND cl.status = 'sent'
                """,
                (current_user.id,)
            )
            today_sent = cur.fetchone()['count']
            
            # Taxa de sucesso (hoje)
            cur.execute(
                """
                SELECT 
                    COUNT(CASE WHEN cl.status = 'sent' THEN 1 END) as sent,
                    COUNT(CASE WHEN cl.status IN ('failed', 'invalid') THEN 1 END) as failed
                FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s AND DATE(cl.sent_at) = CURRENT_DATE
                """,
                (current_user.id,)
            )
            success_data = cur.fetchone()
            sent_count = success_data['sent'] or 0
            failed_count = success_data['failed'] or 0
            total_attempted = sent_count + failed_count
            success_rate = round((sent_count / total_attempted * 100), 1) if total_attempted > 0 else 0
            
            # Total de negócios fechados (todas as campanhas)
            cur.execute(
                "SELECT COALESCE(SUM(closed_deals), 0) as total FROM campaigns WHERE user_id = %s",
                (current_user.id,)
            )
            total_deals = cur.fetchone()['total']
            
            # Taxa de conversão geral (todas as campanhas)
            cur.execute(
                """
                SELECT 
                    COALESCE(SUM(c.closed_deals), 0) as total_deals,
                    COUNT(CASE WHEN cl.status = 'sent' THEN 1 END) as total_sent
                FROM campaigns c
                LEFT JOIN campaign_leads cl ON cl.campaign_id = c.id
                WHERE c.user_id = %s
                """,
                (current_user.id,)
            )
            conversion_data = cur.fetchone()
            overall_conversion = round((conversion_data['total_deals'] / conversion_data['total_sent'] * 100), 1) if conversion_data['total_sent'] > 0 else 0
        
        conn.close()
        
        return {
            "today_leads_extracted": today_leads,
            "today_messages_sent": today_sent,
            "today_success_rate": success_rate,
            "total_closed_deals": total_deals,
            "overall_conversion_rate": overall_conversion,
            "active_campaigns": active_campaigns,
            "total_campaigns": total_campaigns
        }
        
    except Exception as e:
        print(f"Erro ao obter overview do dashboard: {e}")
        return {"error": str(e)}, 500


@app.route("/api/webhooks/hotmart", methods=["POST"])
def hotmart_webhook():
    """Recebe webhooks da Hotmart"""
    hottok = request.headers.get('X-Hotmart-Hottok')
    payload = request.json
    
    service = HotmartService()
    success = service.process_webhook(payload, hottok)
    
    if success:
        return {"status": "success"}, 200
    else:
        return {"status": "error"}, 400
