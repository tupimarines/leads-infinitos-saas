#!/usr/bin/env python3
"""
Script para criar licenças ANUAIS para todos os usuários na base de dados.

Comportamento padrão:
- Cria licença anual apenas para usuários que ainda NÃO possuem nenhuma licença.
- Evita duplicidade de license usando hotmart_purchase_id único.

Uso (local ou no container Dokploy):

  # Somente para quem não tem licença (padrão)
  python create_annual_licenses.py --yes

  # Forçar criação para TODOS os usuários (mesmo que já tenham licença)
  python create_annual_licenses.py --yes --force

  # Personalizar dias de expiração (padrão: 365)
  python create_annual_licenses.py --yes --expires-days 365

No Dokploy/Hostinger (exemplo):
  docker exec -w /app a6a79c2d0fff python create_annual_licenses.py --yes
  docker exec -w /app a6a79c2d0fff python create_annual_licenses.py --yes --force
"""

import argparse
import os
import sqlite3
from datetime import datetime, timedelta


def get_db_connection() -> sqlite3.Connection:
    db_path = os.path.join(os.getcwd(), "app.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def user_has_any_license(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM licenses WHERE user_id = ? LIMIT 1",
        (user_id,)
    ).fetchone()
    return bool(row)


def create_annual_license_for_user(conn: sqlite3.Connection, user_id: int, expires_days: int = 365) -> int:
    now = datetime.utcnow()
    expires_at = now + timedelta(days=expires_days)
    purchase_id = f"ANNUAL-{user_id}-{now.strftime('%Y%m%d%H%M%S')}"
    cur = conn.execute(
        """
        INSERT INTO licenses 
        (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            purchase_id,
            '5974664',  # Product ID padrão do projeto
            'anual',
            now.isoformat() + 'Z',
            expires_at.isoformat()
        )
    )
    return cur.lastrowid


def create_annual_licenses(only_missing: bool, expires_days: int) -> tuple[int, int]:
    """Cria licenças anuais para todos os usuários.
    Retorna (num_processed, num_created).
    """
    conn = get_db_connection()
    try:
        users = conn.execute("SELECT id, email FROM users ORDER BY id").fetchall()
        if not users:
            print("❌ Nenhum usuário encontrado.")
            return 0, 0

        num_processed = 0
        num_created = 0
        print(f"👥 Usuários encontrados: {len(users)}\n")

        for user in users:
            num_processed += 1
            uid = user['id']
            email = user['email']

            if only_missing and user_has_any_license(conn, uid):
                print(f"➡️  {email}: já possui licença. Pulando...")
                continue

            try:
                lic_id = create_annual_license_for_user(conn, uid, expires_days=expires_days)
                num_created += 1
                print(f"✅ {email}: licença anual criada (ID {lic_id})")
            except sqlite3.IntegrityError as e:
                # Provável conflito de hotmart_purchase_id (único)
                print(f"⚠️  {email}: não foi possível criar licença (duplicidade). Detalhe: {e}")
            except Exception as e:
                print(f"❌ {email}: erro ao criar licença. Detalhe: {e}")

        conn.commit()
        return num_processed, num_created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria licenças anuais para todos os usuários")
    parser.add_argument("--yes", action="store_true", help="Confirma execução sem prompt")
    parser.add_argument("--force", action="store_true", help="Cria licença mesmo se usuário já tiver alguma")
    parser.add_argument("--expires-days", type=int, default=365, help="Dias até expiração (padrão: 365)")
    args = parser.parse_args()

    db_path = os.path.join(os.getcwd(), "app.db")
    if not os.path.exists(db_path):
        print("❌ Banco de dados não encontrado em ./app.db")
        return 1

    if not args.yes:
        try:
            resp = input("Isto criará licenças anuais. Deseja continuar? (s/N): ").strip().lower()
        except Exception:
            resp = 'n'
        if resp not in ("s", "sim", "y", "yes"):
            print("❌ Operação cancelada.")
            return 1

    only_missing = not args.force
    print("=\n= CRIAÇÃO DE LICENÇAS ANUAIS\n=")
    print(f"Somente quem não tem licença: {only_missing}")
    print(f"Dias até expiração: {args.expires_days}")

    try:
        processed, created = create_annual_licenses(only_missing=only_missing, expires_days=args.expires_days)
        print("\n✅ Concluído!")
        print(f"Usuários processados: {processed}")
        print(f"Licenças criadas: {created}")
        return 0
    except Exception as e:
        print(f"❌ Erro: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


