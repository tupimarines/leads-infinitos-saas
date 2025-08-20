#!/usr/bin/env python3
"""
Exclui um usuário por ID ou email, removendo também suas licenças.

Uso:
  python delete_user.py --id 6 --yes
  python delete_user.py --email "user@example.com" --yes

Se omitir --yes, pedirá confirmação interativa (evite em produção).
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


def find_user(conn: sqlite3.Connection, user_id: int | None, email: str | None):
    if user_id is not None:
        return conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if email is not None:
        return conn.execute("SELECT id, email FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return None


def delete_user(conn: sqlite3.Connection, user_id: int) -> tuple[int, int]:
    # Apagar licenças do usuário primeiro (FK manual)
    cur_lic = conn.execute("DELETE FROM licenses WHERE user_id = ?", (user_id,))
    cur_usr = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur_usr.rowcount, cur_lic.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Excluir usuário e suas licenças")
    parser.add_argument("--id", type=int, help="ID do usuário")
    parser.add_argument("--email", help="Email do usuário")
    parser.add_argument("--yes", action="store_true", help="Pula confirmação interativa")
    args = parser.parse_args()

    if not args.id and not args.email:
        print("❌ Informe --id ou --email")
        return 1

    conn = get_db_connection()
    try:
        user = find_user(conn, args.id, args.email)
        if not user:
            print("❌ Usuário não encontrado")
            return 1

        uid, uemail = user["id"], user["email"]
        lic_count_row = conn.execute("SELECT COUNT(*) AS c FROM licenses WHERE user_id = ?", (uid,)).fetchone()
        lic_count = lic_count_row["c"] if lic_count_row else 0
        print(f"Usuário: {uemail} (ID {uid}) | Licenças: {lic_count}")

        if not args.yes:
            try:
                resp = input("Confirmar exclusão? (s/N): ").strip().lower()
            except Exception:
                resp = "n"
            if resp not in ("s", "sim", "y", "yes"):
                print("❌ Operação cancelada")
                return 1

        deleted_users, deleted_licenses = delete_user(conn, uid)
        conn.commit()
        print(f"✅ Excluído: usuários={deleted_users}, licenças={deleted_licenses}")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"❌ Erro: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())


