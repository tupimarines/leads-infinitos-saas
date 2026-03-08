"""
Sincroniza campaign_leads com a API Uazapi (list_messages).
Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover).
"""

import re


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


def _fetch_all_phones_by_status(uazapi_service, token, folder_id, message_status):
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


def sync_campaign_leads_from_uazapi(conn, campaign_id, token, folder_id, uazapi_service):
    """
    Sincroniza status de campaign_leads com list_messages da Uazapi.
    Atualiza status 'sent' e 'failed' no DB conforme retorno da API.
    Itera todas as páginas para capturar todos os envios.
    Retorna dict com {sent: count, failed: count, updated_sent: int, updated_failed: int}.
    """
    if not uazapi_service or not token or not folder_id:
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    sent_phones = _fetch_all_phones_by_status(
        uazapi_service, token, folder_id, "Sent"
    )
    failed_phones = _fetch_all_phones_by_status(
        uazapi_service, token, folder_id, "Failed"
    )

    def _phone_match_params(ph):
        if len(ph) <= 11 and not ph.startswith("55"):
            return (ph, "55" + ph)
        return (ph, ph)

    # Match por phone ou whatsapp_link (extrai dígitos de wa.me/5511999999999 etc)
    def _phone_where():
        return """(
            regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') IN (%s, %s)
            OR regexp_replace(COALESCE(whatsapp_link, ''), '[^0-9]', '', 'g') IN (%s, %s)
        )"""

    updated_sent = 0
    updated_failed = 0
    with conn.cursor() as cur:
        for ph in sent_phones:
            p1, p2 = _phone_match_params(ph)
            cur.execute(
                """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW())
                   WHERE campaign_id = %s AND status != 'sent'
                   AND """ + _phone_where(),
                (campaign_id, p1, p2, p1, p2),
            )
            updated_sent += cur.rowcount
        for ph in failed_phones:
            p1, p2 = _phone_match_params(ph)
            cur.execute(
                """UPDATE campaign_leads SET status = 'failed', sent_at = COALESCE(sent_at, NOW())
                   WHERE campaign_id = %s AND status NOT IN ('sent', 'failed')
                   AND """ + _phone_where(),
                (campaign_id, p1, p2, p1, p2),
            )
            updated_failed += cur.rowcount
    conn.commit()

    return {
        "sent": len(sent_phones),
        "failed": len(failed_phones),
        "updated_sent": updated_sent,
        "updated_failed": updated_failed,
    }
