"""
Classificação de falhas de envio outbox (Uazapi) para política em _persist_outcome.

Matriz (resumo — primeira regra que aplicar ganha após texto unificado):
| Sinal | Classificação |
|-------|----------------|
| Marcadores internos ``missing_phone``, ``missing_message_template`` | terminal |
| Corpo/JSON com ``no session``, ``no_session``, ``desconect``, ``disconnected``, ``sessão`` (whatsapp), ``not logged`` | instance_unreachable |
| HTTP 502, 503, 504 | retry_backoff |
| HTTP 408, 429 | retry_backoff |
| HTTP 5xx restantes (sem palavras de sessão acima) | retry_backoff |
| HTTP 4xx (exceto 408/429) | terminal |
| ``result_json`` estilo create_advanced (``uazapi_request_failed``) | mesma lógica que corpo + http |
| Excepção: ``TimeoutError`` / subclasses de ``requests.exceptions.Timeout`` | retry_backoff |
| Excepção: ``ConnectionError``, ``BrokenPipeError``, ``requests.exceptions.ConnectionError`` | instance_unreachable |
| HTTP ``None`` sem texto útil | instance_unreachable |
| HTTP ``None`` com texto sem código 4xx/5xx | retry_backoff |
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

OutboxFailureKind = Literal["terminal", "retry_backoff", "instance_unreachable"]

_INTERNAL_TERMINAL_MARKERS = frozenset({"missing_phone", "missing_message_template"})

_INSTANCE_UNREACHABLE_SUBSTRINGS = (
    "no session",
    "no_session",
    "desconect",
    "disconnected",
    "not logged",
    "sem sessão",
    "sessão inválida",
    "sessao invalida",
    "connection closed",
    "instance not connected",
    "whatsapp not connected",
    "closed session",
)


def _lower_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).lower()


def _json_loads_if_str(body: Any) -> Any:
    if not isinstance(body, str) or not body.strip():
        return body
    try:
        return json.loads(body)
    except Exception:
        return body


def _blob_from_result_json(result_json: Any) -> str:
    if not isinstance(result_json, dict):
        return ""
    if result_json.get("uazapi_request_failed"):
        parts: list[str] = []
        eb = result_json.get("error_body")
        if isinstance(eb, dict):
            parts.append(
                _lower_str(
                    eb.get("error")
                    or eb.get("message")
                    or eb.get("Message")
                )
            )
        elif isinstance(eb, str):
            parts.append(_lower_str(eb))
        parts.append(_lower_str(result_json.get("exception")))
        return " ".join(p for p in parts if p)
    return _lower_str(
        result_json.get("error")
        or result_json.get("message")
        or result_json.get("Message")
    )


def _unified_search_text(
    http_status: Optional[int],
    response_body: Any,
    result_json: Any,
) -> str:
    chunks: list[str] = []
    if isinstance(response_body, str):
        chunks.append(_lower_str(response_body))
        parsed = _json_loads_if_str(response_body)
        if isinstance(parsed, dict):
            chunks.append(_lower_str(parsed.get("error") or parsed.get("message")))
    elif isinstance(response_body, dict):
        chunks.append(
            _lower_str(
                response_body.get("error")
                or response_body.get("message")
                or response_body.get("Message")
            )
        )
    chunks.append(_blob_from_result_json(result_json))
    if http_status is not None:
        chunks.append(f"http{http_status}")
    return " ".join(c for c in chunks if c)


def _has_instance_unreachable_keywords(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in _INSTANCE_UNREACHABLE_SUBSTRINGS)


def _exception_suggests_timeout(exc_cls: Optional[type]) -> bool:
    if exc_cls is None or not isinstance(exc_cls, type):
        return False
    if issubclass(exc_cls, TimeoutError):
        return True
    try:
        import requests

        return issubclass(exc_cls, requests.exceptions.Timeout)
    except TypeError:
        return False
    except ImportError:
        return False


def _exception_suggests_unreachable_host(exc_cls: Optional[type]) -> bool:
    if exc_cls is None or not isinstance(exc_cls, type):
        return False
    if issubclass(exc_cls, ConnectionError):
        return True
    if issubclass(exc_cls, BrokenPipeError):
        return True
    try:
        import requests

        return issubclass(exc_cls, requests.exceptions.ConnectionError)
    except TypeError:
        return False
    except ImportError:
        return False


def classify_outbox_send_failure(
    http_status: Optional[int],
    response_body: Any,
    result_json: Any,
    exception_class: Optional[type] = None,
) -> OutboxFailureKind:
    """
    Classifica uma falha de envio outbox para decisão de terminal vs retry vs instância.

    ``result_json``: resposta da API em sucesso; em falhas futuras pode vir dict com
    ``uazapi_request_failed`` (paridade com ``create_advanced_campaign``).
    ``exception_class``: tipo capturado no ``except`` (ex.: ``requests.ReadTimeout``).
    """
    if isinstance(response_body, str) and response_body in _INTERNAL_TERMINAL_MARKERS:
        return "terminal"

    if _exception_suggests_timeout(exception_class):
        return "retry_backoff"
    if _exception_suggests_unreachable_host(exception_class):
        return "instance_unreachable"

    if isinstance(result_json, dict) and result_json.get("uazapi_request_failed"):
        code = result_json.get("http_status")
        if code is not None:
            try:
                http_status = int(code)
            except (TypeError, ValueError):
                pass
        eb = result_json.get("error_body")
        if isinstance(eb, dict):
            response_body = eb
        elif eb is not None and response_body is None:
            response_body = eb

    text = _unified_search_text(http_status, response_body, result_json)
    if _has_instance_unreachable_keywords(text):
        return "instance_unreachable"

    if http_status is not None:
        if http_status in (502, 503, 504):
            return "retry_backoff"
        if http_status in (408, 429):
            return "retry_backoff"
        if 500 <= http_status <= 599:
            return "retry_backoff"
        if 400 <= http_status <= 499:
            return "terminal"

    if http_status is None and not text.strip():
        return "instance_unreachable"

    return "retry_backoff"
