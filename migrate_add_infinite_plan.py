#!/usr/bin/env python3
"""
Migração: Adicionar plano "infinite" e criar licença para superadmin.

- Adiciona 'infinite' ao CHECK de license_type
- Cria licença infinite para augustogumi@gmail.com (50 msg/dia, 10000 extração/mês)

Uso: python migrate_add_infinite_plan.py [--dry-run]
"""

import os
import sys
from datetime import datetime, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'
DRY_RUN = '--dry-run' in sys.argv


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432'),
    )


def main():
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # 1. Alterar CHECK constraint para incluir 'infinite'
        print("1. Alterando CHECK constraint de license_type...")
        cur.execute("""
            DO $$
            DECLARE r RECORD;
            BEGIN
                FOR r IN (
                    SELECT conname FROM pg_constraint c
                    WHERE conrelid = 'public.licenses'::regclass AND contype = 'c'
                    AND pg_get_constraintdef(c.oid) LIKE '%license_type%'
                )
                LOOP
                    EXECUTE 'ALTER TABLE licenses DROP CONSTRAINT ' || quote_ident(r.conname);
                END LOOP;
            END $$;
        """)
        cur.execute("""
            ALTER TABLE licenses
            ADD CONSTRAINT licenses_license_type_check
            CHECK (license_type IN ('starter', 'pro', 'scale', 'semestral', 'anual', 'infinite'));
        """)
        if not DRY_RUN:
            conn.commit()
        print("   ✅ Constraint atualizada")

        # 2. Buscar user_id do superadmin
        cur.execute("SELECT id FROM users WHERE email = %s", (SUPER_ADMIN_EMAIL,))
        row = cur.fetchone()
        if not row:
            print(f"   ⚠️ Usuário {SUPER_ADMIN_EMAIL} não encontrado. Crie o usuário primeiro.")
            return 1
        user_id = row[0]

        # 3. Cancelar licenças ativas existentes do superadmin
        print("2. Cancelando licenças anteriores do superadmin...")
        cur.execute(
            "UPDATE licenses SET status = 'cancelled' WHERE user_id = %s",
            (user_id,)
        )
        if not DRY_RUN:
            conn.commit()
        print("   ✅ Licenças anteriores canceladas")

        # 4. Inserir licença infinite
        print("3. Criando licença infinite...")
        purchase_id = "SUPERADMIN-INFINITE-1"
        product_id = "SUPERADMIN-INFINITE"
        purchase_date = datetime.utcnow()
        expires_at = purchase_date + timedelta(days=365 * 10)  # 10 anos

        cur.execute(
            """
            INSERT INTO licenses
            (user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date, expires_at)
            VALUES (%s, %s, %s, 'infinite', %s, %s)
            """,
            (user_id, purchase_id, product_id, purchase_date, expires_at)
        )
        if not DRY_RUN:
            conn.commit()
        print(f"   ✅ Licença infinite criada para {SUPER_ADMIN_EMAIL}")
        print(f"      - 50 mensagens/dia por instância")
        print(f"      - 10000 leads extração/mês")

    except Exception as e:
        print(f"❌ Erro: {e}")
        conn.rollback()
        return 1
    finally:
        cur.close()
        conn.close()

    if DRY_RUN:
        print("\n⚠️  Modo --dry-run: nenhuma alteração foi persistida.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
