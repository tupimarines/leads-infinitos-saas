#!/usr/bin/env python3
"""
Atualiza o token de autenticação da Hubla no banco de dados (tabela hubla_config).

Uso:
  python update_hubla_token.py --token "<TOKEN_DA_HUBLA>" [--product-id "<ID_PRODUTO>"]

Observação:
- Se não houver linha na tabela hubla_config, será criada uma com os valores informados.
"""

import argparse
import os
import sqlite3


def get_db_connection() -> sqlite3.Connection:
    db_path = os.path.join(os.getcwd(), "app.db")
    if not os.path.exists(db_path):
        raise RuntimeError("Banco de dados ./app.db não encontrado")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_hubla_config(webhook_token: str, product_id: str | None = None) -> None:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id FROM hubla_config LIMIT 1").fetchone()
        if row:
            if product_id:
                conn.execute(
                    "UPDATE hubla_config SET webhook_token = ?, product_id = ?, updated_at = CURRENT_TIMESTAMP",
                    (webhook_token, product_id),
                )
            else:
                conn.execute(
                    "UPDATE hubla_config SET webhook_token = ?, updated_at = CURRENT_TIMESTAMP",
                    (webhook_token,),
                )
        else:
            conn.execute(
                "INSERT INTO hubla_config (webhook_token, product_id, sandbox_mode) VALUES (?, ?, ?)",
                (webhook_token, product_id or "your-hubla-product-id", True),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza token do webhook da Hubla")
    parser.add_argument("--token", required=True, help="Token de autenticação da Hubla")
    parser.add_argument("--product-id", required=False, help="ID do produto da Hubla (opcional)")
    args = parser.parse_args()

    try:
        upsert_hubla_config(args.token.strip(), product_id=(args.product_id.strip() if args.product_id else None))
        print("✅ Token da Hubla atualizado com sucesso!")
        print("Agora os webhooks serão validados com esse token.")
        return 0
    except Exception as e:
        print(f"❌ Erro ao atualizar token da Hubla: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


