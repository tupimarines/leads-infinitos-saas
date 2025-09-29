from flask import Flask, render_template, request, redirect, url_for, send_file, flash
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
import sqlite3
import os
import secrets
import string
import json
import threading
import time
from datetime import datetime, timedelta
from main import run_scraper, run_scraper_with_progress


def get_db_connection() -> sqlite3.Connection:
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    
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
    
    # Tabela de webhooks da Hubla
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hubla_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hubla_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_token TEXT NOT NULL,
            product_id TEXT NOT NULL,
            sandbox_mode BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    
    # Tabela de reset de senha
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        """
    )
    
    # Tabela de jobs de scraping
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scraping_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            locations TEXT NOT NULL,  -- JSON array of locations
            total_results INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            progress INTEGER DEFAULT 0,  -- 0-100
            current_location TEXT,
            results_path TEXT,  -- Path to results file
            error_message TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
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
    
    # Inserir configuração inicial da Hubla se não existir
    conn.execute(
        """
        INSERT OR IGNORE INTO hubla_config 
        (webhook_token, product_id, sandbox_mode) 
        VALUES (?, ?, ?)
        """,
        ('your-hubla-webhook-token', 'your-hubla-product-id', True)
    )
    
    conn.commit()
    conn.close()


class User(UserMixin):
    def __init__(self, id: int, email: str, password_hash: str):
        self.id = id
        self.email = email
        self.password_hash = password_hash

    @staticmethod
    def get_by_id(user_id: int) -> "User | None":
        conn = get_db_connection()
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
        return None

    @staticmethod
    def get_by_email(email: str) -> "User | None":
        conn = get_db_connection()
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if row:
            return User(row[0], row[1], row[2])
        return None

    @staticmethod
    def create(email: str, password: str) -> "User":
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return User(new_id, email, password_hash)

    def has_active_license(self) -> bool:
        """Verifica se o usuário tem uma licença ativa"""
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT COUNT(*) as count FROM licenses 
            WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
            """,
            (self.id,)
        ).fetchone()
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

    @staticmethod
    def create(user_id: int, hotmart_purchase_id: str, hotmart_product_id: str, 
               license_type: str, purchase_date: str) -> "License":
        # Calcular data de expiração baseada no tipo de licença
        from datetime import datetime, timedelta
        purchase_dt = datetime.fromisoformat(purchase_date.replace('Z', '+00:00'))
        
        if license_type == 'semestral':
            expires_at = purchase_dt + timedelta(days=180)
        else:  # anual
            expires_at = purchase_dt + timedelta(days=365)
        
        conn = get_db_connection()
        cur = conn.execute(
            """
            INSERT INTO licenses 
            (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, hotmart_purchase_id, hotmart_product_id, license_type, 
             purchase_date, expires_at.isoformat())
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        
        return License(new_id, user_id, hotmart_purchase_id, hotmart_product_id, 
                      license_type, 'active', purchase_date, expires_at.isoformat())

    @staticmethod
    def get_by_user_id(user_id: int) -> list["License"]:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM licenses WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        conn.close()
        
        return [License(row['id'], row['user_id'], row['hotmart_purchase_id'], 
                       row['hotmart_product_id'], row['license_type'], row['status'],
                       row['purchase_date'], row['expires_at']) for row in rows]


class HotmartService:
    def __init__(self):
        self.base_url = "https://developers.hotmart.com/payments/api/v1"
        self.config = self._get_config()
    
    def _get_config(self) -> dict:
        """Obtém configuração da Hotmart do banco"""
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM hotmart_config LIMIT 1").fetchone()
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
        Processa webhook da Hotmart
        Retorna True se processado com sucesso
        """
        # TODO: Implementar validação de assinatura do webhook
        # Por enquanto, apenas salva o webhook
        
        # Extrair purchase_id do formato real da Hotmart
        purchase_id = None
        if payload.get('data', {}).get('purchase', {}).get('transaction'):
            purchase_id = payload.get('data', {}).get('purchase', {}).get('transaction')
        
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO hotmart_webhooks (event_type, hotmart_purchase_id, payload)
            VALUES (?, ?, ?)
            """,
            (payload.get('event'), purchase_id, json.dumps(payload))
        )
        conn.commit()
        conn.close()
        
        # Processar evento de venda (Hotmart usa PURCHASE_COMPLETE)
        if payload.get('event') == 'PURCHASE_COMPLETE':
            return self._process_sale_completed(payload.get('data', {}))
        
        return True
    
    def _process_sale_completed(self, sale_data: dict) -> bool:
        """Processa evento de venda completada"""
        try:
            # Extrair dados do formato real da Hotmart
            buyer_email = sale_data.get('buyer', {}).get('email')
            purchase_id = sale_data.get('purchase', {}).get('transaction')
            product_id = str(sale_data.get('product', {}).get('id', ''))
            purchase_date = sale_data.get('purchase', {}).get('approved_date')
            
            # Converter timestamp para ISO string
            if purchase_date:
                from datetime import datetime
                purchase_date = datetime.fromtimestamp(purchase_date / 1000).isoformat()
            
            if not all([buyer_email, purchase_id, product_id, purchase_date]):
                print(f"Dados insuficientes: email={buyer_email}, purchase_id={purchase_id}, product_id={product_id}, date={purchase_date}")
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
                print(f"Licença criada para {buyer_email}: {license_type} - {purchase_id}")
            else:
                # Usuário ainda não se registrou, a licença será criada quando ele se registrar
                print(f"Usuário {buyer_email} não encontrado. Licença será criada no registro.")
                pass
            
            return True
            
        except Exception as e:
            print(f"Erro ao processar venda completada: {e}")
            return False


class HublaService:
    def __init__(self):
        self.config = self._get_config()
    
    def _get_config(self) -> dict:
        """Obtém configuração da Hubla do banco"""
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM hubla_config LIMIT 1").fetchone()
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
        conn.execute(
            """
            INSERT INTO hubla_webhooks (event_type, hubla_purchase_id, payload)
            VALUES (?, ?, ?)
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
        cur = conn.execute(
            """
            INSERT INTO scraping_jobs (user_id, keyword, locations, total_results)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, keyword, json.dumps(locations), total_results)
        )
        job_id = cur.lastrowid
        conn.commit()
        conn.close()
        return job_id
    
    @staticmethod
    def update_status(job_id: int, status: str, progress: int = None, 
                     current_location: str = None, error_message: str = None):
        """Update job status and progress"""
        conn = get_db_connection()
        
        update_fields = ["status = ?"]
        params = [status]
        
        if progress is not None:
            update_fields.append("progress = ?")
            params.append(progress)
        
        if current_location is not None:
            update_fields.append("current_location = ?")
            params.append(current_location)
        
        if error_message is not None:
            update_fields.append("error_message = ?")
            params.append(error_message)
        
        if status == 'running' and 'started_at' not in [f.split(' = ')[0] for f in update_fields]:
            update_fields.append("started_at = ?")
            params.append(datetime.now().isoformat())
        
        if status in ['completed', 'failed']:
            update_fields.append("completed_at = ?")
            params.append(datetime.now().isoformat())
        
        params.append(job_id)
        
        conn.execute(
            f"UPDATE scraping_jobs SET {', '.join(update_fields)} WHERE id = ?",
            params
        )
        conn.commit()
        conn.close()
    
    @staticmethod
    def set_results(job_id: int, results_path: str):
        """Set the results file path for a completed job"""
        conn = get_db_connection()
        conn.execute(
            "UPDATE scraping_jobs SET results_path = ? WHERE id = ?",
            (results_path, job_id)
        )
        conn.commit()
        conn.close()
    
    @staticmethod
    def get_by_id(job_id: int) -> dict:
        """Get job by ID"""
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM scraping_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_user_id(user_id: int, limit: int = 10) -> list:
        """Get recent jobs for a user"""
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT * FROM scraping_jobs 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
            """,
            (user_id, limit)
        ).fetchall()
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


def send_reset_email(email, temp_password):
    """Envia email com senha temporária"""
    try:
        # Para testes locais, apenas mostrar a senha no console
        if app.config['MAIL_USERNAME'] == 'seu-email@gmail.com':
            print(f"\n" + "="*50)
            print(f"📧 EMAIL DE RESET DE SENHA (TESTE LOCAL)")
            print(f"Para: {email}")
            print(f"Senha temporária: {temp_password}")
            print(f"="*50 + "\n")
            return True
        
        msg = Message(
            'Redefinição de Senha - Leads Infinitos',
            recipients=[email]
        )
        msg.html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #4f7cff;">Redefinição de Senha</h2>
            <p>Olá!</p>
            <p>Você solicitou a redefinição de sua senha no Leads Infinitos.</p>
            <p>Sua senha temporária é:</p>
            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <h3 style="margin: 0; color: #333; font-family: monospace;">{temp_password}</h3>
            </div>
            <p><strong>Importante:</strong></p>
            <ul>
                <li>Esta senha é temporária e deve ser alterada após o login</li>
                <li>Use esta senha para fazer login no sistema</li>
                <li>Após o login, você poderá definir uma nova senha</li>
            </ul>
            <p>Se você não solicitou esta redefinição, ignore este email.</p>
            <p>Atenciosamente,<br>Equipe Leads Infinitos</p>
        </div>
        """
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        return False


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
STORAGE_ROOT = os.environ.get("STORAGE_DIR", "storage")

# Configuração do Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'seu-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'sua-senha-de-app')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'seu-email@gmail.com')

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
init_db()


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
    
    # Start job in background
    run_scraping_job_async(job_id)
    
    flash(f"Scraping iniciado! Job ID: {job_id}. Você pode acompanhar o progresso na página de jobs.")
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


@app.route("/webhook/hotmart", methods=["POST"])
def hotmart_webhook():
    """Endpoint para receber webhooks da Hotmart"""
    try:
        payload = request.get_json()
        signature = request.headers.get('X-Hotmart-Signature', '')
        
        hotmart_service = HotmartService()
        success = hotmart_service.process_webhook(payload, signature)
        
        if success:
            return {"status": "success"}, 200
        else:
            return {"status": "error", "message": "Failed to process webhook"}, 400
            
    except Exception as e:
        print(f"Erro no webhook da Hotmart: {e}")
        return {"status": "error", "message": str(e)}, 500


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
        if not user:
            # Em desenvolvimento, criar usuário automaticamente para facilitar testes
            if app.config.get('DEBUG', False):
                try:
                    user = User.create(email, "temp123456")
                    # Criar licença vitalícia para o usuário de teste
                    expires_at = datetime.now() + timedelta(days=365*50)
                    conn = get_db_connection()
                    conn.execute(
                        """
                        INSERT INTO licenses 
                        (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user.id,
                            f"DEV-TEST-{datetime.now().strftime('%Y%m%d')}",
                            '5974664',
                            'anual',
                            datetime.now().isoformat(),
                            expires_at.isoformat()
                        )
                    )
                    conn.commit()
                    conn.close()
                    flash("Usuário criado automaticamente para teste (modo desenvolvimento).")
                except Exception as e:
                    flash("Erro ao criar usuário de teste.")
                    return redirect(url_for("forgot_password"))
            else:
                flash("Email não encontrado em nossa base de dados.")
                return redirect(url_for("forgot_password"))
        
        # Gerar senha temporária
        temp_password = generate_temp_password()
        
        # Atualizar senha do usuário
        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(temp_password), user.id)
        )
        conn.commit()
        conn.close()
        
        # Enviar email
        if send_reset_email(email, temp_password):
            flash("Senha temporária enviada para seu email. Verifique sua caixa de entrada.")
        else:
            flash("Erro ao enviar email. Entre em contato com o suporte.")
        
        return redirect(url_for("login"))
    
    return render_template("forgot_password.html")


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
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), current_user.id)
        )
        conn.commit()
        conn.close()
        
        flash("Senha alterada com sucesso!")
        return redirect(url_for("index"))
    
    return render_template("change_password.html")


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


if __name__ == "__main__":
    # Em produção, use um servidor WSGI (Gunicorn/Waitress). Traefik no Dokploy fará o proxy.
    app.run(host="0.0.0.0", port=8000, debug=True)