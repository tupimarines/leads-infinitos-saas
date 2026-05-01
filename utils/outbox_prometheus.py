"""
Métricas Prometheus para tentativas da fila ``campaign_message_outbox`` (tech-spec Task 14).

Os contadores incrementam no processo do ``worker_cadence`` (envio real). Para scrape,
definir ``UAZAPI_OUTBOX_METRICS_PORT`` > 0 nesse processo. O app Flask pode expor
``GET /metrics`` com ``EXPOSE_PROMETHEUS_METRICS`` (métricas do processo web; contadores
outbox ficam em zero salvo instrumentação futura no mesmo processo).
"""

from __future__ import annotations

import threading

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, Info, generate_latest

OUTBOX_SEND_ATTEMPTS = Counter(
    "campaign_outbox_send_attempts_total",
    "Tentativas de envio outbox Uazapi concluídas (após escrita em campaign_send_attempts ou falha de persistência).",
    ("outcome",),
)

OUTBOX_SEND_LATENCY_SECONDS = Histogram(
    "campaign_outbox_send_latency_seconds",
    "Latência entre início do envio (ou falha local imediata) e fim da persistência da tentativa.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

_metrics_server_lock = threading.Lock()
_metrics_server_started = False


def _register_alert_threshold_info() -> None:
    from utils.config import (
        OUTBOX_ALERT_FAILURE_RATE_THRESHOLD,
        OUTBOX_ALERT_MIN_ATTEMPTS_PER_SEC,
    )

    info = Info(
        "campaign_outbox_alert_thresholds",
        "Limiares sugestivos para alertas de taxa de falha (ajustar com ops; ver utils.config).",
    )
    info.info(
        {
            "failure_rate_threshold": str(OUTBOX_ALERT_FAILURE_RATE_THRESHOLD),
            "min_attempts_per_sec": str(OUTBOX_ALERT_MIN_ATTEMPTS_PER_SEC),
        }
    )


_register_alert_threshold_info()


def observe_campaign_outbox_send_attempt(outcome: str, latency_ms: int) -> None:
    label = (outcome or "unknown").strip().lower() or "unknown"
    OUTBOX_SEND_ATTEMPTS.labels(outcome=label).inc()
    OUTBOX_SEND_LATENCY_SECONDS.observe(max(0.0, float(latency_ms) / 1000.0))


def maybe_start_outbox_metrics_http_server() -> None:
    """Inicia ``prometheus_client.start_http_server`` uma vez (porta ``UAZAPI_OUTBOX_METRICS_PORT``)."""
    global _metrics_server_started
    from utils.config import UAZAPI_OUTBOX_METRICS_PORT

    if UAZAPI_OUTBOX_METRICS_PORT <= 0:
        return
    with _metrics_server_lock:
        if _metrics_server_started:
            return
        from prometheus_client import start_http_server

        start_http_server(UAZAPI_OUTBOX_METRICS_PORT)
        _metrics_server_started = True


def flask_metrics_response():
    """Resposta Flask com corpo OpenMetrics/Prometheus."""
    from flask import Response

    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
