#!/usr/bin/env python3
"""
Migração do schema (init_db) — executar UMA vez no deploy antes do Gunicorn e dos workers.

Uso:
  python scripts/run_migrate_db.py

Docker Compose: serviço `migrate` com restart: \"no\" e depends_on com
condition: service_completed_successfully nos demais serviços.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def main() -> int:
    print("=== run_migrate_db: iniciando init_db ===")
    try:
        from app import init_db

        init_db()
    except Exception as e:
        print(f"=== run_migrate_db: FALHOU — {e} ===")
        import traceback

        traceback.print_exc()
        return 1
    print("=== run_migrate_db: OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
