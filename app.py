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
import random
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
from services.uazapi import UazapiService
import re
import pandas as pd
import io
import csv
from openai import OpenAI
from functools import wraps


load_dotenv()

# Super Admin email (multi-instance feature)
SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'

# Throttling para warning de stats Uazapi (evitar spam a cada polling)
_stats_uazapi_warning_last = {}  # campaign_id -> timestamp
STATS_UAZAPI_WARNING_COOLDOWN = 300  # 5 min

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
            lead_count INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Adicionar coluna lead_count se ainda não existir (compatibilidade com DBs existentes)
    cur.execute(
        """
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='scraping_jobs' AND column_name='lead_count'
            ) THEN
                ALTER TABLE scraping_jobs ADD COLUMN lead_count INTEGER DEFAULT 0;
            END IF;
        END $$;
        """
    )
    
    # Criar índice para queries de agregação mensal
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scraping_jobs_user_date 
        ON scraping_jobs(user_id, created_at);
        """
    )
    
    # Tabela de histórico imutável de uso mensal (anti-bypass de limite)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_usage_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            cycle_start DATE NOT NULL,
            cycle_end DATE NOT NULL,
            leads_extracted INTEGER NOT NULL DEFAULT 0,
            job_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Índice para queries de limite mensal
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_monthly_usage_user_cycle 
        ON monthly_usage_history(user_id, cycle_start);
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

    # Adicionar coluna api_provider (migração: MegaAPI vs Uazapi)
    cur.execute(
        """
        ALTER TABLE instances ADD COLUMN IF NOT EXISTS api_provider TEXT DEFAULT 'megaapi';
        """
    )

    # Migração: remover instâncias MegaAPI do superadmin (manter apenas Uazapi)
    print("➡️ Removendo instâncias MegaAPI do superadmin...")
    cur.execute(
        """
        DELETE FROM instances
        WHERE user_id = (SELECT id FROM users WHERE email = 'augustogumi@gmail.com')
        AND (api_provider IS NULL OR api_provider != 'uazapi');
        """
    )

    # Tabela de modelos de mensagem
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_templates (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    # Adicionar coluna scheduled_start se não existir (migração)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS scheduled_start TIMESTAMP;
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

    # Adicionar coluna sent_by_instance para rastrear qual instância enviou (migração)
    cur.execute(
        """
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS sent_by_instance VARCHAR(255);
        """
    )

    # Tabela de junção: instâncias vinculadas a campanhas (multi-instance)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_instances (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
            UNIQUE(campaign_id, instance_id)
        );
        """
    )

    # Adicionar coluna rotation_mode em campaigns (migração)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS rotation_mode TEXT DEFAULT 'single';
        """
    )

    # ============================================================
    # CADENCE FEATURE MIGRATIONS
    # ============================================================

    # Cadence toggle + config on campaigns
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS enable_cadence BOOLEAN DEFAULT FALSE;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS cadence_config JSONB DEFAULT '{}';
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS terms_accepted BOOLEAN DEFAULT FALSE;
        """
    )

    # Campaign Steps table — stores message content per cadence step
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_steps (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            step_label TEXT NOT NULL DEFAULT '',
            message_template TEXT NOT NULL DEFAULT '[]',
            media_path TEXT,
            media_type TEXT,
            delay_days INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id, step_number)
        );
        """
    )

    # Cadence tracking columns on campaign_leads
    cur.execute(
        """
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS current_step INTEGER DEFAULT 1;
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS cadence_status TEXT DEFAULT 'pending';
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS snooze_until TIMESTAMP;
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS last_message_sent_at TIMESTAMP;
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS chatwoot_conversation_id INTEGER;
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS campaign_tags TEXT[] DEFAULT '{}';
        ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS notes TEXT;
        """
    )

    # ============================================================
    # END CADENCE FEATURE MIGRATIONS
    # ============================================================

    # ============================================================
    # UAZAPI CAMPAIGN API MIGRATIONS
    # ============================================================

    # Colunas para campanhas via API Uazapi (envio em massa avançado)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS uazapi_folder_id TEXT;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS use_uazapi_sender BOOLEAN DEFAULT false;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS delay_min_minutes INTEGER;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS delay_max_minutes INTEGER;
        """
    )

    # Horário comercial configurável por campanha (faixa de horários + sábado/domingo)
    cur.execute(
        """
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_hour_start INTEGER DEFAULT 8;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_hour_end INTEGER DEFAULT 20;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_saturday BOOLEAN DEFAULT false;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS send_sunday BOOLEAN DEFAULT false;
        """
    )

    # ============================================================
    # END UAZAPI CAMPAIGN API MIGRATIONS
    # ============================================================

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
        return 10  # Fallback

    @staticmethod
    def create(user_id: int, hotmart_purchase_id: str, hotmart_product_id: str, 
               license_type: str, purchase_date: str) -> "License":
        # Calcular data de expiração baseada no tipo de licença
        from datetime import datetime, timedelta
        
        # Garantir formato correto do license_type
        license_type = license_type.strip().lower()
        
        purchase_dt = datetime.fromisoformat(purchase_date.replace('Z', '+00:00'))
        
        # Validity usually 1 year for all these plans as per Screenshot in conversation history context (assuming)
        # Or if "semestral" logic was different, we assume standard 1 year for the new plans unless specified otherwise.
        # Defaulting to 1 year for standard SaaS plans.
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

def is_super_admin(user=None):
    """Verifica se o usuário é o super admin (multi-instance feature)"""
    u = user or current_user
    return u.is_authenticated and u.email == SUPER_ADMIN_EMAIL

class Campaign:
    def __init__(self, id, user_id, name, status, message_template, daily_limit, created_at, closed_deals=0, scheduled_start=None, sent_today=0, rotation_mode='single', enable_cadence=False, terms_accepted=False, cadence_config=None, use_uazapi_sender=False, uazapi_folder_id=None, **kwargs):
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
        self.rotation_mode = rotation_mode
        self.enable_cadence = enable_cadence or False
        self.terms_accepted = terms_accepted or False
        self.cadence_config = cadence_config or {}
        self.use_uazapi_sender = bool(use_uazapi_sender)
        self.uazapi_folder_id = uazapi_folder_id

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
        leads = [{'phone': '...', 'name': '...', 'whatsapp_link': '...', 'address': '...', ...}]
        """
        if not leads:
            return
            
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # Incluir status='pending' explicitamente para garantir processamento pelo worker
                # Novas colunas de enriquecimento
                args_str = ','.join(
                    cur.mogrify("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", 
                               (campaign_id, l.get('phone'), l.get('name'), l.get('whatsapp_link'), 'pending',
                                l.get('address'), l.get('website'), l.get('category'), l.get('location'),
                                l.get('reviews_count'), l.get('reviews_rating'), l.get('latitude'), l.get('longitude')
                               )).decode('utf-8') 
                    for l in leads
                )
                cur.execute("""
                    INSERT INTO campaign_leads 
                    (campaign_id, phone, name, whatsapp_link, status, address, website, category, location, reviews_count, reviews_rating, latitude, longitude) 
                    VALUES 
                """ + args_str)
            conn.commit()
        except Exception as e:
            print(f"Erro ao adicionar leads: {e}")
            conn.rollback()
        finally:
            conn.close()


class MessageTemplate:
    def __init__(self, id, user_id, name, content, created_at):
        self.id = id
        self.user_id = user_id
        self.name = name
        self.content = content
        self.created_at = created_at

    @staticmethod
    def create(user_id: int, name: str, content: str) -> "MessageTemplate":
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO message_templates (user_id, name, content)
                VALUES (%s, %s, %s) RETURNING id, created_at
                """,
                (user_id, name, content)
            )
            row = cur.fetchone()
            new_id = row[0]
            created_at = row[1]
        conn.commit()
        conn.close()
        return MessageTemplate(new_id, user_id, name, content, created_at)

    @staticmethod
    def get_by_user(user_id: int):
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM message_templates WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
            rows = cur.fetchall()
        conn.close()
        return [MessageTemplate(**row) for row in rows]


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
            
            # Lógica de preços baseada nos planos (Starter=197, Pro=297, Scale=397)
            # Usando faixas seguras considerando possíveis descontos pequenos, 
            # mas para cupons de 99% precisariamos de outra validação (TODO: Validar oferta/produto)
            # Por enquanto, assumindo faixas de preço padrão ou fallback para Starter
            
            if price_value >= 390.00:
                license_type = 'scale'
            elif price_value >= 290.00:
                license_type = 'pro'
            elif price_value > 50.00: # Se pagou mais de 50, provavelmente é Starter/Pro c/ desconto ou Starter
                 license_type = 'starter'
            else:
                 # Fallback para compras com muito desconto (ex: 99% off) ou testes
                 # O usuário mencionou ter comprado "Starter" com cupom de 99%
                 license_type = 'starter'
                
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
    
    @staticmethod
    def get_monthly_lead_count(user_id: int, subscription_date: datetime) -> dict:
        """
        Retorna leads usados no ciclo mensal atual baseado em purchase_date
        AGORA CONSULTA O HISTÓRICO IMUTÁVEL (anti-bypass por deleção de jobs)
        
        Args:
            user_id: ID do usuário
            subscription_date: Data de compra da licença (purchase_date)
        
        Returns:
            {
                'used': int,           # Leads usados no ciclo atual  
                'cycle_start': datetime,
                'cycle_end': datetime
            }
        """
        from datetime import datetime, timedelta
        
        # Calcular quantos meses (ciclos de 30 dias) passaram desde a assinatura
        today = datetime.now()
        days_since_purchase = (today - subscription_date).days
        months_elapsed = days_since_purchase // 30
        
        # Ciclo atual: purchase_date + (30 * months_elapsed) dias
        cycle_start = subscription_date + timedelta(days=30 * months_elapsed)
        cycle_end = cycle_start + timedelta(days=30)
        
        # Se hoje < cycle_start (edge case), estamos no ciclo anterior
        if today < cycle_start:
            months_elapsed -= 1
            cycle_start = subscription_date + timedelta(days=30 * months_elapsed)
            cycle_end = cycle_start + timedelta(days=30)
        
        conn = get_db_connection()
        with conn.cursor() as cur:
            # QUERY IMUTÁVEL: Consulta histórico ao invés de scraping_jobs
            # Isso impede bypass por deleção de jobs
            cur.execute("""
                SELECT COALESCE(SUM(leads_extracted), 0) as total
                FROM monthly_usage_history
                WHERE user_id = %s
                  AND cycle_start = %s
            """, (user_id, cycle_start.date()))
            used = cur.fetchone()[0]
        conn.close()
        
        return {
            'used': used,
            'cycle_start': cycle_start,
            'cycle_end': cycle_end
        }


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


@app.route("/api/login", methods=["POST"])
def api_login():
    """
    Login via JSON para automação (n8n, curl, etc).
    Body: {"email": "...", "password": "..."}
    Retorna 200 + Set-Cookie (session) em sucesso.
    """
    if request.is_json:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        user = User.get_by_email(email)
        if not user or not check_password_hash(user.password_hash, password):
            return json.dumps({"error": "Credenciais inválidas"}), 401
        login_user(user)
        return json.dumps({"ok": True, "user_id": user.id}), 200
    return json.dumps({"error": "Content-Type deve ser application/json"}), 400


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
    # APPEND ", Brasil" to force country context (User Request)
    cleaned_locs = []
    for loc in localizacoes:
        l = loc.strip()
        if not l: continue
        # Avoid double suffix if user already typed it
        if not l.lower().endswith('brasil') and not l.lower().endswith('brazil'):
             l = f"{l}, Brasil"
        cleaned_locs.append(l)
    
    localizacoes = cleaned_locs[:15]
    
    # VALIDAÇÃO: Limite mensal de 1500 leads
    # Buscar licença ativa do usuário
    licenses = License.get_by_user_id(current_user.id)
    active_license = next((l for l in licenses if l.status == 'active'), None)
    
    if not active_license:
        flash("Você não possui uma licença ativa. Por favor, adquira uma licença para continuar.", "error")
        return redirect(url_for("index"))
    
    # Converter purchase_date para datetime (pode já ser datetime do banco)
    from datetime import datetime
    subscription_date = active_license.purchase_date
    
    # Se for string, converter
    if isinstance(subscription_date, str):
        subscription_date = datetime.fromisoformat(subscription_date.replace('Z', '+00:00'))
    
    # Remover timezone info se presente
    if hasattr(subscription_date, 'tzinfo') and subscription_date.tzinfo is not None:
        subscription_date = subscription_date.replace(tzinfo=None)
    
    # Calcular uso mensal
    cycle_info = ScrapingJob.get_monthly_lead_count(current_user.id, subscription_date)
    
    # Validar limite (1500 leads por mês)
    # Validar limite (1500 leads por mês)
    MONTHLY_LIMIT = 2000
    requested_leads = total
    
    if cycle_info['used'] + requested_leads > MONTHLY_LIMIT:
        available = MONTHLY_LIMIT - cycle_info['used']
        renewal_date = cycle_info['cycle_end'].date().isoformat()
        
        flash(
            f"Limite mensal atingido! Você já usou {cycle_info['used']} de {MONTHLY_LIMIT} leads neste ciclo. "
            f"Disponível: {available} leads. Renovação em {renewal_date}.",
            "error"
        )
        return redirect(url_for("index"))
    
    # Create background job
    job_id = ScrapingJob.create(
        user_id=current_user.id,
        keyword=palavra_chave,
        locations=localizacoes,
        total_results=total
    )
    
    # Validar API Token antes de enfileirar
    if not os.environ.get('APIFY_TOKEN'):
        flash("Erro de Configuração: APIFY_TOKEN não encontrado. Contate o suporte ou configure no arquivo .env/Dokploy.", "error")
        # Marcar job como falho imediatamente para não ficar 'pending' para sempre
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE scraping_jobs SET status = 'failed', error_message = %s WHERE id = %s", 
                        ('APIFY_TOKEN não configurado', job_id))
        conn.commit()
        conn.close()
        return redirect(url_for("jobs"))

    # Start job in background (Queue)
    try:
        from worker_scraper import run_scraper_task
        q.enqueue(run_scraper_task, job_id, job_timeout=3600)
        flash(f"Scraping enfileirado! Job ID: {job_id}. Você pode acompanhar o progresso na página de jobs.")
    except Exception as e:
        print(f"❌ Erro ao enfileirar job no Redis: {e}")
        flash(f"Erro ao iniciar o job: {str(e)}", "error")
        # Tentar marcar como falho no banco
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("UPDATE scraping_jobs SET status = 'failed', error_message = %s WHERE id = %s", 
                            (f'Erro de Fila: {str(e)}', job_id))
            conn.commit()
            conn.close()
        except:
            pass

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
    threading.Thread(target=send_async_email, args=(app, msg)).start()

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
    threading.Thread(target=send_async_email, args=(app, msg)).start()



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

    # 2. Obter Instância(s) WhatsApp
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if is_super_admin():
            # Superadmin: todas as instâncias Uazapi com status real
            cur.execute(
                "SELECT * FROM instances WHERE user_id = %s AND api_provider = 'uazapi'",
                (current_user.id,),
            )
            instances = cur.fetchall()
        else:
            cur.execute("SELECT * FROM instances WHERE user_id = %s", (current_user.id,))
            instances = cur.fetchall()
    conn.close()

    instance = None if is_super_admin() else (instances[0] if instances else None)
    instances_with_status = None

    if is_super_admin() and instances:
        uazapi = UazapiService()
        instances_with_status = []
        for inst in instances:
            apikey = inst.get("apikey") or ""
            if not apikey:
                instances_with_status.append(
                    {"id": inst["id"], "name": inst.get("name", "?"), "status": "Desconectado"}
                )
                continue
            result = uazapi.get_status(apikey)
            raw_status = "disconnected"
            if result:
                raw_status = (
                    result.get("instance", {}).get("status")
                    or result.get("status")
                    or "disconnected"
                )
            status_label = {
                "connected": "Conectado",
                "connecting": "Conectando",
                "disconnected": "Desconectado",
            }.get(raw_status, "Desconectado")
            instances_with_status.append(
                {"id": inst["id"], "name": inst.get("name", "?"), "status": status_label}
            )

    return render_template(
        "account.html",
        user=current_user,
        license=active_license,
        instance=instance,
        instances_with_status=instances_with_status,
        is_super_admin=is_super_admin(),
    )

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

    if campaign.use_uazapi_sender and campaign.uazapi_folder_id:
        success, err = _uazapi_control_campaign(campaign_id, current_user.id, 'delete')
        if success:
            flash("Campanha excluída com sucesso!", "success")
        else:
            flash(f"Erro ao excluir campanha: {err}", "error")
    elif campaign.delete():
        flash("Campanha excluída com sucesso!", "success")
    else:
        flash("Erro ao excluir campanha.", "error")

    return redirect(url_for('campaigns'))

# --- Kanban Board Routes ---

@app.route('/campaigns/<int:campaign_id>/kanban')
@login_required
def campaign_kanban(campaign_id):
    """Render the Kanban board for a campaign"""
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        flash("Campanha não encontrada.", "error")
        return redirect(url_for('campaigns'))
    
    # Get total lead count
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = %s", (campaign_id,))
        total_leads = cur.fetchone()[0]
    conn.close()
    
    return render_template('campaigns_kanban.html', campaign=campaign, total_leads=total_leads)


@app.route('/api/campaigns/<int:campaign_id>/kanban-data')
@login_required
def campaign_kanban_data(campaign_id):
    """API: Get all leads for the kanban board"""
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, phone, name, status, current_step, cadence_status, 
                   snooze_until, last_message_sent_at, chatwoot_conversation_id,
                   sent_at, whatsapp_link, notes, log,
                   address, website, category, location, reviews_count, reviews_rating, latitude, longitude,
                   CASE 
                       WHEN cadence_status IN ('snoozed', 'active') THEN 1
                       WHEN status IN ('sent', 'pending') THEN 2
                       ELSE 3
                       END as status_priority
            FROM campaign_leads 
            WHERE campaign_id = %s 
            ORDER BY current_step ASC, status_priority ASC, last_message_sent_at DESC NULLS LAST, name ASC
        """, (campaign_id,))
        leads = cur.fetchall()
    conn.close()
    
    # Serialize datetime objects
    serialized = []
    for lead in leads:
        row = dict(lead)
        for key in ['snooze_until', 'last_message_sent_at', 'sent_at']:
            if row.get(key):
                row[key] = row[key].isoformat()
        serialized.append(row)
    
    return json.dumps({'leads': serialized, 'campaign_id': campaign_id})


@app.route('/api/campaigns/<int:campaign_id>/leads/<int:lead_id>/move', methods=['POST'])
@login_required
def move_campaign_lead(campaign_id, lead_id):
    """API: Move a lead to a different step or status on the kanban board"""
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
    
    data = request.json
    target_step = data.get('target_step', 1)
    target_status = data.get('target_status', 'active')
    
    # Validate target_status
    valid_statuses = ['pending', 'active', 'snoozed', 'converted', 'lost', 'stopped', 'replied']
    if target_status not in valid_statuses:
        return json.dumps({'error': f'Status inválido: {target_status}'}), 400
    
    conn = get_db_connection()
    with conn.cursor() as cur:
        # Verify lead belongs to this campaign
        cur.execute("SELECT id FROM campaign_leads WHERE id = %s AND campaign_id = %s", (lead_id, campaign_id))
        if not cur.fetchone():
            conn.close()
            return json.dumps({'error': 'Lead não encontrado'}), 404
        
        cur.execute("""
            UPDATE campaign_leads 
            SET current_step = %s, cadence_status = %s
            WHERE id = %s AND campaign_id = %s
        """, (target_step, target_status, lead_id, campaign_id))
    conn.commit()
    conn.close()
    
    return json.dumps({'success': True, 'lead_id': lead_id, 'new_step': target_step, 'new_status': target_status})


@app.route('/api/leads/<int:lead_id>/note', methods=['POST'])
@login_required
def update_lead_note(lead_id):
    """API: Update note for a specific lead"""
    data = request.json
    note = data.get('note', '')
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Security check: ensure lead belongs to a campaign owned by user
            cur.execute("""
                SELECT cl.id FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE cl.id = %s AND c.user_id = %s
            """, (lead_id, current_user.id))
            
            if not cur.fetchone():
                return json.dumps({'error': 'Lead não encontrado ou acesso negado'}), 404
            
            cur.execute("UPDATE campaign_leads SET notes = %s WHERE id = %s", (note, lead_id))
        conn.commit()
        return json.dumps({'success': True})
    except Exception as e:
        conn.rollback()
        return json.dumps({'error': str(e)}), 500
    finally:
        conn.close()


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

@app.route('/admin/campaigns')
@login_required
@admin_required
def admin_campaigns():
    status_filter = request.args.get('status')
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Counts for filters
        cur.execute("SELECT COUNT(*) as count FROM campaigns")
        count_all = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM campaigns WHERE status = 'running'")
        count_running = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM campaigns WHERE status = 'pending'")
        count_pending = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM campaigns WHERE status = 'paused'")
        count_paused = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM campaigns WHERE status = 'completed'")
        count_completed = cur.fetchone()['count']
        
        # Build query based on filter
        if status_filter:
            cur.execute("""
                SELECT c.*, u.email as user_email,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id) as total_leads,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id AND status = 'sent') as sent_count,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id AND status = 'pending') as pending_count
                FROM campaigns c
                JOIN users u ON c.user_id = u.id
                WHERE c.status = %s
                ORDER BY c.created_at DESC
            """, (status_filter,))
        else:
            cur.execute("""
                SELECT c.*, u.email as user_email,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id) as total_leads,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id AND status = 'sent') as sent_count,
                       (SELECT COUNT(*) FROM campaign_leads WHERE campaign_id = c.id AND status = 'pending') as pending_count
                FROM campaigns c
                JOIN users u ON c.user_id = u.id
                ORDER BY c.created_at DESC
            """)
        
        campaigns = cur.fetchall()
    conn.close()
    
    counts = {
        'all': count_all,
        'running': count_running,
        'pending': count_pending,
        'paused': count_paused,
        'completed': count_completed
    }
    
    return render_template('admin/campaigns.html', 
                         campaigns=campaigns,
                         status_filter=status_filter,
                         counts=counts)

@app.route('/api/admin/campaigns/<int:campaign_id>', methods=['DELETE'])
@login_required
@admin_required
def admin_delete_campaign(campaign_id):
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT use_uazapi_sender, uazapi_folder_id FROM campaigns WHERE id = %s", (campaign_id,))
            row = cur.fetchone()
        conn.close()

        if row and row.get('use_uazapi_sender') and row.get('uazapi_folder_id'):
            success, err = _uazapi_control_campaign(campaign_id, current_user.id, 'delete', admin_mode=True)
            if success:
                return {"success": True}
            return {"error": err or "Falha ao controlar campanha Uazapi"}, 500

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM campaign_leads WHERE campaign_id = %s", (campaign_id,))
            cur.execute("DELETE FROM campaign_instances WHERE campaign_id = %s", (campaign_id,))
            cur.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        print(f"Erro ao excluir campanha: {e}")
        return {"error": str(e)}, 500

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
            cur.execute("DELETE FROM password_resets WHERE user_id = %s", (user_id,))
            # Fix: monthly_usage_history and message_templates constraints
            cur.execute("DELETE FROM monthly_usage_history WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM message_templates WHERE user_id = %s", (user_id,))
            
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
            "instance_apikey": user['instance_apikey'],
            "remote_jid": None  # Will be populated via JS check or separate call, but let's try to fetch if status is connected? 
                                # Actually, better to fetch it in the check_status endpoint called by frontend.
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
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE apikey = %s", (instance_apikey,))
        prov_row = cur.fetchone()
    conn.close()
    
    if not prov_row:
        return {"error": "Instance not found"}, 404
    api_provider = prov_row[0]
    
    if api_provider == 'uazapi':
        uazapi = UazapiService()
        result = uazapi.get_status(instance_apikey)
        if not result:
            return {"error": "Failed to verify status"}, 400
        instance_data = result.get('instance') or result
        status_val = instance_data.get('status', 'disconnected') if isinstance(instance_data, dict) else 'disconnected'
        new_status = status_val if status_val in ('connected', 'connecting', 'disconnected') else 'disconnected'
        remote_jid = None
        if isinstance(result, dict):
            remote_jid = result.get('id') or result.get('me')
            if not remote_jid and result.get('instance_data'):
                remote_jid = result['instance_data'].get('phone') or result['instance_data'].get('user') or result['instance_data'].get('jid')
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE instances SET status = %s WHERE apikey = %s", (new_status, instance_apikey))
        conn.commit()
        conn.close()
        print(f"Admin Checked Status for {instance_apikey}: {new_status} (Uazapi)")
        return {"status": new_status, "result": result, "remote_jid": remote_jid}
    
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
    
    # Extract Remote JID / Phone
    remote_jid = None
    if isinstance(result, dict):
        # Variant 1: top level 'id' or 'jid'
        remote_jid = result.get('id') or result.get('me')
        
        # Variant 2: instance_data
        if not remote_jid and result.get('instance_data'):
             remote_jid = result['instance_data'].get('phone') or result['instance_data'].get('user') or result['instance_data'].get('jid')

    # Update DB
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE instances SET status = %s WHERE apikey = %s", (new_status, instance_apikey))
    conn.commit()
    conn.close()
    
    # Debug print
    print(f"Admin Checked Status for {instance_apikey}: {new_status} (JID: {remote_jid})")
    
    return {"status": new_status, "result": result, "remote_jid": remote_jid}


@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    instance_name = data.get('instance_name')
    
    if not email or not password:
        return {"error": "Email e senha são obrigatórios"}, 400
        
    # Check if user exists
    user = User.get_by_email(email)
    if user:
        return {"error": "Email já cadastrado"}, 400
        
    try:
        # 1. Create User
        user = User.create(email, password)
        
        # 2. Create Instance (Optional)
        if instance_name:
            service = WhatsappService()
            # Precisamos do contexto do usuário recém criado, mas o WhatsappService usa current_user.
            # WORKAROUND: Inserir manualmente no DB ou impersonate.
            # Como WhatsappService.create_instance usa create_instance -> usa get_db_connection
            # E usa current_user.id para salvar no banco.
            # AQUI TEMOS UM PROBLEMA: WhatsappService assume current_user.
            
            # Vamos inserir direto no banco para ser mais seguro e não depender do current_user ser o admin
            
            # Sanitize
            safe_name = "".join(c for c in instance_name if c.isalnum() or c in ('-', '_'))
            if safe_name:
                # Call Mega API directly or via Service but strictly for the API part?
                # Service.create_instance calls API and then saves DB using current_user.
                # Let's call API manually to get key, then save to DB for the NEW USER.
                
                # Using service just for the API call part would be nice if decoupled.
                # create_instance method mixes both.
                # Let's split or just copy logic here for Admin context.
                
                # API Call
                url = f"{service.base_url}/rest/instance/init"
                params = {'instance_key': safe_name}
                payload = {"messageData": {"webhookUrl": "", "webhookEnabled": True}}
                
                try:
                    resp = requests.post(url, params=params, json=payload, headers=service.headers, timeout=15)
                    if resp.status_code == 200:
                        # Success
                        instance_key = safe_name # Usually matches
                        # Check response
                         # ... (skipped detailed json check for brevity, assuming success if 200)
                        
                        # Save to DB for the NEW USER
                        conn = get_db_connection()
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO instances (user_id, name, apikey, status) VALUES (%s, %s, %s, 'disconnected')",
                                (user.id, safe_name, instance_key)
                            )
                        conn.commit()
                        conn.close()
                        print(f"✅ Instância {safe_name} criada para usuário {user.id}")
                        
                    else:
                        print(f"⚠️ Erro ao criar instância na MegaAPI: {resp.text}")
                        # Don't fail the user creation, just warn
                except Exception as e:
                    print(f"⚠️ Erro ao conectar MegaAPI: {e}")

        return {"success": True}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@login_required
@admin_required
def admin_update_user(user_id):
    data = request.json
    email = data.get('email')
    password = data.get('password') # Optional
    
    if not email:
        return {"error": "Email é obrigatório"}, 400
        
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Check for email conflict
            cur.execute("SELECT id FROM users WHERE email = %s AND id != %s", (email, user_id))
            if cur.fetchone():
                return {"error": "Email já está em uso por outro usuário"}, 400
            
            # Update Email
            cur.execute("UPDATE users SET email = %s WHERE id = %s", (email, user_id))
            
            # Update Password if provided
            if password:
                password_hash = generate_password_hash(password)
                cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
                
        conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}, 500
    finally:
        conn.close()
@app.route('/campaigns/new')
@login_required
def new_campaign():
    # Fetch user's instances for multi-instance selection
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, name, status, COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE user_id = %s ORDER BY id ASC", (current_user.id,))
        user_instances = cur.fetchall()
    conn.close()
    
    return render_template('campaigns_new.html', 
                           instances=user_instances,
                           is_super_admin=is_super_admin())

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

def _uazapi_control_campaign(campaign_id: int, user_id: int, action: str, admin_mode: bool = False):
    """
    Helper: executa action (stop|continue|delete) na Uazapi para campanha com use_uazapi_sender.
    Retorna (success, error_msg). Em caso de delete, já remove do DB.
    admin_mode: se True, não valida user_id (para admin deletar qualquer campanha).
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if admin_mode:
                cur.execute(
                    "SELECT c.id, c.use_uazapi_sender, c.uazapi_folder_id, c.user_id FROM campaigns c WHERE c.id = %s",
                    (campaign_id,)
                )
            else:
                cur.execute(
                    "SELECT c.id, c.use_uazapi_sender, c.uazapi_folder_id, c.user_id FROM campaigns c WHERE c.id = %s AND c.user_id = %s",
                    (campaign_id, user_id)
                )
            campaign = cur.fetchone()
        if not campaign or not campaign.get('use_uazapi_sender') or not campaign.get('uazapi_folder_id'):
            return False, "Campanha não usa envio Uazapi ou folder_id ausente"
        folder_id = campaign['uazapi_folder_id']
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.apikey FROM campaign_instances ci
                JOIN instances i ON i.id = ci.instance_id
                WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
                LIMIT 1
            """, (campaign_id,))
            inst = cur.fetchone()
        conn.close()
        if not inst or not inst.get('apikey'):
            return False, "Instância Uazapi não encontrada para esta campanha"
        token = inst['apikey']
        uazapi = UazapiService()
        result = uazapi.edit_campaign(token, folder_id, action)
        if not result:
            return False, "Falha ao comunicar com API Uazapi"
        conn = get_db_connection()
        with conn.cursor() as cur:
            if action == 'stop':
                cur.execute("UPDATE campaigns SET status = 'paused' WHERE id = %s", (campaign_id,))
            elif action == 'continue':
                cur.execute("UPDATE campaigns SET status = 'running' WHERE id = %s", (campaign_id,))
            elif action == 'delete':
                cur.execute("DELETE FROM campaign_leads WHERE campaign_id = %s", (campaign_id,))
                cur.execute("DELETE FROM campaign_instances WHERE campaign_id = %s", (campaign_id,))
                cur.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
        conn.commit()
        conn.close()
        return True, None
    except Exception as e:
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return False, str(e)


@app.route('/api/campaigns/<int:campaign_id>/uazapi-control', methods=['POST'])
@login_required
def uazapi_control(campaign_id):
    """
    Controla campanha Uazapi: stop, continue ou delete.
    Body: { "action": "stop" | "continue" | "delete" }
    """
    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    if action not in ('stop', 'continue', 'delete'):
        return json.dumps({'error': 'action deve ser stop, continue ou delete'}), 400
    success, err = _uazapi_control_campaign(campaign_id, current_user.id, action)
    if success:
        return json.dumps({'success': True, 'action': action})
    return json.dumps({'error': err or 'Erro ao controlar campanha'}), 500


@app.route('/api/campaigns/<int:campaign_id>/uazapi-messages', methods=['GET'])
@login_required
def uazapi_messages(campaign_id):
    """
    Lista mensagens da campanha na Uazapi.
    Query params: messageStatus (Scheduled|Sent|Failed), page, pageSize.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT use_uazapi_sender, uazapi_folder_id FROM campaigns WHERE id = %s AND user_id = %s",
                (campaign_id, current_user.id)
            )
            campaign = cur.fetchone()
        if not campaign or not campaign.get('use_uazapi_sender') or not campaign.get('uazapi_folder_id'):
            return json.dumps({'error': 'Campanha não usa envio Uazapi ou folder_id ausente'}), 404
        folder_id = campaign['uazapi_folder_id']
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.apikey FROM campaign_instances ci
                JOIN instances i ON i.id = ci.instance_id
                WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
                LIMIT 1
            """, (campaign_id,))
            inst = cur.fetchone()
        conn.close()
        if not inst or not inst.get('apikey'):
            return json.dumps({'error': 'Instância Uazapi não encontrada para esta campanha'}), 404
        token = inst['apikey']
        message_status = request.args.get('messageStatus')
        page = request.args.get('page', type=int)
        page_size = request.args.get('pageSize', type=int)
        uazapi = UazapiService()
        result = uazapi.list_messages(
            token, folder_id,
            message_status=message_status if message_status else None,
            page=page if page is not None else None,
            page_size=page_size if page_size is not None else None
        )
        if result is None:
            return json.dumps({'error': 'Falha ao obter mensagens da Uazapi'}), 500
        return json.dumps(result, default=str)
    except Exception as e:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        print(f"Erro ao listar mensagens Uazapi: {e}")
        return json.dumps({'error': str(e)}), 500


@app.route('/api/campaigns/<int:campaign_id>/toggle_pause', methods=['POST'])
@login_required
def toggle_campaign_pause(campaign_id):
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, use_uazapi_sender, uazapi_folder_id FROM campaigns WHERE id = %s AND user_id = %s", (campaign_id, current_user.id))
            campaign = cur.fetchone()
        conn.close()

        if not campaign:
            return json.dumps({"error": "Campanha não encontrada"}), 404

        current_status = campaign['status']
        new_status = None

        if current_status == 'running':
            new_status = 'paused'
        elif current_status == 'paused':
            new_status = 'running'
        elif current_status == 'pending':
            new_status = 'paused'  # Allow pausing pending campaigns too
        else:
            return json.dumps({"error": f"Não é possível pausar/continuar campanha com status '{current_status}'"}), 400

        # Se use_uazapi_sender, delegar para Uazapi API
        if campaign.get('use_uazapi_sender') and campaign.get('uazapi_folder_id'):
            action = 'stop' if new_status == 'paused' else 'continue'
            success, err = _uazapi_control_campaign(campaign_id, current_user.id, action)
            if success:
                return json.dumps({"success": True, "new_status": new_status})
            return json.dumps({"error": err or "Erro ao controlar campanha Uazapi"}), 500

        # Comportamento atual para campanhas sem Uazapi
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE campaigns SET status = %s WHERE id = %s", (new_status, campaign_id))
        conn.commit()
        conn.close()

        return json.dumps({"success": True, "new_status": new_status})

    except Exception as e:
        print(f"Erro ao alternar pausa da campanha: {e}")
        return json.dumps({"error": str(e)}), 500

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
        df = pd.read_csv(filepath, dtype=str)
        
        # Adicionar coluna 'status' se não existir (valor 1 = pronto para envio)
        if 'status' not in [c.lower() for c in df.columns]:
            df['status'] = 1
            # Salvar novamente com a coluna status
            df.to_csv(filepath, index=False)
        
        # Tentar identificar colunas
        cols = [c.lower() for c in df.columns]
        name_col = next((c for c in cols if 'name' in c or 'nome' in c), None)
        
        count = 0
        if name_col:
             # Contar válidos usando a coluna 'name' como referência
             count = int(df[df.columns[cols.index(name_col)]].notna().sum())
        else:
             # Se não tiver coluna name, usar o total de linhas
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
    def extract_phone_from_whatsapp_link(link):
        """Helper to extract phone from whatsapp link"""
        if not link: return None
        import re
        # Patterns
        patterns = [r'wa\.me/([0-9]+)', r'phone=([0-9]+)', r'whatsapp\.com/send\?phone=([0-9]+)']
        for pattern in patterns:
            match = re.search(pattern, str(link))
            if match: return match.group(1)
        # Fallback: just digits if long enough
        digits = re.sub(r'\D', '', str(link))
        if len(digits) >= 10: return digits
        return None

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
    
    # NEW: Get instance_ids and rotation_mode (multi-instance)
    instance_ids = data.get('instance_ids', [])  # list of instance IDs
    rotation_mode = data.get('rotation_mode', 'single')  # 'single' or 'round_robin'

    # Uazapi campaign API: use_uazapi_sender, delays (minutos)
    use_uazapi_sender = bool(data.get('use_uazapi_sender', False))
    delay_min_minutes = data.get('delay_min_minutes')  # None ou int
    delay_max_minutes = data.get('delay_max_minutes')  # None ou int

    # Horário comercial configurável (faixa de horários + sábado/domingo)
    send_hour_start = data.get('send_hour_start', 8)
    send_hour_end = data.get('send_hour_end', 20)
    send_saturday = bool(data.get('send_saturday', False))
    send_sunday = bool(data.get('send_sunday', False))
    # Validar faixa de horário (0-23)
    if send_hour_start is not None:
        send_hour_start = max(0, min(23, int(send_hour_start)))
    if send_hour_end is not None:
        send_hour_end = max(0, min(23, int(send_hour_end)))
    
    # Validate rotation_mode
    if rotation_mode not in ('single', 'round_robin'):
        rotation_mode = 'single'
    
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

    # Uazapi: quando use_uazapi_sender=true, instance_ids obrigatório e apenas instâncias Uazapi
    if use_uazapi_sender:
        if not instance_ids:
            return json.dumps({'error': 'Selecione uma instância Uazapi para campanhas com envio em massa.'}), 400
        conn_check = get_db_connection()
        with conn_check.cursor() as cur:
            cur.execute(
                "SELECT id FROM instances WHERE user_id = %s AND id = ANY(%s) AND COALESCE(api_provider, 'megaapi') = 'uazapi'",
                (current_user.id, instance_ids)
            )
            uazapi_ids = [r[0] for r in cur.fetchall()]
        conn_check.close()
        if len(uazapi_ids) != len(instance_ids):
            return json.dumps({'error': 'Apenas instâncias Uazapi podem ser usadas com envio em massa. Verifique suas instâncias.'}), 400
        # Para Uazapi API: usar primeira instância (single folder por campanha)
        instance_ids = uazapi_ids[:1] if uazapi_ids else []
        
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
                df = pd.read_csv(file_path, dtype=str)
            elif file_path.endswith('.xlsx'):
                df = pd.read_excel(file_path, dtype=str)
            else:
                 # Fallback: tentar ler como CSV
                df = pd.read_csv(file_path, dtype=str)
            
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
            
            # Enrichment Columns
            address_col = next((c for c in cols if 'address' in c or 'endereço' in c), None)
            website_col = next((c for c in cols if 'website' in c or 'site' in c), None)
            category_col = next((c for c in cols if 'category' in c or 'categoria' in c), None)
            location_col = next((c for c in cols if 'location' in c or 'localização' in c), None)
            reviews_count_col = next((c for c in cols if 'reviews_count' in c or 'avaliações' in c), None)
            reviews_rating_col = next((c for c in cols if 'reviews_average' in c or 'rating' in c or 'nota' in c), None)
            latitude_col = next((c for c in cols if 'latitude' in c or 'lat' == c), None)
            longitude_col = next((c for c in cols if 'longitude' in c or 'lon' == c or 'lng' == c), None)
            
            # Check availability: Need either phone_col OR whatsapp_link_col
            if not phone_col and not whatsapp_link_col:
                 return json.dumps({'error': 'Nenhuma coluna de telefone ou link de WhatsApp encontrada no arquivo'}), 400
            
            # Filtrar apenas leads com status = 1 (ou sem coluna status)
            # Filtrar apenas leads com status = 1 (ou sem coluna status)
            if status_col:
                # Convert to numeric to be safe or compare with string '1'
                # Since we used dtype=str, it should be '1'. 
                # Handling both cases safely:
                df_filtered = df[df[status_col].astype(str).str.strip() == '1']
            else:
                df_filtered = df
                 
            for _, row in df_filtered.iterrows():
                raw_phone = str(row[phone_col]) if phone_col and pd.notna(row[phone_col]) else ""
                raw_name = str(row[name_col]) if name_col and pd.notna(row[name_col]) else "Visitante"
                raw_whatsapp_link = str(row[whatsapp_link_col]) if whatsapp_link_col and pd.notna(row[whatsapp_link_col]) else None
                
                final_phone = None
                
                # 1. Try to extract from WhatsApp Link FIRST (Priority)
                if raw_whatsapp_link:
                    extracted = extract_phone_from_whatsapp_link(raw_whatsapp_link)
                    if extracted:
                        final_phone = extracted
                
                # 2. If not found, try Phone column
                if not final_phone and raw_phone:
                     clean_p = re.sub(r'\D', '', raw_phone)
                     if len(clean_p) >= 10:
                        final_phone = clean_p
                
                # 3. Add if valid
                if final_phone:
                    valid_leads.append({
                        'phone': final_phone,
                        'name': raw_name,
                        'whatsapp_link': raw_whatsapp_link,
                        'address': str(row[address_col]) if address_col and pd.notna(row[address_col]) else None,
                        'website': str(row[website_col]) if website_col and pd.notna(row[website_col]) else None,
                        'category': str(row[category_col]) if category_col and pd.notna(row[category_col]) else None,
                        'location': str(row[location_col]) if location_col and pd.notna(row[location_col]) else None,
                        'reviews_count': str(row[reviews_count_col]) if reviews_count_col and pd.notna(row[reviews_count_col]) else None,
                        'reviews_rating': str(row[reviews_rating_col]) if reviews_rating_col and pd.notna(row[reviews_rating_col]) else None,
                        'latitude': str(row[latitude_col]) if latitude_col and pd.notna(row[latitude_col]) else None,
                        'longitude': str(row[longitude_col]) if longitude_col and pd.notna(row[longitude_col]) else None
                    })

        except Exception as e:
            print(f"Erro ao ler arquivo: {e}")
            return json.dumps({'error': f'Erro ao ler arquivo: {str(e)}'}), 500
        
        if not valid_leads:
            return json.dumps({'error': 'Nenhum lead válido encontrado na lista'}), 400

        # 4. Criar Campanha
        # NEW: Create campaign with scheduled_start, dynamic status, rotation_mode, use_uazapi_sender
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Determine initial status based on scheduled_start
            # use_uazapi_sender: Uazapi gerencia envio; status 'running' após API call
            initial_status = 'pending' if scheduled_start else 'running'
            
            cur.execute(
                """
                INSERT INTO campaigns (user_id, name, message_template, daily_limit, scheduled_start, status, rotation_mode, use_uazapi_sender, delay_min_minutes, delay_max_minutes, send_hour_start, send_hour_end, send_saturday, send_sunday)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at
                """,
                (current_user.id, name, message_template_json, 100, scheduled_start, initial_status, rotation_mode, use_uazapi_sender, delay_min_minutes, delay_max_minutes, send_hour_start, send_hour_end, send_saturday, send_sunday)
            )
            row = cur.fetchone()
            campaign_id = row[0]
            created_at = row[1]
            
            # NEW: Insert campaign_instances associations
            if instance_ids:
                # Validate that all instance_ids belong to this user
                cur.execute("SELECT id FROM instances WHERE user_id = %s AND id = ANY(%s)", 
                           (current_user.id, instance_ids))
                valid_ids = [r[0] for r in cur.fetchall()]
                for inst_id in valid_ids:
                    cur.execute(
                        "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (campaign_id, inst_id)
                    )
            else:
                # Backward compatible: auto-associate user's default instance
                cur.execute("SELECT id FROM instances WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1", 
                           (current_user.id,))
                default_inst = cur.fetchone()
                if default_inst:
                    cur.execute(
                        "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (campaign_id, default_inst[0])
                    )
            
            # ============================================================
            # CADENCE: Save steps, media, and flags
            # ============================================================
            enable_cadence = data.get('enable_cadence', False)
            terms_accepted = data.get('terms_accepted', False)
            steps = data.get('steps', [])
            
            if enable_cadence and steps:
                # cadence_config: rollover_time (HH:MM) para rollover diário
                rollover_time = data.get('rollover_time', '23:00')
                if rollover_time and not re.match(r'^\d{1,2}:\d{2}$', str(rollover_time)):
                    rollover_time = '23:00'
                cadence_config_json = json.dumps({'rollover_time': str(rollover_time)})
                cur.execute(
                    """UPDATE campaigns SET enable_cadence = TRUE, terms_accepted = %s,
                       cadence_config = COALESCE(cadence_config, '{}')::jsonb || %s::jsonb
                       WHERE id = %s""",
                    (terms_accepted, cadence_config_json, campaign_id)
                )
                
                # Media storage directory
                media_dir = os.path.join('storage', str(current_user.id), 'campaign_media')
                os.makedirs(media_dir, exist_ok=True)
                
                for step in steps:
                    step_number = step.get('step_number', 1)
                    step_label = step.get('step_label', '')
                    step_messages = step.get('message_templates', [])
                    delay_days = step.get('delay_days', 0)
                    media_base64 = step.get('media_base64')
                    media_name = step.get('media_name')
                    media_type = step.get('media_type')  # 'image' or 'video'
                    
                    media_path = None
                    if media_base64 and media_name:
                        import base64, uuid
                        # Generate unique filename
                        ext = os.path.splitext(media_name)[1] or '.bin'
                        unique_name = f"camp_{campaign_id}_step_{step_number}_{uuid.uuid4().hex[:8]}{ext}"
                        media_path = os.path.join(media_dir, unique_name)
                        
                        # Decode and save
                        try:
                            with open(media_path, 'wb') as f:
                                f.write(base64.b64decode(media_base64))
                        except Exception as e:
                            print(f"⚠️ Failed to save media for step {step_number}: {e}")
                            media_path = None
                    
                    # Serialize step messages as JSON
                    step_template_json = json.dumps(step_messages)
                    
                    cur.execute(
                        """
                        INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template, media_path, media_type, delay_days)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (campaign_id, step_number) DO UPDATE SET
                            step_label = EXCLUDED.step_label,
                            message_template = EXCLUDED.message_template,
                            media_path = EXCLUDED.media_path,
                            media_type = EXCLUDED.media_type,
                            delay_days = EXCLUDED.delay_days
                        """,
                        (campaign_id, step_number, step_label, step_template_json, media_path, media_type, delay_days)
                    )
            # ============================================================
            # END CADENCE
            # ============================================================
            
        conn.commit()
        conn.close()
        
        # 5. Adicionar Leads
        CampaignLead.add_leads(campaign_id, valid_leads)
        
        # 6. Uazapi: se use_uazapi_sender, montar messages, chamar API, salvar folder_id
        if use_uazapi_sender:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Obter primeira instância Uazapi vinculada
                cur.execute("""
                    SELECT i.id, i.apikey, i.api_provider
                    FROM campaign_instances ci
                    JOIN instances i ON i.id = ci.instance_id
                    WHERE ci.campaign_id = %s AND i.api_provider = 'uazapi'
                    LIMIT 1
                """, (campaign_id,))
                inst = cur.fetchone()
            conn.close()
            
            if inst and inst.get('apikey'):
                token = inst['apikey']
                # Parse message_templates (lista de variações)
                try:
                    variations = json.loads(message_template_json)
                    if isinstance(variations, str):
                        variations = [variations]
                    if not variations:
                        variations = ["Olá!"]
                except Exception:
                    variations = [message_template_json or "Olá!"]
                
                # Montar messages array: 1 msg por lead com random.choice(variations)
                messages = []
                for lead in valid_leads:
                    msg_text = random.choice(variations)
                    if lead.get('name'):
                        msg_text = msg_text.replace("{nome}", lead['name']).replace("{name}", lead['name'])
                    # number sem @s.whatsapp.net; garantir formato 55...
                    phone = str(lead.get('phone', '')).strip()
                    if not phone.startswith('55') and len(phone) >= 10:
                        phone = '55' + phone
                    messages.append({"number": phone, "type": "text", "text": msg_text})
                
                if messages:
                    delay_min_sec = (delay_min_minutes or 5) * 60
                    delay_max_sec = (delay_max_minutes or 15) * 60
                    scheduled_for_param = None
                    if scheduled_start:
                        try:
                            dt = datetime.fromisoformat(scheduled_start.replace('Z', '+00:00'))
                            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
                            delta_min = (dt - now).total_seconds() / 60
                            if delta_min > 0:
                                scheduled_for_param = max(1, int(delta_min))  # Uazapi: minutos a partir de agora
                        except Exception:
                            pass
                    
                    uazapi = UazapiService()
                    # Log payload de envio em massa (visibilidade como MegaAPI)
                    payload_summary = {"campaign_id": campaign_id, "leads": len(messages), "delay_min": delay_min_sec, "delay_max": delay_max_sec}
                    print(f"[UAZAPI] create_advanced_campaign payload: {json.dumps(payload_summary)}")
                    if os.environ.get('DEBUG_SENDER'):
                        for i, m in enumerate(messages[:3]):  # primeiras 3 como amostra
                            print(f"[UAZAPI]   msg[{i}]: number={m.get('number')} text={m.get('text', '')[:50]}...")
                        if len(messages) > 3:
                            print(f"[UAZAPI]   ... +{len(messages)-3} mais")
                    result = uazapi.create_advanced_campaign(
                        token, delay_min_sec, delay_max_sec, messages,
                        info=name, scheduled_for=scheduled_for_param
                    )
                    if result and result.get('folder_id'):
                        print(f"[UAZAPI] create_advanced_campaign OK campaign_id={campaign_id} folder_id={result['folder_id']}")
                        conn = get_db_connection()
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE campaigns SET uazapi_folder_id = %s, status = 'running' WHERE id = %s",
                                (result['folder_id'], campaign_id)
                            )
                        conn.commit()
                        conn.close()
                    elif result:
                        print(f"⚠️ [UAZAPI] create_advanced_campaign sem folder_id campaign_id={campaign_id} result={result}")
                    else:
                        print(f"⚠️ [UAZAPI] create_advanced_campaign falhou campaign_id={campaign_id}")
            else:
                print(f"⚠️ [Uazapi] Campanha {campaign_id}: use_uazapi_sender=true mas nenhuma instância Uazapi vinculada. Envio não iniciado.")
        
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

        print(f"🆕 [WhatsappService] Creating instance {instance_name} via {url}")
        
        try:
            response = requests.post(url, params=params, json=payload, headers=self.headers, timeout=15)
            # Log response body if error or just debug
            if response.status_code != 200:
                print(f"❌ Create API Status: {response.status_code}")
                print(f"❌ Create API Body: {response.text}")
                
            response.raise_for_status()
            
            print(f"✅ Create API Response: {response.text[:200]}") # Truncate for sanity
            
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error creating instance: {e}")
            if e.response:
                print(f"❌ Response: {e.response.text}")
            return None

    def get_qr_code(self, instance_key: str) -> dict:
        """Gets QR Code for the instance"""
        url = f"{self.base_url}/rest/instance/qrcode/{instance_key}"
        print(f"📷 [WhatsappService] Getting QR for {instance_key} via {url}")
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code != 200:
                 print(f"❌ QR API Status: {response.status_code}")
                 print(f"❌ QR API Body: {response.text}")
            
            response.raise_for_status()
            try:
                return response.json()
            except requests.exceptions.JSONDecodeError:
                # Some endpoints return raw strings or HTML
                return {"data": response.text}
        except requests.exceptions.RequestException as e:
            print(f"❌ Error getting QR code: {e}")
            return None

    def get_status(self, instance_key: str) -> dict:
        """Gets instance connection status"""
        url = f"{self.base_url}/rest/instance/{instance_key}"
        # print(f"🔍 [WhatsappService] Checking status for {instance_key}") # Too spammy if polling?
        # Let's log only errors or significant events
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                 print(f"❌ Status API Error: {response.status_code} - {response.text}")
            
            response.raise_for_status()
            data = response.json()
            
            # DEBUG: Log status payload to understand structure
            print(f"🔍 [WhatsappService] Status Payload: {data}")

            # API might return a list [ {InstanceObject} ]
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ Error getting status: {e}")
            return None

    def logout_instance(self, instance_key: str) -> dict:
        """Logs out WhatsApp instance"""
        url = f"{self.base_url}/rest/instance/{instance_key}/logout"
        print(f"🚪 [WhatsappService] Logging out {instance_key} via DELETE {url}")
        
        try:
            response = requests.delete(url, headers=self.headers, timeout=15)
            print(f"🚪 Logout API Status: {response.status_code}")
            
            if response.status_code == 404:
                return {"message": "Instance already logged out or not found"}
            
            if response.status_code != 200:
                print(f"🚪 Logout API Error Body: {response.text}")
                
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error logging out instance: {e}")
            return {"error": str(e)}

    def delete_instance(self, instance_key: str) -> dict:
        """Deletes WhatsApp instance"""
        # Doc: DELETE /rest/instance/{instance_key}/delete
        url = f"{self.base_url}/rest/instance/{instance_key}/delete"
        print(f"🗑️ [WhatsappService] Deleting {instance_key} via DELETE {url}")
        
        try:
            response = requests.delete(url, headers=self.headers, timeout=15)
            print(f"🗑️ Delete API Status: {response.status_code}")
            print(f"🗑️ Delete API Body: {response.text}")
            
            if response.status_code == 404:
                # CRITICAL FIX: Distinguish between "Instance validation failed" 404 and "Endpoint not found" 404
                # If API returns HTML, it's likely a bad URL/Proxy error.
                if 'text/html' in response.headers.get('Content-Type', ''):
                    print("❌ Error: Received HTML 404 from API - Endpoint likely incorrect.")
                    return {"error": "API Endpoint not found (404 HTML)"}
                
                return {"message": "Instance already deleted or not found"}
                
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error deleting instance: {e}")
            if e.response:
                 print(f"❌ Response: {e.response.text}")
            return {"error": str(e)}


@app.route("/whatsapp")
@login_required
def whatsapp_config():
    """Page to configure WhatsApp instance(s)"""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM instances WHERE user_id = %s ORDER BY id ASC", (current_user.id,))
        instances = cur.fetchall()
    conn.close()
    
    # Backward compatibility: pass first instance as 'instance' for non-super-admin template
    instance = instances[0] if instances else None
    
    return render_template("whatsapp_config.html", 
                           instance=instance, 
                           instances=instances,
                           is_super_admin=is_super_admin())


@app.route("/api/whatsapp/init", methods=["POST"])
@login_required
def init_whatsapp():
    """API to initialize a WhatsApp instance"""
    instance_name = request.json.get("instance_name") or ""
    
    # Check if user already has an instance (skip for super admin)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT name, apikey FROM instances WHERE user_id = %s", (current_user.id,))
        existing_instances = cur.fetchall()
    conn.close()

    if existing_instances and not is_super_admin():
        # Prevent duplicates for normal users
        return {
            "error": f"Você já possui uma instância criada. Nome: {existing_instances[0][0]}"
        }, 400

    
    # Sanitize if provided
    safe_name = ""
    if instance_name:
        safe_name = "".join(c for c in instance_name if c.isalnum() or c in ('-', '_'))
    
    try:
        if is_super_admin():
            # Superadmin usa Uazapi
            uazapi = UazapiService()
            result = uazapi.create_instance(safe_name if safe_name else "instance")
            if not result:
                return {"error": "Falha ao criar instância na Uazapi."}, 500
            instance_key = result.get('token') or (result.get('instance') or {}).get('token')
            if not instance_key:
                print(f"Warning: No token from Uazapi. Result: {result}")
                return {"error": "Falha ao obter token da instância. Resposta da API inválida."}, 500
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO instances (user_id, name, apikey, status, api_provider)
                        VALUES (%s, %s, %s, 'disconnected', 'uazapi')
                        """,
                        (current_user.id, instance_name or safe_name, instance_key)
                    )
                conn.commit()
            finally:
                conn.close()
            return {"status": "success", "key": instance_key, "data": result}
        
        # Usuários normais: MegaAPI
        service = WhatsappService()
        result = service.create_instance(safe_name if safe_name else None)
        
        if result:
            # Check API level error
            if result.get('error') is True:
                 pass

            # Save to DB
            instance_key = result.get('data', {}).get('instance_key')
            
            # Fallback 1: Top level (sometimes APIs vary)
            if not instance_key:
                instance_key = result.get('instance_key')
                
            # Fallback 2: safe_name if we sent it and API didn't return it but succeeded
            if not instance_key and safe_name:
                 if result.get('message') == 'Instance created' or result.get('error') is False:
                     instance_key = safe_name

            if not instance_key:
                 print(f"Warning: No key returned from Mega API. Result: {result}")
                 return {"error": "Falha ao obter ID da instância. Resposta da API inválida."}, 500

            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    # Simple INSERT - we already checked for existence
                    cur.execute(
                        """
                        INSERT INTO instances (user_id, name, apikey, status)
                        VALUES (%s, %s, %s, 'disconnected')
                        """,
                        (current_user.id, instance_name, instance_key)
                    )
                conn.commit()
            finally:
                conn.close()
            
            return {"status": "success", "key": instance_key, "data": result}
        
        return {"error": "Failed to create instance at provider"}, 500
    except Exception as e:
        print(f"Error in init_whatsapp: {e}")
        return {"error": f"Erro interno: {str(e)}"}, 500


@app.route("/api/whatsapp/qr/<instance_key>")
@login_required
def get_whatsapp_qr(instance_key):
    """API to get QR code"""
    # Verify ownership
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE apikey = %s", (instance_key,))
        row = cur.fetchone()
    conn.close()
    
    if not row or row[0] != current_user.id:
        return {"error": "Unauthorized"}, 403
    
    api_provider = row[1]
    
    if api_provider == 'uazapi':
        uazapi = UazapiService()
        result = uazapi.connect(instance_key)
        if not result:
            return {"error": "Falha ao obter QR code da Uazapi."}, 500
        instance_data = result.get('instance') or {}
        if isinstance(instance_data, dict) and instance_data.get('status') in ('connected', 'open'):
            return {"error": "Instância já está conectada! Não é necessário escanear QR Code."}, 200
        qrcode_val = result.get('qrcode') or instance_data.get('qrcode')
        if qrcode_val and isinstance(qrcode_val, str) and len(qrcode_val) > 50:
            return {"base64": qrcode_val}
        return {"error": "QR Code não disponível. Tente novamente em alguns segundos."}, 500
        
    service = WhatsappService()
    result = service.get_qr_code(instance_key)
    
    if result:
        print(f"📷 QR API raw response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        print(f"📷 QR API raw response: {str(result)[:500]}")
        
        # Try multiple known response formats from Mega API
        
        # Format 1: Direct base64 field with actual base64 string
        base64_val = result.get('base64')
        if base64_val and isinstance(base64_val, str) and len(base64_val) > 50:
            return {"base64": base64_val}
        
        # Format 2: qrcode field
        qrcode_val = result.get('qrcode')
        if qrcode_val and isinstance(qrcode_val, str) and len(qrcode_val) > 50:
            return {"base64": qrcode_val}
        
        # Format 3: Nested under 'data' key
        data_val = result.get('data')
        if data_val and isinstance(data_val, str):
            # Could be HTML with embedded img
            if '<img' in data_val:
                match = re.search(r'src=["\']data:image/png;base64,([^"\']+)["\']', data_val)
                if match:
                    return {"base64": match.group(1)}
            # Could be raw base64 string
            elif len(data_val) > 50:
                return {"base64": data_val}
        
        # Format 4: Check if response itself has a 'code' or 'pairingCode' (some API versions)
        pairing_code = result.get('pairingCode') or result.get('code')
        if pairing_code and isinstance(pairing_code, str):
            return {"pairingCode": pairing_code, "error": f"Use o código de pareamento: {pairing_code}"}
        
        # Format 5: Instance might already be connected
        instance_data = result.get('instance', {})
        if isinstance(instance_data, dict) and instance_data.get('status') in ('connected', 'open'):
            return {"error": "Instância já está conectada! Não é necessário escanear QR Code."}, 200
        
        # If nothing matched, return descriptive error
        print(f"⚠️ QR response format not recognized: {result}")
        return {"error": "QR Code não disponível. Tente novamente em alguns segundos."}, 500
    return {"error": "Falha ao obter QR code da API"}, 500


@app.route("/api/whatsapp/status/<instance_key>")
@login_required
def get_whatsapp_status(instance_key):
    """API to get status"""
    # Verify ownership
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id, COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE apikey = %s", (instance_key,))
        row = cur.fetchone()
    conn.close()
    
    if not row or row[1] != current_user.id:
        return {"error": "Unauthorized"}, 403
    
    api_provider = row[2]
    
    if api_provider == 'uazapi':
        uazapi = UazapiService()
        result = uazapi.get_status(instance_key)
        if not result:
            return {"error": "Failed to get status"}, 500
        instance_data = result.get('instance') or result
        status_val = instance_data.get('status', 'disconnected') if isinstance(instance_data, dict) else 'disconnected'
        new_status = status_val if status_val in ('connected', 'connecting', 'disconnected') else 'disconnected'
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE instances SET status = %s, updated_at = NOW() WHERE id = %s", (new_status, row[0]))
        conn.commit()
        conn.close()
        print(f"Status checked for instance {instance_key} (User {current_user.id}): {new_status} (Uazapi)")
        return result
    
    service = WhatsappService()
    result = service.get_status(instance_key)
    
    if result:
        # Comprehensive status detection logic (matching admin_check_whatsapp_status)
        # Mega API Structure variations:
        # 1. { "instance_data": { "phone_connected": true, ... } }
        # 2. { "phone_connected": true, ... } (sometimes top-level)
        # 3. { "status": "CONNECTED" or "open" }
        # 4. [ { ... } ] (Array if looking up by key)
        
        is_connected = False
        
        # Handle array response
        if isinstance(result, list) and len(result) > 0:
            result = result[0]
            
        # Check various possible status indicators
        if result.get('instance_data'):
            is_connected = result['instance_data'].get('phone_connected', False)
        elif 'phone_connected' in result:
            is_connected = result.get('phone_connected', False)
        elif result.get('status') == 'CONNECTED':
            is_connected = True
        elif result.get('status') == 'open':
            is_connected = True
        # NEW: Handle nested instance object from payload: {'instance': {'status': 'connected'}}
        elif isinstance(result.get('instance'), dict):
             status_val = result['instance'].get('status')
             if status_val in ['connected', 'CONNECTED', 'open']:
                 is_connected = True

            
        # If there's an error flag, override to disconnected
        if result.get('error'):
            is_connected = False
        
        new_status = 'connected' if is_connected else 'disconnected'
        
        # Update DB with new status
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE instances SET status = %s, updated_at = NOW() WHERE id = %s", (new_status, row[0]))
        conn.commit()
        conn.close()
        
        # Debug logging
        print(f"Status checked for instance {instance_key} (User {current_user.id}): {new_status} (Connected: {is_connected})")
        
        return result
    return {"error": "Failed to get status"}, 500


@app.route("/api/whatsapp/delete/<instance_key>", methods=["POST"])
@login_required
def delete_whatsapp_instance(instance_key):
    """API to delete instance"""
    print(f"🗑️ Deleting instance {instance_key} for user {current_user.id}...")
    
    # Verify ownership
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id, COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE apikey = %s", (instance_key,))
        row = cur.fetchone()
    conn.close()
    
    if not row or row[1] != current_user.id:
        print(f"❌ Unauthorized delete attempt for {instance_key}")
        return {"error": "Unauthorized"}, 403
    
    api_provider = row[2]
    
    if api_provider == 'uazapi':
        uazapi = UazapiService()
        success, status_code = uazapi.delete_instance(instance_key)
        print(f"🗑️ Uazapi Delete Result: success={success}, status={status_code}")
        # Sempre remover do DB: se Uazapi falhou (ex: instância já deletada lá),
        # o usuário ainda deve conseguir remover da UI
        print(f"🗑️ Removing from database...")
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM instances WHERE id = %s", (row[0],))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Instance deleted"}
    
    service = WhatsappService()
    
    # 0. Try Logout first (ensure session is killed)
    try:
        service.logout_instance(instance_key)
    except:
        pass # Continue to delete

    # 1. Delete from Mega API
    result = service.delete_instance(instance_key)
    print(f"🗑️ Mega API Delete Result: {result}")
    
    # Check if Result implies a failure (e.g. valid JSON error)
    if result and result.get('error') and 'Endpoint not found' in str(result.get('error')):
         return {"error": "Falha na API: Endpoint de deleção não encontrado. Contate o suporte."}, 500

    # 2. Delete from DB (Only if API didn't critically fail)
    # We proceed even if API says "not found" (idempotency)
    
    print(f"🗑️ Removing from database...")
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM instances WHERE id = %s", (row[0],))
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Instance deleted"}




@app.route("/api/campaigns/<int:campaign_id>/stats")
@login_required
def get_campaign_stats(campaign_id):
    """API para obter estatísticas de uma campanha"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Verificar se a campanha pertence ao usuário e se usa Uazapi
            cur.execute(
                "SELECT id, closed_deals, use_uazapi_sender, uazapi_folder_id, status FROM campaigns WHERE id = %s AND user_id = %s",
                (campaign_id, current_user.id)
            )
            campaign = cur.fetchone()
            
            if not campaign:
                conn.close()
                return {"error": "Campaign not found"}, 404
            
            closed_deals = campaign['closed_deals'] or 0
            
            # Buscar estatísticas de leads (campaign_leads)
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
        
        sent = stats['sent'] or 0
        pending = stats['pending'] or 0
        failed = stats['failed'] or 0
        total_leads = stats['total_leads'] or 0
        uazapi_debug = {}  # para ?debug=1

        # Campanhas Uazapi: campaign_leads nunca é atualizado pelo envio remoto.
        # Buscar contagens reais via API Uazapi.
        # Estratégia: 1) list_folders (log_sucess/log_failed) como fonte primária; 2) list_messages como fallback.
        if campaign.get('use_uazapi_sender') and campaign.get('uazapi_folder_id'):
            try:
                conn2 = get_db_connection()
                with conn2.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT i.apikey FROM campaign_instances ci
                        JOIN instances i ON i.id = ci.instance_id
                        WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
                        LIMIT 1
                    """, (campaign_id,))
                    inst = cur.fetchone()
                conn2.close()
                if inst and inst.get('apikey'):
                    uazapi = UazapiService()
                    folder_id = campaign['uazapi_folder_id']
                    token = inst['apikey']
                    uazapi_sent, uazapi_failed, uazapi_scheduled = 0, 0, 0
                    uazapi_debug = {}
                    source_used = None

                    # 1) Tentar list_folders (Active) — MessageQueueFolder tem log_sucess, log_failed, log_total
                    folders = uazapi.list_folders(token, status='Active')
                    if isinstance(folders, dict):
                        folders = folders.get('folders') or folders.get('data') or folders.get('items') or []
                    if isinstance(folders, list):
                        for f in folders:
                            fid = f.get('id') or f.get('folder_id') or f.get('folderId')
                            if str(fid) == str(folder_id):
                                # log_sucess (typo na spec), log_delivered, log_failed
                                uazapi_sent = int(f.get('log_sucess', 0) or f.get('log_delivered', 0) or f.get('log_success', 0) or 0)
                                uazapi_failed = int(f.get('log_failed', 0) or 0)
                                uazapi_debug = {"uazapi_sent": uazapi_sent, "uazapi_failed": uazapi_failed, "source": "list_folders"}
                                source_used = "list_folders"
                                break

                    # 2) Fallback: list_messages por status (pageSize maior para parsing robusto)
                    if source_used is None:
                        def _count_listmessages(r):
                            if not r:
                                return 0
                            p = r.get('pagination') or {}
                            if isinstance(p, dict):
                                total = int(p.get('total', 0) or p.get('totalRecords', 0) or 0)
                                if total > 0:
                                    return total
                            msgs = r.get('messages') or r.get('data') or []
                            if isinstance(msgs, list):
                                return len(msgs)
                            return 0

                        r_sent = uazapi.list_messages(token, folder_id, message_status='Sent', page=1, page_size=1000)
                        r_failed = uazapi.list_messages(token, folder_id, message_status='Failed', page=1, page_size=1000)
                        r_scheduled = uazapi.list_messages(token, folder_id, message_status='Scheduled', page=1, page_size=1000)
                        uazapi_sent = _count_listmessages(r_sent)
                        uazapi_failed = _count_listmessages(r_failed)
                        uazapi_scheduled = _count_listmessages(r_scheduled)
                        uazapi_debug = {"uazapi_sent": uazapi_sent, "uazapi_failed": uazapi_failed, "uazapi_scheduled": uazapi_scheduled, "source": "list_messages"}
                        source_used = "list_messages"
                        if request.args.get('debug') == '1' and uazapi_sent == 0 and uazapi_failed == 0 and uazapi_scheduled == 0:
                            uazapi_debug["_raw_sent"] = r_sent
                            uazapi_debug["_raw_failed"] = r_failed
                            uazapi_debug["_raw_scheduled"] = r_scheduled

                    if uazapi_sent > 0 or uazapi_failed > 0 or uazapi_scheduled > 0:
                        sent = uazapi_sent
                        failed = uazapi_failed
                        # invalid = subconjunto de failed (números inválidos); para Uazapi não distinguimos, então failed inclui inválidos
                        pending = max(0, total_leads - sent - failed) if total_leads else uazapi_scheduled
                    elif campaign.get('status') == 'running' and total_leads > 0:
                        now_ts = time.time()
                        last = _stats_uazapi_warning_last.get(campaign_id, 0)
                        if now_ts - last >= STATS_UAZAPI_WARNING_COOLDOWN:
                            print(f"⚠️ [Stats] Campanha {campaign_id} Uazapi: {source_used or 'API'} retornou 0 para todos os status. Verificar API/token.")
                            _stats_uazapi_warning_last[campaign_id] = now_ts
            except Exception as e:
                uazapi_debug = {"uazapi_error": str(e)}
                print(f"⚠️ [Stats] Erro ao buscar stats Uazapi para campanha {campaign_id}: {e}")
        
        conversion_rate = round((closed_deals / sent * 100), 1) if sent > 0 else 0
        
        result = {
            "total_leads": total_leads,
            "sent": sent,
            "pending": pending,
            "failed": failed,
            "invalid": stats['invalid'] or 0,
            "closed_deals": closed_deals,
            "conversion_rate": conversion_rate,
            "started_at": stats['started_at'].isoformat() if stats['started_at'] else None,
            "last_sent_at": stats['last_sent_at'].isoformat() if stats['last_sent_at'] else None
        }
        
        # Debug: ?debug=1 retorna fonte e dados brutos para diagnóstico
        if request.args.get('debug') == '1':
            result["debug"] = {
                "source": "uazapi" if (campaign.get('use_uazapi_sender') and campaign.get('uazapi_folder_id')) else "db",
                "campaign_status": campaign.get('status'),
                "uazapi_folder_id": campaign.get('uazapi_folder_id'),  # para comparar com list_folders
                **uazapi_debug,
            }
        
        return result
        
    except Exception as e:
        print(f"Erro ao obter stats da campanha: {e}")
        return {"error": str(e)}, 500


@app.route("/api/campaigns/<int:campaign_id>/sync-uazapi", methods=["POST"])
@login_required
def sync_campaign_uazapi_stats(campaign_id):
    """
    Sincroniza campaign_leads com a API Uazapi (list_messages).
    Atualiza status sent/failed no DB a partir das mensagens retornadas.
    Útil quando list_folders/list_messages para stats retornam 0 ou estão desatualizados.
    """
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT c.id, c.uazapi_folder_id, c.use_uazapi_sender
                   FROM campaigns c
                   WHERE c.id = %s AND c.user_id = %s""",
                (campaign_id, current_user.id)
            )
            campaign = cur.fetchone()
        conn.close()
        if not campaign or not campaign.get('use_uazapi_sender') or not campaign.get('uazapi_folder_id'):
            return json.dumps({"error": "Campanha não usa Uazapi ou sem folder_id"}), 400

        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.apikey FROM campaign_instances ci
                JOIN instances i ON i.id = ci.instance_id
                WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
                LIMIT 1
            """, (campaign_id,))
            inst = cur.fetchone()
        conn.close()
        if not inst or not inst.get('apikey'):
            return json.dumps({"error": "Instância Uazapi não encontrada"}), 404

        uazapi = UazapiService()
        token = inst['apikey']
        folder_id = campaign['uazapi_folder_id']

        def _extract_phones_from_messages(resp):
            phones = set()
            if not resp:
                return phones
            msgs = resp.get('messages') or resp.get('data') or []
            for m in msgs if isinstance(msgs, list) else []:
                num = m.get('number') or m.get('chatid') or m.get('chatId') or m.get('sender') or ''
                if num:
                    clean = re.sub(r'\D', '', str(num).split('@')[0])
                    if len(clean) >= 10:
                        phones.add(clean)
            return phones

        r_sent = uazapi.list_messages(token, folder_id, message_status='Sent', page=1, page_size=1000)
        r_failed = uazapi.list_messages(token, folder_id, message_status='Failed', page=1, page_size=1000)
        sent_phones = _extract_phones_from_messages(r_sent)
        failed_phones = _extract_phones_from_messages(r_failed)

        updated_sent = 0
        updated_failed = 0
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # Match por phone normalizado (apenas dígitos)
                def _phone_match_params(ph):
                    if len(ph) <= 11 and not ph.startswith('55'):
                        return (ph, '55' + ph)  # DB pode ter 55+DDD+num
                    return (ph, ph)

                for ph in sent_phones:
                    p1, p2 = _phone_match_params(ph)
                    cur.execute(
                        """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW())
                           WHERE campaign_id = %s AND status != 'sent'
                           AND regexp_replace(phone, '[^0-9]', '', 'g') IN (%s, %s)""",
                        (campaign_id, p1, p2)
                    )
                    updated_sent += cur.rowcount
                for ph in failed_phones:
                    p1, p2 = _phone_match_params(ph)
                    cur.execute(
                        """UPDATE campaign_leads SET status = 'failed', sent_at = COALESCE(sent_at, NOW())
                           WHERE campaign_id = %s AND status NOT IN ('sent', 'failed')
                           AND regexp_replace(phone, '[^0-9]', '', 'g') IN (%s, %s)""",
                        (campaign_id, p1, p2)
                    )
                    updated_failed += cur.rowcount
            conn.commit()
        finally:
            conn.close()

        return json.dumps({
            "success": True,
            "synced": {"sent": len(sent_phones), "failed": len(failed_phones)},
            "updated": {"sent": updated_sent, "failed": updated_failed}
        })
    except Exception as e:
        print(f"Erro ao sincronizar stats Uazapi campanha {campaign_id}: {e}")
        return json.dumps({"error": str(e)}), 500


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
            
            # Campanhas ativas (status='running')
            cur.execute(
                "SELECT COUNT(*) as active FROM campaigns WHERE user_id = %s AND status = 'running'",
                (current_user.id,)
            )
            active_campaigns = cur.fetchone()['active']
            
            # Leads extraídos HOJE (de scraping_jobs - CORRIGIDO)
            cur.execute(
                """
                SELECT COALESCE(SUM(lead_count), 0) as total
                FROM scraping_jobs
                WHERE user_id = %s 
                  AND status = 'completed'
                  AND status = 'completed'
                  AND EXTRACT(MONTH FROM created_at) = EXTRACT(MONTH FROM CURRENT_DATE)
                  AND EXTRACT(YEAR FROM created_at) = EXTRACT(YEAR FROM CURRENT_DATE)
                """,
                (current_user.id,)
            )
            today_leads = cur.fetchone()['total']
            
            # Mensagens enviadas NO MÊS (ALTERADO de hoje para mês)
            cur.execute(
                """
                SELECT COUNT(*) as count FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s 
                  AND EXTRACT(MONTH FROM cl.sent_at) = EXTRACT(MONTH FROM CURRENT_DATE)
                  AND EXTRACT(YEAR FROM cl.sent_at) = EXTRACT(YEAR FROM CURRENT_DATE)
                  AND cl.status = 'sent'
                """,
                (current_user.id,)
            )
            month_sent = cur.fetchone()['count']
            
            # Taxa de sucesso NO MÊS (ALTERADO de hoje para mês)
            cur.execute(
                """
                SELECT 
                    COUNT(CASE WHEN cl.status = 'sent' THEN 1 END) as sent,
                    COUNT(CASE WHEN cl.status IN ('failed', 'invalid') THEN 1 END) as failed
                FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s 
                  AND EXTRACT(MONTH FROM cl.sent_at) = EXTRACT(MONTH FROM CURRENT_DATE)
                  AND EXTRACT(YEAR FROM cl.sent_at) = EXTRACT(YEAR FROM CURRENT_DATE)
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
            
            # Taxa de conversão geral (usando mensagens mensais)
            overall_conversion = round((total_deals / month_sent * 100), 1) if month_sent > 0 else 0
        
        conn.close()
        
        return {
            "today_leads_extracted": today_leads,
            "today_messages_sent": month_sent,  # Nome mantido para compatibilidade frontend
            "today_success_rate": success_rate,  # Nome mantido para compatibilidade frontend
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


@app.route('/campaigns/<int:campaign_id>/edit')
@login_required
def edit_campaign(campaign_id):
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        flash("Campanha não encontrada.", "error")
        return redirect(url_for('campaigns'))
    return render_template('campaigns_edit.html', campaign=campaign)


@app.route('/api/campaigns/<int:campaign_id>/leads')
@login_required
def get_campaign_leads(campaign_id):
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
        
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    
    # Filters
    name_filter = request.args.get('name', '')
    phone_filter = request.args.get('phone', '')
    status_filter = request.args.get('status', '')

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Build query dynamically
        base_query = "FROM campaign_leads WHERE campaign_id = %s"
        params = [campaign_id]
        
        if name_filter:
            base_query += " AND name ILIKE %s"
            params.append(f"%{name_filter}%")
        if phone_filter:
            base_query += " AND phone ILIKE %s"
            params.append(f"%{phone_filter}%")
        if status_filter:
            base_query += " AND status = %s"
            params.append(status_filter)

        # Count total filtered
        cur.execute(f"SELECT COUNT(*) as count {base_query}", tuple(params))
        total = cur.fetchone()['count']
        
        # Fetch filtered leads
        query = f"""
            SELECT id, phone, name, whatsapp_link, status, log, sent_at 
            {base_query}
            ORDER BY id ASC
            LIMIT %s OFFSET %s
        """
        params.extend([per_page, offset])
        
        cur.execute(query, tuple(params))
        leads = cur.fetchall()
    conn.close()
    
    return json.dumps({
        'leads': [dict(l, sent_at=l['sent_at'].isoformat() if l['sent_at'] else None) for l in leads],
        'total': total,
        'page': page,
        'pages': (total + per_page - 1) // per_page
    }, default=str)


@app.route('/api/campaigns/<int:campaign_id>/leads/<int:lead_id>', methods=['DELETE'])
@login_required
def delete_campaign_lead(campaign_id, lead_id):
    """API para excluir um lead específico de uma campanha"""
    # 1. Verificar permissão (Campanha pertence ao usuário?)
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada ou acesso negado'}), 404
        
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 2. Verificar se o lead pertence à campanha
            cur.execute("SELECT id FROM campaign_leads WHERE id = %s AND campaign_id = %s", (lead_id, campaign_id))
            if not cur.fetchone():
                return json.dumps({'error': 'Lead não encontrado nesta campanha'}), 404
            
            # 3. Excluir lead
            cur.execute("DELETE FROM campaign_leads WHERE id = %s", (lead_id,))
            
        conn.commit()
    except Exception as e:
        if 'conn' in locals(): conn.rollback()
        return json.dumps({'error': str(e)}), 500
    finally:
        if 'conn' in locals(): conn.close()
        
    return json.dumps({'success': True})


@app.route('/api/templates', methods=['GET', 'POST'])
@login_required
def message_templates():
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        content = data.get('content')
        
        if not name or not content:
            return json.dumps({'error': 'Nome e conteúdo obrigatórios'}), 400
            
        tpl = MessageTemplate.create(current_user.id, name, content)
        return json.dumps({'id': tpl.id, 'name': tpl.name, 'content': tpl.content})
        
    else:
        templates = MessageTemplate.get_by_user(current_user.id)
        return json.dumps([vars(t) for t in templates], default=str)


@app.route('/api/campaigns/<int:campaign_id>/update', methods=['POST'])
@login_required
def update_campaign(campaign_id):
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
    
    data = request.json
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if 'name' in data:
                cur.execute("UPDATE campaigns SET name = %s WHERE id = %s", (data['name'], campaign_id))
            
            if 'scheduled_start' in data:
                 cur.execute("UPDATE campaigns SET scheduled_start = %s WHERE id = %s", (data['scheduled_start'], campaign_id))
                 
            if 'message_templates' in data:
                templates = json.dumps(data['message_templates'])
                cur.execute("UPDATE campaigns SET message_template = %s WHERE id = %s", (templates, campaign_id))
                
        conn.commit()
        return json.dumps({'success': True})
    except Exception as e:
        conn.rollback()
        return json.dumps({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/campaigns/<int:campaign_id>/steps/<int:step_number>', methods=['GET'])
@login_required
def get_step_template(campaign_id, step_number):
    """Retorna o template de mensagem de um passo específico"""
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
        
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT message_template 
                FROM campaign_steps 
                WHERE campaign_id = %s AND step_number = %s
            """, (campaign_id, step_number))
            row = cur.fetchone()
            
            if not row:
                return json.dumps({'error': 'Passo não encontrado'}), 404
                
            # Parse template (handle string or list)
            tpl = row['message_template']
            if not tpl:
                tpl = []
            elif isinstance(tpl, str):
                try:
                    loaded = json.loads(tpl)
                    if isinstance(loaded, list):
                        tpl = loaded
                    else:
                        tpl = [tpl] # Legacy string format
                except:
                    tpl = [tpl] # Legacy plain string
            
            return json.dumps({'template': tpl})
            
    except Exception as e:
        return json.dumps({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/campaigns/<int:campaign_id>/steps/<int:step_number>', methods=['POST'])
@login_required
def update_step_template(campaign_id, step_number):
    """Atualiza o template de mensagem de um passo específico"""
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
        
    data = request.json
    template_list = data.get('template')
    
    if not isinstance(template_list, list):
        return json.dumps({'error': 'Formato inválido. Esperado lista de mensagens.'}), 400
        
    # Filter empty strings
    template_list = [t.strip() for t in template_list if t and t.strip()]
    
    if not template_list:
        return json.dumps({'error': 'A mensagem não pode estar vazia.'}), 400
        
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Check if step exists
            cur.execute("SELECT 1 FROM campaign_steps WHERE campaign_id = %s AND step_number = %s", (campaign_id, step_number))
            if not cur.fetchone():
                return json.dumps({'error': 'Passo não encontrado'}), 404
            
            # Update
            json_tpl = json.dumps(template_list, ensure_ascii=False)
            cur.execute("""
                UPDATE campaign_steps 
                SET message_template = %s 
                WHERE campaign_id = %s AND step_number = %s
            """, (json_tpl, campaign_id, step_number))
            
        conn.commit()
        return json.dumps({'success': True})
            
    except Exception as e:
        conn.rollback()
        return json.dumps({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/campaigns/<int:campaign_id>/replace-leads', methods=['POST'])
@login_required
def replace_leads(campaign_id):
    campaign = Campaign.get_by_id(campaign_id, current_user.id)
    if not campaign:
        return json.dumps({'error': 'Campanha não encontrada'}), 404
        
    data = request.json
    job_id = data.get('job_id')
    
    if not job_id:
        return json.dumps({'error': 'Job ID obrigatório'}), 400
        
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT results_path FROM scraping_jobs WHERE id = %s AND user_id = %s", (job_id, current_user.id))
            job = cur.fetchone()
        
        if not job or not job['results_path'] or not os.path.exists(job['results_path']):
            conn.close()
            return json.dumps({'error': 'Arquivo de leads não encontrado'}), 404
            
        file_path = job['results_path']
        valid_leads = []
        
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path, dtype=str)
        elif file_path.endswith('.xlsx'):
            df = pd.read_excel(file_path, dtype=str)
        else:
             try:
                 df = pd.read_csv(file_path, dtype=str)
             except:
                 conn.close()
                 return json.dumps({'error': 'Formato de arquivo desconhecido'}), 400

        # Normalization Logic (Simplified version of create_campaign)
        cols = [c.lower() for c in df.columns]
        phone_col = next((c for c in cols if 'phone' in c or 'telefone' in c or 'celular' in c or 'whatsapp' in c), None)
        name_col = next((c for c in cols if 'name' in c or 'nome' in c), None)
        link_col = next((c for c in cols if 'link' in c or 'url' in c), None)
        status_col = next((c for c in cols if 'status' in c), None)
        
        # Helper to extract phone
        def extract_phone(link):
            if not link: return None
            import re
            patterns = [r'wa\.me/([0-9]+)', r'phone=([0-9]+)', r'whatsapp\.com/send\?phone=([0-9]+)']
            for pattern in patterns:
                match = re.search(pattern, str(link))
                if match: return match.group(1)
            digits = re.sub(r'\D', '', str(link))
            if len(digits) >= 10: return digits
            return None

        for _, row in df.iterrows():
            # Check Status if exists (1 = ready)
            # Safe comparison for string '1'
            if status_col:
                val = str(row.iloc[cols.index(status_col)]).strip()
                if val != '1':
                    continue
                
            lead_data = {}
            if name_col: lead_data['name'] = str(row.iloc[cols.index(name_col)])
            
            raw_phone = None
            raw_link = None
            
            if phone_col: raw_phone = str(row.iloc[cols.index(phone_col)])
            if link_col: raw_link = str(row.iloc[cols.index(link_col)])
            
            final_phone = extract_phone(raw_link) or extract_phone(raw_phone)
            
            if final_phone:
                lead_data['phone'] = final_phone
                lead_data['whatsapp_link'] = raw_link if raw_link else f"https://wa.me/{final_phone}"
                valid_leads.append(lead_data)
        
        if not valid_leads:
             conn.close()
             return json.dumps({'error': 'Nenhum lead válido encontrado nesta lista (verifique filtro de status=1)'}), 400

        # Replace logic: Delete pending leads, insert new ones
        with conn.cursor() as cur:
            # Delete only pending to preserve history of sent leads
            cur.execute("DELETE FROM campaign_leads WHERE campaign_id = %s AND status = 'pending'", (campaign_id,))
            
            # Insert new
            args_str = ','.join(
                cur.mogrify("(%s, %s, %s, %s, %s)", 
                           (campaign_id, l.get('phone'), l.get('name'), l.get('whatsapp_link'), 'pending')).decode('utf-8') 
                for l in valid_leads
            )
            cur.execute("INSERT INTO campaign_leads (campaign_id, phone, name, whatsapp_link, status) VALUES " + args_str)
            
        conn.commit()
        conn.close()
        
        return json.dumps({'success': True, 'count': len(valid_leads)})
        
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return json.dumps({'error': str(e)}), 500

# ==========================================
# MIGRATION ROUTE (TEMPORARY - REMOVE AFTER USE)
# ==========================================
@app.route('/migrate_cadence')
@login_required
def migrate_cadence_route():
    # Security check: only super admin
    if current_user.email != SUPER_ADMIN_EMAIL:
        return "Unauthorized", 403
    
    conn = get_db_connection()
    log = ["<h1>Relatório de Migração de Cadência</h1>"]
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Get Super Admin ID
            cur.execute("SELECT id FROM users WHERE email = %s", (SUPER_ADMIN_EMAIL,))
            user = cur.fetchone()
            if not user:
                return "Super admin ({}) not found".format(SUPER_ADMIN_EMAIL), 404
            
            user_id = user['id']
            log.append(f"<p><strong>Super Admin ID:</strong> {user_id}</p>")
            
            # 2. Get all campaigns
            cur.execute("SELECT id, name FROM campaigns WHERE user_id = %s", (user_id,))
            campaigns = cur.fetchall()
            
            log.append(f"<p>Encontradas {len(campaigns)} campanhas.</p><ul>")
            
            for camp in campaigns:
                cid = camp['id']
                cname = camp['name']
                log.append(f"<li><strong>Campanha {cid} ({cname}):</strong>")
                
                # Enable cadence
                cur.execute("UPDATE campaigns SET enable_cadence = TRUE, terms_accepted = TRUE WHERE id = %s", (cid,))
                
                # Check steps
                cur.execute("SELECT count(*) as count FROM campaign_steps WHERE campaign_id = %s", (cid,))
                count = cur.fetchone()['count']
                
                if count == 0:
                    steps = [
                        (1, "Mensagem Inicial", "[]", 0),
                        (2, "Follow-up 1", "Olá, conseguiu ver minha mensagem anterior?", 1),
                        (3, "Follow-up 2", "Oi novamente! Imagino que esteja corrido. Se tiver interesse, estou por aqui.", 2),
                        (4, "Break-up", "Última tentativa. Vou encerrar meu contato por enquanto.", 3)
                    ]
                    for s_num, s_label, s_msg, s_delay in steps:
                        cur.execute("""
                            INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template, delay_days)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (cid, s_num, s_label, s_msg, s_delay))
                    log.append(" <span style='color:green'>Steps criados.</span>")
                else:
                    log.append(" <span style='color:gray'>Steps já existem.</span>")
                
                # Update leads
                cur.execute("""
                    UPDATE campaign_leads
                    SET current_step = 1,
                        cadence_status = 'snoozed',
                        snooze_until = NOW() + INTERVAL '1 day',
                        last_message_sent_at = COALESCE(last_message_sent_at, NOW())
                    WHERE campaign_id = %s
                      AND status = 'sent'
                      AND (cadence_status IS NULL OR cadence_status = 'pending')
                """, (cid,))
                leads_count = cur.rowcount
                if leads_count > 0:
                    log.append(f" <span style='color:blue'>{leads_count} leads movidos para cadência (snoozed 1 day).</span>")
                
                log.append("</li>")
            
            log.append("</ul><p><strong>Migração concluída com sucesso!</strong></p>")
                
        conn.commit()
        return "".join(log)
    except Exception as e:
        conn.rollback()
        return f"Error: {str(e)}", 500
    finally:
        conn.close()


@app.route('/migrate_notes')
@login_required
def migrate_notes_route():
    # Security check: only super admin
    if current_user.email != SUPER_ADMIN_EMAIL:
        return "Unauthorized", 403
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS notes TEXT;")
        conn.commit()
        return "Migration notes executed successfully."
    except Exception as e:
        conn.rollback()
        return f"Error: {str(e)}", 500
    finally:
        conn.close()


@app.route('/migrate_enrichment')
@login_required
def migrate_enrichment_route():
    # Security check: only super admin
    if current_user.email != SUPER_ADMIN_EMAIL:
        return "Unauthorized", 403
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS address TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS website TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS category TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS location TEXT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS reviews_count FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS reviews_rating FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS latitude FLOAT;")
            cur.execute("ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS longitude FLOAT;")
        conn.commit()
        return "Enrichment Migration executed successfully."
    except Exception as e:
        conn.rollback()
        return f"Error: {str(e)}", 500
    finally:
        conn.close()

# ==========================================
# SYNC SNOOZED CONVERSATIONS TO CHATWOOT
# ==========================================
@app.route('/sync_chatwoot_snooze')
@login_required
def sync_chatwoot_snooze_route():
    # Security check: only super admin (Augusto)
    if current_user.email != SUPER_ADMIN_EMAIL:
        return "Unauthorized: Only Super Admin can run this sync", 403
        
    chatwoot_url = os.environ.get('CHATWOOT_API_URL', 'https://chatwoot.wbtech.dev')
    chatwoot_token = os.environ.get('CHATWOOT_ACCESS_TOKEN')
    chatwoot_account_id = os.environ.get('CHATWOOT_ACCOUNT_ID', '2')
    
    if not chatwoot_token:
        # Try to load from .env if not in os.environ (for local dev)
        from dotenv import load_dotenv
        load_dotenv()
        chatwoot_token = os.environ.get('CHATWOOT_ACCESS_TOKEN')
        if not chatwoot_token:
            return "Error: CHATWOOT_ACCESS_TOKEN not properly configured in env variables", 500

    log = ["<h1>Log de Sincronização Chatwoot (Snooze + Discovery v2)</h1>"]
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Get User ID
            cur.execute("SELECT id FROM users WHERE email = %s", (SUPER_ADMIN_EMAIL,))
            user = cur.fetchone()
            if not user:
                return f"Super admin ({SUPER_ADMIN_EMAIL}) not found", 404
            user_id = user['id']
            
            # 2. Find Snoozed Leads (Check ALL snoozed leads, even without conversation_id)
            query = """
                SELECT cl.id, cl.chatwoot_conversation_id, cl.snooze_until, cl.phone, cl.name, c.name as campaign_name
                FROM campaign_leads cl
                JOIN campaigns c ON cl.campaign_id = c.id
                WHERE c.user_id = %s
                AND cl.cadence_status = 'snoozed'
                AND cl.snooze_until > NOW()
            """
            # ==========================================
            # MANUAL CORRECTIONS (User provided)
            # ==========================================
            manual_fixes = {
                6386: 890,
                6388: 895,
                6389: 900,
                6393: 907,
                6617: 877,
                6618: 880,
                6622: 887,
                6624: 891, # Rule: Check reply/human
                6626: 901,
                6627: 903  # Rule: Label check
            }
            
            for l_id, c_id in manual_fixes.items():
                cur.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (c_id, l_id))
            conn.commit()
            
            # Re-fetch leads after update
            cur.execute(query, (user_id,))
            leads = cur.fetchall()
            
            log.append(f"<p>Encontrados <strong>{len(leads)} leads</strong> em estado 'snoozed' localmente.</p><ul>")
            
            # Calculate tomorrow 8 AM timestamp for Chatwoot
            tomorrow_8am = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            snooze_timestamp = int(tomorrow_8am.timestamp())
            log.append(f"<p>Horário de retorno definido para: <strong>{tomorrow_8am.strftime('%d/%m/%Y %H:%M')}</strong></p>")
            
            headers = {
                "api_access_token": chatwoot_token,
                "Content-Type": "application/json"
            }
            
            success_count = 0
            linked_count = 0
            
            for lead in leads:
                conv_id = lead['chatwoot_conversation_id']
                phone = lead['phone']
                name = lead['name']
                lead_id = lead['id']
                
                # If no conversation ID, try to find it
                if not conv_id:
                    try:
                        clean_phone = re.sub(r'\D', '', str(phone or ''))
                        contact_id = None
                        
                        strategies = []
                        if clean_phone:
                            strategies.append(('Raw', clean_phone))
                            strategies.append(('+Phone', f"+{clean_phone}"))
                            # Try last 9 digits (no country/area code sometimes)
                            if len(clean_phone) >= 9:
                                strategies.append(('Last9', clean_phone[-9:]))
                            # Try last 8 digits (very broad match)
                            if len(clean_phone) >= 8:
                                strategies.append(('Last8', clean_phone[-8:]))
                        
                        if name:
                            strategies.append(('Name', name))

                        for label, query_val in strategies:
                            if contact_id: break # Found!
                            
                            try:
                                search_url = f"{chatwoot_url}/api/v1/accounts/{chatwoot_account_id}/contacts/search"
                                search_resp = requests.get(search_url, params={'q': query_val}, headers=headers, timeout=5)
                                if search_resp.status_code == 200:
                                    data = search_resp.json()
                                    if data.get('payload'):
                                        # Use the first one
                                        match = data['payload'][0]
                                        contact_id = match['id']
                            except:
                                pass

                        if contact_id:
                            # Get Conversations for Contact
                            conv_url = f"{chatwoot_url}/api/v1/accounts/{chatwoot_account_id}/contacts/{contact_id}/conversations"
                            conv_resp = requests.get(conv_url, headers=headers, timeout=5)
                            
                            if conv_resp.status_code == 200:
                                conv_data = conv_resp.json()
                                if conv_data.get('payload'):
                                    # Pick the most recent one
                                    conv_id = conv_data['payload'][0]['id']
                                    
                                    # Update Database
                                    conn2 = get_db_connection()
                                    with conn2.cursor() as cur2:
                                        cur2.execute("UPDATE campaign_leads SET chatwoot_conversation_id = %s WHERE id = %s", (conv_id, lead_id))
                                    conn2.commit()
                                    conn2.close()
                                    linked_count += 1
                                    log.append(f"<li>🔗 Lead #{lead_id} ({name}): Vinculado à conversa {conv_id} (via {label})</li>")
                                else:
                                     log.append(f"<li>❓ Lead #{lead_id}: Contato encontrado (ID {contact_id}) mas sem conversas.</li>")
                        else:
                            log.append(f"<li>❌ Lead #{lead_id} ({name}): Não encontrado no Chatwoot (Tentado: {clean_phone})</li>")
                            
                    except Exception as e_discovery:
                         log.append(f"<li>⚠️ Discovery Error Lead #{lead_id}: {str(e_discovery)}</li>")

                # If we have a conversation ID (either existing or just found), snooze it
                if conv_id:
                    # SPECIAL RULES (Manual Overrides)
                    if lead_id == 6624:
                        log.append(f"<li>🛑 Lead #{lead_id} (Conv {conv_id}): <strong>Skipped Snooze</strong> (Rule: Reply/Human check required)</li>")
                        continue
                        
                    if lead_id == 6627:
                         log.append(f"<li>🛑 Lead #{lead_id} (Conv {conv_id}): <strong>Skipped Snooze</strong> (Rule: Label check required)</li>")
                         continue
                        
                    try:
                        url = f"{chatwoot_url}/api/v1/accounts/{chatwoot_account_id}/conversations/{conv_id}/toggle_status"
                        payload = {
                            "status": "snoozed",
                            "snoozed_until": snooze_timestamp
                        }
                        
                        resp = requests.post(url, json=payload, headers=headers, timeout=5)
                        
                        if resp.status_code == 200:
                            success_count += 1
                            log.append(f"<li>✅ Lead #{lead['id']} (Conv {conv_id}): <span style='color:green'>Snoozed until 8am</span></li>")
                        elif resp.status_code == 404:
                             log.append(f"<li>⚠️ Lead #{lead['id']} (Conv {conv_id}): Chatwoot 404 (Not Found)</li>")
                        else:
                            log.append(f"<li>❌ Lead #{lead['id']} (Conv {conv_id}): API Error {resp.status_code} - {resp.text}</li>")
                            
                    except Exception as ex:
                        log.append(f"<li>❌ Lead #{lead['id']} (Conv {conv_id}): Exception - {str(ex)}</li>")
                
                # Rate limit safety
                time.sleep(0.1)
                
            log.append("</ul>")
            log.append(f"<h3>Resumo: {success_count}/{len(leads)} processados. {linked_count} novos vínculos.</h3>")
            
        return "".join(log)
    except Exception as e:
        return f"Error: {str(e)}", 500
    finally:
        conn.close()

