#!/usr/bin/env python3
"""
Expira licenças starter_trial vencidas e remove instâncias Uazapi.

Executa:
1. Busca licenças starter_trial com expires_at <= NOW() e status='active'
2. Para cada licença: deleta instâncias Uazapi via API e remove do banco
3. Atualiza status da licença para 'expired'

Uso: python scripts/expire_starter_trial.py
Recomendado: rodar via cron diariamente (ex: 0 2 * * * = 2h da manhã).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from utils.expire_starter_trial import expire_starter_trial_licenses


if __name__ == "__main__":
    try:
        expire_starter_trial_licenses()
    except Exception as e:
        print(f"❌ Erro: {e}")
        sys.exit(1)
