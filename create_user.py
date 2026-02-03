#!/usr/bin/env python3
"""
Script genérico para criar/atualizar usuário e opcionalmente criar licença.

Uso (exemplos):

  # Criar usuário simples
  python create_user.py --email "user@example.com" --password "senha123456"

  # Criar usuário e licença anual (purchase_id gerado automaticamente)
  python create_user.py --email "user@example.com" --password "senha123456" --create-license --license-type anual

  # Criar usuário e licença vitalícia (usa tipo 'anual' com expiração em 50 anos)
  python create_user.py --email "user@example.com" --password "senha123456" --create-license --lifetime

  # Forçar atualização da senha caso usuário já exista
  python create_user.py --email "user@example.com" --password "novaSenha" --update-password
"""

import argparse
import os
import psycopg2
from datetime import datetime, timedelta
from typing import Tuple
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )

def ensure_user(conn, email: str, password: str, update_password: bool) -> Tuple[int, str]:
    """Garante que o usuário exista. Se existir e update_password=True, atualiza a senha."""
    email_norm = email.strip().lower()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email_norm,))
        existing = cur.fetchone()

        if existing:
            user_id = existing[0]
            if update_password:
                password_hash = generate_password_hash(password)
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (password_hash, user_id)
                )
                return user_id, "updated"
            return user_id, "existing"

        password_hash = generate_password_hash(password)
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email_norm, password_hash)
        )
        return cur.fetchone()[0], "created"


def create_license(
    conn,
    user_id: int,
    purchase_id: str,
    product_id: str,
    license_type: str,
    lifetime: bool,
    purchase_date_iso: str | None = None,
) -> Tuple[int, str, str]:
    """Cria licença para o usuário, se ainda não existir para o mesmo purchase_id."""
    with conn.cursor() as cur:
        # Verificar duplicidade por hotmart_purchase_id (único)
        cur.execute(
            "SELECT id FROM licenses WHERE hotmart_purchase_id = %s",
            (purchase_id,)
        )
        existing = cur.fetchone()
        if existing:
            return existing[0], license_type, ""

        now = datetime.utcnow()
        purchase_date = purchase_date_iso or (now.isoformat() + "Z")

        if lifetime:
            expires_at = now + timedelta(days=365 * 50)
            license_type_to_use = "anual"
        else:
            if license_type not in ("anual", "semestral"):
                license_type = "anual"
            license_type_to_use = license_type
            expires_at = now + (timedelta(days=365) if license_type_to_use == "anual" else timedelta(days=180))

        cur.execute(
            """
            INSERT INTO licenses 
            (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                user_id,
                purchase_id,
                product_id,
                license_type_to_use,
                purchase_date,
                expires_at.isoformat()
            )
        )
        return cur.fetchone()[0], license_type_to_use, expires_at.isoformat()


def ensure_instance(conn, user_id: int, instance_name: str = None) -> str:
    """Garante que o usuário tenha uma instância (fake ou real)"""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM instances WHERE user_id = %s", (user_id,))
        existing = cur.fetchone()
        
        if existing:
            return f"existing ({existing[0]})"
            
        safe_name = instance_name or f"inst_{user_id}_{datetime.now().strftime('%H%M%S')}"
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in ('-', '_'))
        
        cur.execute(
            "INSERT INTO instances (user_id, name, apikey, status) VALUES (%s, %s, %s, 'disconnected')",
            (user_id, safe_name, safe_name)
        )
        return f"created ({safe_name})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Criar/atualizar usuário e opcionalmente criar licença")
    parser.add_argument("--email", required=True, help="Email do usuário")
    parser.add_argument("--password", required=True, help="Senha do usuário")
    parser.add_argument("--update-password", action="store_true", help="Atualiza a senha se o usuário já existir")
    parser.add_argument("--create-license", action="store_true", help="Cria uma licença para o usuário")
    parser.add_argument("--license-type", choices=["anual", "semestral"], default="anual", help="Tipo de licença (padrão: anual)")
    parser.add_argument("--lifetime", action="store_true", help="Cria licença vitalícia (equivale a anual com expiração em 50 anos)")
    parser.add_argument("--product-id", default="5974664", help="ID do produto (padrão: 5974664)")
    parser.add_argument("--purchase-id", default=None, help="ID da compra; se omitido, será gerado (ex.: MANUAL-YYYYMMDDHHMMSS)")
    parser.add_argument("--create-instance", action="store_true", help="Cria uma instância WhatsApp (disconnected)")
    parser.add_argument("--instance-name", default=None, help="Nome da instância (opcional)")

    args = parser.parse_args()

    conn = get_db_connection()
    try:
        print("=\n= CRIAÇÃO/ATUALIZAÇÃO DE USUÁRIO (PostgreSQL)\n=")
        user_id, user_status = ensure_user(conn, args.email, args.password, args.update_password)
        print(f"Usuário: {args.email.strip().lower()}")
        print(f"Status do usuário: {user_status}")

        if args.create_instance:
            instance_status = ensure_instance(conn, user_id, args.instance_name)
            print(f"Instância WhatsApp: {instance_status}")

        license_info = None
        if args.create_license:
            purchase_id = args.purchase_id or f"MANUAL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            lic_id, lic_type, expires_at = create_license(
                conn=conn,
                user_id=user_id,
                purchase_id=purchase_id,
                product_id=args.product_id,
                license_type=args.license_type,
                lifetime=args.lifetime,
            )
            license_info = (lic_id, lic_type, expires_at, purchase_id)

        conn.commit()

        print("\n✅ Concluído!")
        print(f"ID do usuário: {user_id}")
        print(f"Senha definida: {'(atualizada)' if user_status == 'updated' else '(nova)'}")
        if license_info:
            lic_id, lic_type, expires_at, purchase_id = license_info
            print("Licença criada/existente:")
            print(f"  - ID: {lic_id}")
            print(f"  - Tipo: {lic_type}")
            if expires_at:
                print(f"  - Expira em: {expires_at}")
            print(f"  - Purchase ID: {purchase_id}")

        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erro: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())


