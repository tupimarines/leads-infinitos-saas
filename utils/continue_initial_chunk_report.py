"""
AC13: relatório por send_id/instance_id após materialização do continue-initial-chunk.

Função pura (sem I/O) para testes; ``app`` faz o SELECT e chama ``summarize_initial_chunk_materialization_rows``.
"""

from __future__ import annotations

from typing import Any, List, Tuple


def classify_initial_chunk_send_row(row: dict) -> str:
    st = (row.get("status") or "").lower()
    fid = row.get("uazapi_folder_id")
    has_folder = fid is not None and str(fid).strip() != ""
    if st == "failed":
        return "failed"
    if has_folder or st in ("running", "partial", "queued"):
        return "materialized"
    if st == "scheduled":
        return "scheduled_pending_worker"
    return "other"


def summarize_initial_chunk_materialization_rows(
    rows: List[dict[str, Any]],
) -> Tuple[List[dict[str, Any]], bool, bool]:
    """
    Args:
        rows: lista de dicts com id, instance_id, status, uazapi_folder_id (como o SELECT da BD).

    Returns:
        (per_send, partial, all_failed)
    """
    per_send = []
    kinds = set()
    for r in rows:
        oc = classify_initial_chunk_send_row(r)
        kinds.add(oc)
        fid = r.get("uazapi_folder_id")
        per_send.append(
            {
                "send_id": r.get("id"),
                "instance_id": r.get("instance_id"),
                "status": r.get("status"),
                "uazapi_folder_id": str(fid) if fid is not None else None,
                "outcome": oc,
            }
        )

    relevant = kinds & {"materialized", "failed", "scheduled_pending_worker"}
    partial = len(relevant) > 1
    all_failed = bool(per_send) and all(p["outcome"] == "failed" for p in per_send)
    return per_send, partial, all_failed
