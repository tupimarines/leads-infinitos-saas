"""
cadence_config (JSONB): mapas de folder Uazapi por send_id para não sobrescrever FU1 entre chunks.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def parse_cadence_config(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return {}


def merge_fu1_folder_into_config(cfg: Dict[str, Any], folder_id: str, send_key: str) -> Dict[str, Any]:
    """
    Acrescenta/atualiza rollover_fu1_folders_by_send[send_key] e mantém rollover_fu1_folder_id como último folder (compat UI).
    """
    cfg = parse_cadence_config(cfg)
    m = cfg.get("rollover_fu1_folders_by_send")
    if not isinstance(m, dict):
        m = {}
    m = dict(m)
    m[str(send_key)] = str(folder_id)
    cfg["rollover_fu1_folders_by_send"] = m
    cfg["rollover_fu1_folder_id"] = str(folder_id)
    return cfg


def iter_fu1_folder_ids(cfg: Any) -> List[str]:
    """
    Lista única de folder_ids de FU1: valores do mapa + legado rollover_fu1_folder_id se não repetido.
    """
    cfg = parse_cadence_config(cfg)
    seen = set()
    out: List[str] = []
    m = cfg.get("rollover_fu1_folders_by_send") or {}
    if isinstance(m, dict):
        for v in m.values():
            if v is None:
                continue
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    legacy = cfg.get("rollover_fu1_folder_id")
    if legacy:
        s = str(legacy).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def has_fu1_folder(cfg: Any) -> bool:
    return len(iter_fu1_folder_ids(cfg)) > 0


def merge_fu1_into_campaign_db(conn, campaign_id: int, folder_id: str, send_key: str) -> None:
    """Atualiza cadence_config com novo par send_key → folder (FU1). Usa FOR UPDATE."""
    with conn.cursor() as cur:
        cur.execute("SELECT cadence_config FROM campaigns WHERE id = %s FOR UPDATE", (campaign_id,))
        row = cur.fetchone()
        raw = None
        if row:
            try:
                raw = row["cadence_config"]
            except (KeyError, TypeError, IndexError):
                raw = row[0]
        cfg = merge_fu1_folder_into_config(parse_cadence_config(raw), folder_id, send_key)
        cur.execute(
            "UPDATE campaigns SET cadence_config = %s::jsonb WHERE id = %s",
            (json.dumps(cfg), campaign_id),
        )
