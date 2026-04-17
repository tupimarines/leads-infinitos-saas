"""AC13: relatório por send_id/instance_id (utils puro, sem importar app)."""

from utils.continue_initial_chunk_report import summarize_initial_chunk_materialization_rows


def test_outcomes_all_materialized():
    per_send, partial, all_failed = summarize_initial_chunk_materialization_rows(
        [
            {"id": 1, "instance_id": 10, "status": "running", "uazapi_folder_id": "a"},
            {"id": 2, "instance_id": 20, "status": "running", "uazapi_folder_id": "b"},
        ]
    )
    assert partial is False
    assert all_failed is False
    assert {p["outcome"] for p in per_send} == {"materialized"}


def test_outcomes_partial_materialized_and_failed():
    per_send, partial, all_failed = summarize_initial_chunk_materialization_rows(
        [
            {"id": 1, "instance_id": 10, "status": "running", "uazapi_folder_id": "a"},
            {"id": 2, "instance_id": 20, "status": "failed", "uazapi_folder_id": None},
        ]
    )
    assert partial is True
    assert all_failed is False


def test_outcomes_all_failed():
    per_send, partial, all_failed = summarize_initial_chunk_materialization_rows(
        [
            {"id": 1, "instance_id": 10, "status": "failed", "uazapi_folder_id": None},
            {"id": 2, "instance_id": 20, "status": "failed", "uazapi_folder_id": None},
        ]
    )
    assert partial is False
    assert all_failed is True


def test_outcomes_empty_rows():
    per_send, partial, all_failed = summarize_initial_chunk_materialization_rows([])
    assert per_send == []
    assert partial is False
    assert all_failed is False
