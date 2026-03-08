"""
Sincroniza campaign_leads com a API Uazapi (list_messages).
Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover).
"""

import re


def sync_campaign_leads_from_uazapi(conn, campaign_id, token, folder_id, uazapi_service):
    """
    Sincroniza status de campaign_leads com list_messages da Uazapi.
    Atualiza status 'sent' e 'failed' no DB conforme retorno da API.
    Retorna dict com {sent: count, failed: count, updated_sent: int, updated_failed: int}.
    """
    if not uazapi_service or not token or not folder_id:
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    def _extract_phones_from_messages(resp):
        phones = set()
        if not resp:
            return phones
        msgs = resp.get("messages") or resp.get("data") or []
        for m in msgs if isinstance(msgs, list) else []:
            num = m.get("number") or m.get("chatid") or m.get("chatId") or m.get("sender") or ""
            if num:
                clean = re.sub(r"\D", "", str(num).split("@")[0])
                if len(clean) >= 10:
                    phones.add(clean)
        return phones

    r_sent = uazapi_service.list_messages(
        token, folder_id, message_status="Sent", page=1, page_size=1000
    )
    r_failed = uazapi_service.list_messages(
        token, folder_id, message_status="Failed", page=1, page_size=1000
    )
    sent_phones = _extract_phones_from_messages(r_sent)
    failed_phones = _extract_phones_from_messages(r_failed)

    def _phone_match_params(ph):
        if len(ph) <= 11 and not ph.startswith("55"):
            return (ph, "55" + ph)
        return (ph, ph)

    updated_sent = 0
    updated_failed = 0
    with conn.cursor() as cur:
        for ph in sent_phones:
            p1, p2 = _phone_match_params(ph)
            cur.execute(
                """UPDATE campaign_leads SET status = 'sent', sent_at = COALESCE(sent_at, NOW())
                   WHERE campaign_id = %s AND status != 'sent'
                   AND regexp_replace(phone, '[^0-9]', '', 'g') IN (%s, %s)""",
                (campaign_id, p1, p2),
            )
            updated_sent += cur.rowcount
        for ph in failed_phones:
            p1, p2 = _phone_match_params(ph)
            cur.execute(
                """UPDATE campaign_leads SET status = 'failed', sent_at = COALESCE(sent_at, NOW())
                   WHERE campaign_id = %s AND status NOT IN ('sent', 'failed')
                   AND regexp_replace(phone, '[^0-9]', '', 'g') IN (%s, %s)""",
                (campaign_id, p1, p2),
            )
            updated_failed += cur.rowcount
    conn.commit()

    return {
        "sent": len(sent_phones),
        "failed": len(failed_phones),
        "updated_sent": updated_sent,
        "updated_failed": updated_failed,
    }
