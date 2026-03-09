"""
Sincroniza campaign_leads com a API Uazapi (list_messages / list_folders).
Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover).
"""

import json
import os
import re

from psycopg2.extras import RealDictCursor


def _normalize_phone_for_api(phone: str):
    """
    Normaliza número para envio à API Uazapi (POST /chat/check, create_advanced_campaign).
    Extrai dígitos; se 10–11 dígitos sem 55, adiciona 55. Retorna string ou None se inválido.
    """
    if not phone:
        return None
    raw_str = str(phone).split("@")[0]
    clean = re.sub(r"\D", "", raw_str)
    if len(clean) < 10:
        return None
    if 10 <= len(clean) <= 11 and not clean.startswith("55"):
        return "55" + clean
    return clean


def normalize_phone_for_match(raw):
    """
    Normaliza número para match bidirecional (API ↔ DB).
    Extrai dígitos; retorna set de variantes (com/sem 55) para números válidos.
    Usado por sync e rollover.
    """
    if not raw:
        return set()
    raw_str = str(raw).split("@")[0]
    clean = re.sub(r"\D", "", raw_str)
    if len(clean) < 10:
        return set()
    variants = {clean}
    if 10 <= len(clean) <= 11 and not clean.startswith("55"):
        variants.add("55" + clean)
    elif len(clean) >= 12 and clean.startswith("55"):
        variants.add(clean[2:])
    return variants


# Campos conhecidos da API Uazapi para extração de telefone (ordem de prioridade).
_PHONE_FIELDS = (
    "number", "chatid", "chatId", "sender", "senderpn", "jid",
    "recipient", "to", "wa_id", "phoneNumber"
)


def _normalize_phone_value(val):
    """Extrai dígitos de valor (string ou número); remove sufixo @s.whatsapp.net."""
    if val is None:
        return None
    raw = str(val).split("@")[0]  # remotejid: 55xxx@s.whatsapp.net -> 55xxx
    clean = re.sub(r"\D", "", raw)
    return clean if len(clean) >= 10 else None


def _extract_phones_from_message(m):
    """
    Extrai número normalizado (apenas dígitos) de um item de mensagem.
    API Uazapi retorna em formato remotejid (ex: 554137984966@s.whatsapp.net) em chatid, senderpn, jid.
    Ordem: number, chatid, chatId, sender, senderpn, jid, recipient, to, wa_id, phoneNumber.
    Suporta valores aninhados (dict): busca recursivamente por chaves conhecidas.
    """
    if not isinstance(m, dict):
        return None
    for key in _PHONE_FIELDS:
        val = m.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            # Parse recursivo: objeto aninhado pode ter number, chatid, etc.
            ph = _extract_phones_from_message(val)
            if ph:
                return ph
        else:
            ph = _normalize_phone_value(val)
            if ph:
                return ph
    return None


def get_uazapi_campaign_counts(uazapi_service, token, folder_id):
    """
    Retorna contagens reais da Uazapi (Sent, Failed, Scheduled) com paginação completa.
    Usado por stats API e worker para saber quando campanha inicial terminou.
    """
    if not uazapi_service or not token or not folder_id:
        return {"sent": 0, "failed": 0, "scheduled": 0}

    def _count_all_pages(message_status):
        total = 0
        page = 1
        page_size = 500
        while True:
            resp = uazapi_service.list_messages(
                token, folder_id, message_status=message_status, page=page, page_size=page_size
            )
            if not resp:
                break
            msgs = resp.get("messages") or resp.get("data")
            if isinstance(msgs, dict):
                msgs = msgs.get("messages") or msgs.get("data") or []
            if not isinstance(msgs, list):
                msgs = []
            total += len(msgs)
            pag = resp.get("pagination") or {}
            last_page = pag.get("lastPage") or pag.get("last_page") or 1
            if page >= last_page or len(msgs) < page_size:
                break
            page += 1
        return total

    return {
        "sent": _count_all_pages("Sent"),
        "failed": _count_all_pages("Failed"),
        "scheduled": _count_all_pages("Scheduled"),
    }


def is_initial_campaign_finished(counts):
    """True quando não há mais mensagens agendadas (campanha inicial concluída)."""
    return (counts.get("scheduled") or 0) == 0


def fetch_all_phones_by_status(uazapi_service, token, folder_id, message_status):
    """Busca todos os telefones de um status, iterando paginação."""
    phones = set()
    page = 1
    page_size = 500
    while True:
        resp = uazapi_service.list_messages(
            token, folder_id, message_status=message_status, page=page, page_size=page_size
        )
        if not resp:
            break
        msgs = resp.get("messages") or resp.get("data")
        if isinstance(msgs, dict):
            msgs = msgs.get("messages") or msgs.get("data") or []
        if not isinstance(msgs, list):
            msgs = []
        for m in msgs:
            ph = _extract_phones_from_message(m)
            if ph:
                phones.add(ph)
        pag = resp.get("pagination") or {}
        last_page = pag.get("lastPage") or pag.get("last_page") or 1
        if page >= last_page or len(msgs) < page_size:
            break
        page += 1
    return phones


# Alias para compatibilidade (código antigo importava _fetch_all_phones_by_status)
_fetch_all_phones_by_status = fetch_all_phones_by_status


def _sync_folder_via_listfolders(conn, campaign_id, uazapi_service, token, folders_list, folder_id, lead_ids, next_step, cur):
    """
    Se folder encontrado em listfolders com status=done e log_sucess>0, marca lead_ids como sent e
    atualiza current_step para next_step.
    Retorna número de leads atualizados.
    """
    if not folders_list or not isinstance(folders_list, list) or not lead_ids:
        return 0
    folder_info = None
    for f in folders_list:
        fid = f.get("id") or f.get("folder_id") or f.get("folderId")
        if str(fid) == str(folder_id):
            folder_info = f
            break
    if not folder_info:
        return 0
    status = (folder_info.get("status") or "").lower()
    log_success = folder_info.get("log_sucess") or folder_info.get("log_success") or 0
    if status != "done" or log_success <= 0:
        return 0
    ids_to_update = lead_ids[: int(log_success)] if isinstance(lead_ids, list) else []
    if not ids_to_update:
        return 0
    cur.execute(
        """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW()),
           current_step = %s, last_message_sent_at = COALESCE(last_message_sent_at, NOW())
           WHERE id = ANY(%s) AND campaign_id = %s""",
        (next_step, ids_to_update, campaign_id),
    )
    return cur.rowcount


def sync_campaign_leads_from_uazapi(conn, campaign_id, token, folder_id, uazapi_service, debug=False):
    """
    Sincroniza status de campaign_leads com Uazapi.
    Primeiro tenta list_folders (sem status) e usa log_sucess + lead_ids armazenados (F8, F9).
    Fallback: list_messages para Sent/Failed.
    Atualiza current_step conforme etapa (step 1→2, 2→3, 3→4, 4→4).
    """
    if not uazapi_service or not token:
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    debug = os.environ.get("DEBUG_SYNC_UAZAPI") == "1"
    updated_sent = 0
    updated_failed = 0

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config
               FROM campaigns WHERE id = %s""",
            (campaign_id,),
        )
        campaign_row = cur.fetchone()
    lead_ids_by_step = {}
    if campaign_row:
        lid = campaign_row.get("uazapi_last_send_lead_ids")
        if lid:
            try:
                lead_ids_by_step[1] = json.loads(lid) if isinstance(lid, str) else lid
            except Exception:
                pass
        cfg = campaign_row.get("cadence_config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg) if cfg else {}
            except Exception:
                cfg = {}
        for i in (1, 2, 3):
            key = f"rollover_fu{i}_lead_ids"
            ids = cfg.get(key)
            if ids:
                lead_ids_by_step[i + 1] = ids if isinstance(ids, list) else []

    folders_list = uazapi_service.list_folders(token) if folder_id else None

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if folders_list and folder_id:
            lead_ids = lead_ids_by_step.get(1) or (campaign_row and campaign_row.get("uazapi_last_send_lead_ids"))
            if lead_ids and isinstance(lead_ids, str):
                try:
                    lead_ids = json.loads(lead_ids)
                except Exception:
                    lead_ids = []
            n = _sync_folder_via_listfolders(
                conn, campaign_id, uazapi_service, token, folders_list,
                folder_id, lead_ids, 2, cur
            )
            if n > 0:
                updated_sent += n
        cfg = campaign_row.get("cadence_config") if campaign_row else {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg) if cfg else {}
            except Exception:
                cfg = {}
        for step, next_step in [(2, 3), (3, 4), (4, 4)]:
            fid = cfg.get(f"rollover_fu{step-1}_folder_id")
            if fid and folders_list and lead_ids_by_step.get(step):
                n = _sync_folder_via_listfolders(
                    conn, campaign_id, uazapi_service, token, folders_list,
                    fid, lead_ids_by_step[step], next_step, cur
                )
                if n > 0:
                    updated_sent += n

    if not folder_id:
        conn.commit()
        return {"sent": 0, "failed": 0, "updated_sent": updated_sent, "updated_failed": updated_failed}

    sent_phones = fetch_all_phones_by_status(uazapi_service, token, folder_id, "Sent")
    failed_phones = fetch_all_phones_by_status(uazapi_service, token, folder_id, "Failed")

    sent_normalized = set()
    for ph in sent_phones:
        sent_normalized |= normalize_phone_for_match(ph)
    failed_normalized = set()
    for ph in failed_phones:
        failed_normalized |= normalize_phone_for_match(ph)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, phone, whatsapp_link, status FROM campaign_leads WHERE campaign_id = %s""",
            (campaign_id,),
        )
        leads = cur.fetchall()

    sent_ids = []
    failed_ids = []
    for lead in leads:
        lead_variants = normalize_phone_for_match(lead.get("phone")) | normalize_phone_for_match(
            lead.get("whatsapp_link")
        )
        if not lead_variants:
            continue
        if lead.get("status") != "sent" and (lead_variants & sent_normalized):
            sent_ids.append(lead["id"])
        elif lead.get("status") not in ("sent", "failed") and (lead_variants & failed_normalized):
            failed_ids.append(lead["id"])

    with conn.cursor() as cur:
        if sent_ids and updated_sent == 0:
            cur.execute(
                """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW()),
                   current_step = LEAST(4, COALESCE(current_step, 1) + 1)
                   WHERE id = ANY(%s)""",
                (sent_ids,),
            )
            updated_sent = cur.rowcount
        if failed_ids:
            cur.execute(
                """UPDATE campaign_leads SET status = 'failed', sent_at = COALESCE(sent_at, NOW())
                   WHERE id = ANY(%s)""",
                (failed_ids,),
            )
            updated_failed = cur.rowcount
    conn.commit()

    return {
        "sent": len(sent_phones),
        "failed": len(failed_phones),
        "updated_sent": updated_sent,
        "updated_failed": updated_failed,
    }
