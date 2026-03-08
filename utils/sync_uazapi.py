"""
Sincroniza campaign_leads com a API Uazapi (list_messages).
Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover).
"""

import os
import re

from psycopg2.extras import RealDictCursor


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


def _extract_phones_from_message(m):
    """Extrai número normalizado (apenas dígitos) de um item de mensagem."""
    num = m.get("number") or m.get("chatid") or m.get("chatId") or m.get("sender") or ""
    if not num:
        return None
    raw = str(num).split("@")[0]
    clean = re.sub(r"\D", "", raw)
    if len(clean) >= 10:
        return clean
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


def sync_campaign_leads_from_uazapi(conn, campaign_id, token, folder_id, uazapi_service, debug=False):
    """
    Sincroniza status de campaign_leads com list_messages da Uazapi.
    Atualiza status 'sent' e 'failed' no DB conforme retorno da API.
    Itera todas as páginas para capturar todos os envios.
    Retorna dict com {sent: count, failed: count, updated_sent: int, updated_failed: int}.
    """
    if not uazapi_service or not token or not folder_id:
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    debug = os.environ.get("DEBUG_SYNC_UAZAPI") == "1"
    sent_phones = fetch_all_phones_by_status(
        uazapi_service, token, folder_id, "Sent"
    )
    failed_phones = fetch_all_phones_by_status(
        uazapi_service, token, folder_id, "Failed"
    )

    # Normalizar para match bidirecional (API ↔ DB)
    sent_normalized = set()
    for ph in sent_phones:
        sent_normalized |= normalize_phone_for_match(ph)
    failed_normalized = set()
    for ph in failed_phones:
        failed_normalized |= normalize_phone_for_match(ph)

    if debug:
        print(f"[sync_uazapi] campaign_id={campaign_id} folder_id={folder_id} sent_phones={list(sent_phones)[:5]}... failed_phones={list(failed_phones)[:3]}")

    # Match por phone ou whatsapp_link usando normalize_phone_for_match
    updated_sent = 0
    updated_failed = 0
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
        if sent_ids:
            cur.execute(
                """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW())
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

    if debug and (updated_sent > 0 or updated_failed > 0):
        print(f"[sync_uazapi] campaign_id={campaign_id} updated_sent={updated_sent} updated_failed={updated_failed}")

    return {
        "sent": len(sent_phones),
        "failed": len(failed_phones),
        "updated_sent": updated_sent,
        "updated_failed": updated_failed,
    }
