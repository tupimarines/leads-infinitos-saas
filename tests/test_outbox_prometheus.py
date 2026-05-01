"""Métricas Prometheus outbox (tech-spec Task 14)."""

from prometheus_client import REGISTRY

from utils import outbox_prometheus as op


def test_observe_increments_attempt_counter():
    before = REGISTRY.get_sample_value(
        "campaign_outbox_send_attempts_total", {"outcome": "sent"}
    )
    before = 0.0 if before is None else float(before)
    op.observe_campaign_outbox_send_attempt("sent", 42)
    after = REGISTRY.get_sample_value(
        "campaign_outbox_send_attempts_total", {"outcome": "sent"}
    )
    assert after == before + 1.0


def test_observe_failed_label():
    before = REGISTRY.get_sample_value(
        "campaign_outbox_send_attempts_total", {"outcome": "failed"}
    )
    before = 0.0 if before is None else float(before)
    op.observe_campaign_outbox_send_attempt("failed", 1)
    after = REGISTRY.get_sample_value(
        "campaign_outbox_send_attempts_total", {"outcome": "failed"}
    )
    assert after == before + 1.0
