"""
Smoke (Task 7 tech-spec desconexão): dual-run advanced vs outbox documentado em worker_cadence.
Não importa o worker (evita dotenv / side-effects); valida fonte e funções esperadas.
"""

from pathlib import Path


def test_worker_cadence_dual_run_disconnect_documentation():
    root = Path(__file__).resolve().parents[1]
    src = (root / "worker_cadence.py").read_text(encoding="utf-8")
    assert "Dual-run desconexão Uazapi" in src
    assert "waiting_reconnect" in src and "campaign_stage_sends" in src
    assert "campaign_message_outbox" in src
    assert "pending" in src and "waiting_instance" in src
    assert "get_instance_status_cached" in src or "get_status" in src


def test_process_verify_folder_queue_is_defined():
    root = Path(__file__).resolve().parents[1]
    src = (root / "worker_cadence.py").read_text(encoding="utf-8")
    assert "def _process_verify_folder_queue():" in src
