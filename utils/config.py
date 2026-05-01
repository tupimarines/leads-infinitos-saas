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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


# Fila Postgres ``campaign_message_outbox`` (envio unitário Uazapi) em vez de pastas
# ``create_advanced_campaign`` na criação (Task 6) + tick no worker. Default: desligado
# (ambiente: ``USE_MESSAGE_OUTBOX=true`` / ``1`` / ``yes`` / ``on``).
USE_MESSAGE_OUTBOX = _env_bool("USE_MESSAGE_OUTBOX", False)

# ADR-4 / Fase 1 (tech-spec Task 13): o gate da fila outbox na criação e nas APIs admin de
# polling/pausa é **duplo** — (1) ``USE_MESSAGE_OUTBOX`` no ambiente e (2) email do **operador**
# em ``SUPER_ADMIN_EMAILS`` (``is_super_admin``). A coluna ``campaigns.created_by_admin_id``
# identifica quem criou no painel admin **apenas para auditoria**; não substitui nem reforça
# o gate (não usar “created_by_admin_id IS NOT NULL” como condição de outbox).

# --- Task 14 / observabilidade ops (Prometheus) ---
# ``EXPOSE_PROMETHEUS_METRICS``: expõe ``GET /metrics`` no processo Flask (default off).
EXPOSE_PROMETHEUS_METRICS = _env_bool("EXPOSE_PROMETHEUS_METRICS", False)
# Worker ``worker_cadence``: servidor HTTP Prometheus nesta porta (0 = desligado).
# O scrape deve apontar para o **mesmo** processo que incrementa
# ``campaign_outbox_send_attempts_total`` (normalmente o worker, não o Gunicorn web).
UAZAPI_OUTBOX_METRICS_PORT = int(
    (os.environ.get("UAZAPI_OUTBOX_METRICS_PORT") or "0").strip() or "0"
)
# Limiares sugestivos para acordo com ops (documentação + métrica Info em ``utils/outbox_prometheus``).
# Exemplo de alerta (taxa de não-sucesso em 5m, com volume mínimo):
#   sum(rate(campaign_outbox_send_attempts_total{outcome!="sent"}[5m]))
#     / clamp_min(sum(rate(campaign_outbox_send_attempts_total[5m])), 0.001)
#   > OUTBOX_ALERT_FAILURE_RATE_THRESHOLD
# e ``sum(rate(...[5m])) >= OUTBOX_ALERT_MIN_ATTEMPTS_PER_SEC`` para evitar ruído.
OUTBOX_ALERT_FAILURE_RATE_THRESHOLD = float(
    os.environ.get("OUTBOX_ALERT_FAILURE_RATE_THRESHOLD", "0.15")
)
OUTBOX_ALERT_MIN_ATTEMPTS_PER_SEC = float(
    os.environ.get("OUTBOX_ALERT_MIN_ATTEMPTS_PER_SEC", "0.01")
)
