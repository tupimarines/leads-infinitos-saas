"""
Classificação de erros de create_advanced_campaign e respostas Uazapi.
Usado para materialize (retry vs waiting_reconnect vs failed).
"""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple


def _lower_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).lower()


def classify_create_advanced_error(
    result: Optional[dict[str, Any]],
) -> Tuple[str, str, Optional[int]]:
    """
    Devolve (category, message, http_status).

    category:
      - no_session: desconexão / sessão inválida (não retentar create até reconectar)
      - transient_http: 502/503/504 (retentar com backoff)
      - client_error: 4xx
      - server_error: 5xx que não seja transient
      - empty_response: resposta vazia / sem folder sem payload de erro
    """
    if not result:
        return "empty_response", "no result", None
    if result.get("uazapi_request_failed"):
        code = result.get("http_status")
        body = result.get("error_body")
        msg = ""
        if isinstance(body, dict):
            msg = _lower_str(
                body.get("error")
                or body.get("message")
                or body.get("Message")
            )
        elif isinstance(body, str):
            msg = _lower_str(body)
        if not msg and result.get("exception"):
            msg = _lower_str(result.get("exception"))
        if "no session" in msg or "no_session" in msg or "desconect" in msg:
            return "no_session", msg or "no session", code
        if code in (502, 503, 504):
            return "transient_http", msg or f"http {code}", code
        if code is not None and 400 <= int(code) < 500:
            return "client_error", msg or f"http {code}", code
        if code is not None and int(code) >= 500:
            return "server_error", msg or f"http {code}", code
        return "transient_http", msg or "request failed", code
    # Success shape without folder
    if not (result.get("folder_id") or result.get("folderId")):
        err = _lower_str(
            result.get("error") or result.get("message") or result.get("Message")
        )
        if "no session" in err:
            return "no_session", err, None
        return "empty_response", err or "missing folder_id", None
    return "ok", "", None


def format_last_error_for_db(result: Optional[dict[str, Any]], category: str) -> str:
    """JSON curto para campaign_stage_sends.last_materialize_error (até ~2k)."""
    if not result:
        return json.dumps(
            {"category": category, "detail": "empty"}, ensure_ascii=False
        )[:2000]
    if result.get("uazapi_request_failed"):
        return json.dumps(
            {
                "category": category,
                "http_status": result.get("http_status"),
                "error_body": result.get("error_body"),
                "exception": (result.get("exception") or "")[:500],
            },
            ensure_ascii=False,
            default=str,
        )[:2000]
    return json.dumps(
        {"category": category, "body": str(result)[:1500]}, ensure_ascii=False
    )[:2000]
