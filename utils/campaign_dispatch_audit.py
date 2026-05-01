"""
Auditoria append-only de disparos Uazapi (outbox) por campanha — JSON Lines.

Um arquivo ``dispatch_audit.jsonl`` por campanha sob ``storage/{user_id}/campaigns/{campaign_id}/``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping


def _storage_root() -> str:
    """Mesmo padrão que ``app.py`` / ``STORAGE_DIR`` env."""
    root = os.environ.get("STORAGE_DIR", "storage")
    return root if os.path.isabs(root) else os.path.abspath(root)


def dispatch_audit_jsonl_path(
    user_id: int, campaign_id: int, *, ensure_parent: bool = True
) -> str:
    base = os.path.join(_storage_root(), str(int(user_id)), "campaigns", str(int(campaign_id)))
    if ensure_parent:
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, "dispatch_audit.jsonl")


def append_dispatch_audit_event(
    *,
    user_id: int,
    campaign_id: int,
    event: Mapping[str, Any],
) -> None:
    path = dispatch_audit_jsonl_path(user_id, campaign_id)
    row = dict(event)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = json.dumps(row, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
