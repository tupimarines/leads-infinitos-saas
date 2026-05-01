"""
Auditoria append-only de disparos Uazapi (outbox) por campanha — JSON Lines.

Um arquivo ``dispatch_audit.jsonl`` por campanha sob ``storage/{user_id}/campaigns/{campaign_id}/``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

# Sanitização de segredos (UAZAPI / HTTP); telefone pode permanecer (política interna).
_SENSITIVE_KEY_FRAGMENTS = (
    "apikey",
    "api_key",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "client_secret",
    "bearer",
    "token",
)


def _max_response_chars() -> int:
    raw = (os.environ.get("DISPATCH_AUDIT_MAX_RESPONSE_CHARS") or "4000").strip()
    try:
        return max(256, int(raw))
    except ValueError:
        return 4000


def _is_sensitive_key(key: str) -> bool:
    k = str(key).lower().replace("-", "_")
    return any(fragment in k for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_dispatch_audit_payload(obj: Any) -> Any:
    """Remove apikey/Authorization-style secrets from nested structures."""
    if isinstance(obj, Mapping):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize_dispatch_audit_payload(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize_dispatch_audit_payload(x) for x in obj]
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("Bearer ") and len(s) > 12:
            return "Bearer [REDACTED]"
        return obj
    return obj


def _truncate_response_field(obj: Any, max_chars: int) -> Any:
    """Trunca corpo de resposta (string ou serialização JSON) ao limite configurável."""
    if obj is None:
        return None
    if isinstance(obj, str):
        if len(obj) <= max_chars:
            return obj
        return obj[: max(0, max_chars - 15)] + "...[truncated]"
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        s = str(obj)
    if len(s) <= max_chars:
        return obj
    return s[: max(0, max_chars - 15)] + "...[truncated]"


def _storage_root() -> str:
    """Mesmo padrão que ``app.py`` / ``STORAGE_DIR`` env."""
    root = os.environ.get("STORAGE_DIR", "storage")
    return root if os.path.isabs(root) else os.path.abspath(root)


def dispatch_audit_jsonl_path(
    user_id: int, campaign_id: int, *, ensure_parent: bool = True
) -> str:
    base = os.path.join(
        _storage_root(), str(int(user_id)), "campaigns", str(int(campaign_id))
    )
    if ensure_parent:
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, "dispatch_audit.jsonl")


def _append_line_with_lock(path: str, line_utf8: bytes) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if sys.platform == "win32":
        import msvcrt

        with open(path, "ab") as f:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                f.seek(0, os.SEEK_END)
                f.write(line_utf8)
                f.flush()
                os.fsync(f.fileno())
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        with open(path, "ab") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line_utf8)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _normalize_event_row(event_dict: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(event_dict)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    sanitized = sanitize_dispatch_audit_payload(row)
    mc = _max_response_chars()
    if "response" in sanitized:
        sanitized["response"] = _truncate_response_field(sanitized["response"], mc)
    req = sanitized.get("request")
    if isinstance(req, Mapping) and "message_type" not in sanitized:
        kind = req.get("kind")
        if kind in ("text", "media", "none"):
            sanitized["message_type"] = kind
    return sanitized


def append_dispatch_event(
    campaign_id: int,
    user_id: int,
    event_dict: Mapping[str, Any],
) -> None:
    """
    Append uma linha JSONL com sanitização de segredos, truncagem de resposta e lock leve no arquivo.

    Campos esperados pelos chamadores incluem ``attempt_no``, ``outbox_id``, ``request`` (UAZAPI),
    ``response`` (dict ou string), e tipo derivado em ``request.kind`` → ``message_type`` (text|media).
    """
    path = dispatch_audit_jsonl_path(user_id, campaign_id)
    row = _normalize_event_row(event_dict)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    _append_line_with_lock(path, line.encode("utf-8"))


def append_dispatch_audit_event(
    *,
    user_id: int,
    campaign_id: int,
    event: Mapping[str, Any],
) -> None:
    """Alias com kwargs na ordem usada pelo worker (`user_id`, `campaign_id`, `event`)."""
    append_dispatch_event(campaign_id, user_id, event)
