"""Garantias de agendamento de chunks initial (UAZAPI) em worker_cadence."""


def test_initial_chunk_active_statuses_excludes_terminal_and_failed():
    """Após sync marcar send órfão como failed, a instância deve poder receber novo chunk."""
    from utils.limits import INITIAL_CHUNK_ACTIVE_SEND_STATUSES

    active = set(INITIAL_CHUNK_ACTIVE_SEND_STATUSES)
    assert active == {"scheduled", "running", "partial"}
    assert "failed" not in active
    assert "done" not in active
