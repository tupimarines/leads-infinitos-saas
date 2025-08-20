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
import sqlite3
from datetime import datetime, timedelta
from typing import Tuple
from werkzeug.security import generate_password_hash


def get_db_connection() -> sqlite3.Connection:
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_user(conn: sqlite3.Connection, email: str, password: str, update_password: bool) -> Tuple[int, str]:
    """Garante que o usuário exista. Se existir e update_password=True, atualiza a senha.
    Retorna (user_id, status) onde status ∈ {"created", "updated", "existing"}.
    """
    email_norm = email.strip().lower()
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (email_norm,)
    ).fetchone()

    if existing:
        user_id = existing[0]
        if update_password:
            password_hash = generate_password_hash(password)
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id)
            )
            return user_id, "updated"
        return user_id, "existing"

    password_hash = generate_password_hash(password)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email_norm, password_hash)
    )
    return cur.lastrowid, "created"


def create_license(
    conn: sqlite3.Connection,
    user_id: int,
    purchase_id: str,
    product_id: str,
    license_type: str,
    lifetime: bool,
    purchase_date_iso: str | None = None,
) -> Tuple[int, str, str]:
    """Cria licença para o usuário, se ainda não existir para o mesmo purchase_id.
    Retorna (license_id, license_type, expires_at_iso).
    """
    # Verificar duplicidade por hotmart_purchase_id (único)
    existing = conn.execute(
        "SELECT id FROM licenses WHERE hotmart_purchase_id = ?",
        (purchase_id,)
    ).fetchone()
    if existing:
        return existing[0], license_type, ""

    now = datetime.utcnow()
    purchase_date = purchase_date_iso or (now.isoformat() + "Z")

    if lifetime:
        expires_at = now + timedelta(days=365 * 50)
        # Mantemos license_type coerente com a constraint (anual|semestral)
        license_type_to_use = "anual"
    else:
        if license_type not in ("anual", "semestral"):
            license_type = "anual"
        license_type_to_use = license_type
        expires_at = now + (timedelta(days=365) if license_type_to_use == "anual" else timedelta(days=180))

    cur = conn.execute(
        """
        INSERT INTO licenses 
        (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
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
    return cur.lastrowid, license_type_to_use, expires_at.isoformat()


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

    args = parser.parse_args()

    conn = get_db_connection()
    try:
        print("=\n= CRIAÇÃO/ATUALIZAÇÃO DE USUÁRIO\n=")
        user_id, user_status = ensure_user(conn, args.email, args.password, args.update_password)
        print(f"Usuário: {args.email.strip().lower()}")
        print(f"Status do usuário: {user_status}")

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


