"""
Configurações carregadas de variáveis de ambiente.
Fonte única para valores sensíveis ou que variam por ambiente.
"""

import os


def _parse_super_admin_emails():
    raw = os.environ.get(
        "SUPER_ADMIN_EMAILS",
        "augustogumi@gmail.com,ricardo.ost@gmail.com",
    )
    return tuple(e.strip() for e in raw.split(",") if e.strip())


SUPER_ADMIN_EMAILS = _parse_super_admin_emails()
