"""
Sincroniza campaign_leads com a API Uazapi (list_messages / list_folders / message/find).

- list_folders: **fonte de verdade** para agregados log_success/log_failed na pasta (contadores de campanha).
- **Task 3:** POST /message/find no escopo D4 quando a pasta está ``done``/``partial``/falha
  terminal (``failed``/``error``/``cancelled``) **com** candidatos, ou send ``running`` com
  ``log_success > 0`` e candidatos (reconexão). Paginação: ``UAZAPI_MESSAGE_FIND_LIMIT`` (1–200,
  defeito 100) e ``UAZAPI_MESSAGE_FIND_MAX_PAGES`` (defeito 2). Desligar: ``UAZAPI_MESSAGE_FIND=0``
  (log ``uazapi_message_find_disabled_blocking_reconcile``).
- Promoção **sent** pelos primeiros N de **lead_ids** via **log_sucess** (listfolders) está
  **desligada por defeito** (UAZAPI_LISTFOLDERS_PREFIX_SENT=0). UAZAPI_LISTFOLDERS_PREFIX_SENT=1
  reativa o legado e emite um aviso JSON por send.
- Se a pasta **não** aparece em listfolders mas listmessages não retorna erro: **não** há fallback
  em loop Sent/Failed/Scheduled (reduz carga no servidor); confia no próximo listfolders (~10 min).
- Contagens em campaign_stage_sends são limitadas a planned_count para evitar 10/9 no UI.
- **F11 / D3 / Task 4:** ``UAZAPI_LEAD_RECONCILE_V2=1`` → defeito ``UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0`` (não
  enumerar Sent/Failed em massa em ``needs_reconcile`` nem no fallback legado sem stage_sends). Com find activo e candidatos D4, o mesmo
  ramo fica também suprimido em V2=0 para não duplicar SSOT com ``message_find`` (F10).
- **F7:** o probe ``list_messages(Scheduled, page=1)`` quando a pasta falta em ``listfolders`` **não** é
  controlado por ``UAZAPI_SYNC_RECONCILE_LISTMESSAGES`` (só detecção órfã; não enumeração Sent/Failed).

Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover / Task 6 pré-chunk).

- **Task 5:** ``should_block_initial_rollover_for_pending_find`` — com ``UAZAPI_LEAD_RECONCILE_V2=1``,
  o rollover inicial→FU1 adia ``fu_rollover_done`` enquanto houver candidatos D4 a ``message_find``.
"""

import json
import os
import re
import time

from psycopg2.extras import RealDictCursor


def _normalize_folder_id(value):
    if value is None:
        return None
    return str(value).strip()


def _lead_reconcile_v2_enabled():
    return (os.environ.get("UAZAPI_LEAD_RECONCILE_V2") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _sync_reconcile_listmessages_enabled():
    """
    F11 / Task 4 (núcleo): com ``UAZAPI_LEAD_RECONCILE_V2=1``, defeito **desliga** o reconcile
    Sent/Failed via ``list_messages`` em ``needs_reconcile``. Com V2=0, defeito **liga** (legado)
    se a env não estiver definida.
    """
    v2 = _lead_reconcile_v2_enabled()
    raw = os.environ.get("UAZAPI_SYNC_RECONCILE_LISTMESSAGES")
    if raw is None or str(raw).strip() == "":
        raw = "0" if v2 else "1"
    else:
        raw = str(raw).strip()
    return raw.lower() in ("1", "true", "yes", "on")


def _listfolders_prefix_sent_enabled():
    """
    Legado: _sync_folder_via_listfolders marca os primeiros log_success IDs em
    lead_ids como sent. Task 2 da spec — defeito 0 (desligado).
    F11: com ``UAZAPI_LEAD_RECONCILE_V2=1`` o prefixo fica sempre desligado (rollout único).
    """
    if _lead_reconcile_v2_enabled():
        return False
    v = (os.environ.get("UAZAPI_LISTFOLDERS_PREFIX_SENT") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _lead_step_after_confirmed_send(stage: str) -> int:
    """
    current_step ao marcar envio confirmado (list_folders / list_messages).
    Etapa initial em chunks: mantém step 1 até o rollover da campanha principal;
    evita avançar para FU1 só porque log_success subiu na fila (queued/scheduled).
    """
    if stage == "initial":
        return 1
    return {"follow1": 3, "follow2": 4, "breakup": 4}.get(stage, 4)


def _cadence_stage_sql_guard(stage: str) -> str:
    """
    Pastas mantêm histórico em Sent; o sync não deve rebaixar current_step quando
    o lead já avançou na cadência.

    - initial: só step 1 (Inicial).
    - follow1: só step 2 (FU1) — evita voltar quem já está em FU2+.
    - follow2: só step 3 (FU2).
    - breakup: step 3 ou 4 (transição / já na Despedida).
    """
    s = (stage or "").strip()
    if s == "initial":
        return " AND COALESCE(current_step, 1) = 1 "
    if s == "follow1":
        return " AND COALESCE(current_step, 1) = 2 "
    if s == "follow2":
        return " AND COALESCE(current_step, 1) = 3 "
    if s == "breakup":
        return " AND COALESCE(current_step, 1) IN (3, 4) "
    return ""


def _reconcile_send_by_messages(conn, campaign_id, lead_ids, sent_phones, failed_phones):
    """
    DEPRECATED (Task 4 / D3): reconciliação por enumeração ``list_messages`` Sent/Failed.

    Mantido apenas com ``UAZAPI_SYNC_RECONCILE_LISTMESSAGES=1`` (suporte emergencial). O caminho
    suportado é ``message_find`` no escopo do send (F10). Evita confiar só no agregado quando
    a flag legacy está ligada.
    """
    if not lead_ids:
        return set(), set()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, phone, whatsapp_link
               FROM campaign_leads
               WHERE campaign_id = %s
                 AND id = ANY(%s)""",
            (campaign_id, lead_ids),
        )
        rows = cur.fetchall() or []

    sent_normalized = set()
    for ph in sent_phones or []:
        sent_normalized |= normalize_phone_for_match(ph)
    failed_normalized = set()
    for ph in failed_phones or []:
        failed_normalized |= normalize_phone_for_match(ph)

    sent_ids = set()
    failed_ids = set()
    for row in rows:
        variants = normalize_phone_for_match(row.get("phone")) | normalize_phone_for_match(row.get("whatsapp_link"))
        if not variants:
            continue
        if variants & sent_normalized:
            sent_ids.add(row["id"])
            continue
        if variants & failed_normalized:
            failed_ids.add(row["id"])
    return sent_ids, failed_ids


def _reconcile_stage_by_messages(conn, campaign_id, stage, sent_phones, failed_phones):
    """
    DEPRECATED (Task 4 / D3): mesmo que ``_reconcile_send_by_messages``, por etapa quando
    ``lead_ids`` do send não é fiável — só atrás de ``UAZAPI_SYNC_RECONCILE_LISTMESSAGES=1``.
    """
    stage_to_current_step = {"initial": 1, "follow1": 2, "follow2": 3, "breakup": 4}
    current_step = stage_to_current_step.get(stage)
    if not current_step:
        return set(), set()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, phone, whatsapp_link
               FROM campaign_leads
               WHERE campaign_id = %s
                 AND current_step = %s
                 AND COALESCE(removed_from_funnel, FALSE) = FALSE
                 AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
            (campaign_id, current_step),
        )
        rows = cur.fetchall() or []

    sent_normalized = set()
    for ph in sent_phones or []:
        sent_normalized |= normalize_phone_for_match(ph)
    failed_normalized = set()
    for ph in failed_phones or []:
        failed_normalized |= normalize_phone_for_match(ph)

    sent_ids = set()
    failed_ids = set()
    for row in rows:
        variants = normalize_phone_for_match(row.get("phone")) | normalize_phone_for_match(row.get("whatsapp_link"))
        if not variants:
            continue
        if variants & sent_normalized:
            sent_ids.add(row["id"])
            continue
        if variants & failed_normalized:
            failed_ids.add(row["id"])
    return sent_ids, failed_ids


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


def get_uazapi_campaign_counts(uazapi_service, token, folder_id, context=None):
    """
    Retorna contagens reais da Uazapi (Sent, Failed, Scheduled) com paginação completa.
    Usado por stats API e worker para saber quando campanha inicial terminou.
    context: dict opcional (campaign_id, instance_id) para logs de erro.
    """
    if not uazapi_service or not token or not folder_id:
        return {"sent": 0, "failed": 0, "scheduled": 0}

    def _count_all_pages(message_status):
        total = 0
        page = 1
        page_size = 500
        while True:
            resp = uazapi_service.list_messages(
                token, folder_id, message_status=message_status, page=page, page_size=page_size, context=context
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


def fetch_all_phones_by_status(uazapi_service, token, folder_id, message_status, context=None):
    """Busca todos os telefones de um status, iterando paginação.
    context: dict opcional (campaign_id, instance_id) para logs de erro."""
    phones = set()
    page = 1
    page_size = 500
    while True:
        resp = uazapi_service.list_messages(
            token, folder_id, message_status=message_status, page=page, page_size=page_size, context=context
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


def _message_matches_folder(m, folder_id):
    """
    Mensagem do disparo avançado (POST /message/find).
    Exemplo real: send_folder_id='r373bae082f0849', fromMe=true, wasSentByApi=true, sendFunction=sendtext.
    Ignora send_folder_id vazio (string vazia não casa com pasta).
    Comparação case-insensitive — API pode devolver id em minúsculo.
    """
    if not isinstance(m, dict) or not folder_id:
        return False
    sf = m.get("send_folder_id") or m.get("sendFolderId")
    if sf is None:
        return False
    s_clean = str(sf).strip()
    if not s_clean:
        return False
    return s_clean.lower() == str(folder_id).strip().lower()


def _lead_ids_needing_message_find(conn, campaign_id, send_row, folder_id):
    """
    Subconjunto de ``send_row['lead_ids']`` que ainda deve entrar em ``message_find`` para
    evidenciar envio nesta pasta (``folder_id``) e etapa do send.

    **Invariantes**

    - Só devolve IDs presentes em ``campaign_stage_sends.lead_ids`` (via ``send_row``).
    - Exclui removidos do funil e cadência ``converted``/``lost``.
    - Aplica o mesmo recorte de etapa que os ``UPDATE`` existentes (``_cadence_stage_sql_guard``).
    - **F3:** lead ``failed`` com ``last_sent_folder_id`` já igual a esta pasta (find negativo
      ou política terminal já “presa” a este folder) **não** volta ao find — evita loop infinito.
      ``failed`` sem este folder (ex.: só agregado) continua candidato.
    - **``sent``:** tratado como já confirmado para esta pasta quando ``last_sent_folder_id``
      casa com ``folder_id`` (case-insensitive). *Nota:* até a Task 2 desligar o prefixo
      ``listfolders``, um falso ``sent`` com pasta gravada parece confirmado; a correção
      principal é parar a promoção por ordem em ``lead_ids``.

    **SQL:** um ``SELECT cl.id`` com os predicados acima; parâmetros
    ``(campaign_id, lead_ids, folder_norm, folder_norm)``.
    """
    fid = _normalize_folder_id(folder_id)
    if not fid or not send_row:
        return []
    raw_leads = send_row.get("lead_ids") or []
    if isinstance(raw_leads, str):
        try:
            raw_leads = json.loads(raw_leads)
        except Exception:
            raw_leads = []
    if not raw_leads:
        return []
    lead_ids = []
    for x in raw_leads:
        try:
            lead_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not lead_ids:
        return []

    stage = (send_row.get("stage") or "").strip()
    stage_guard = _cadence_stage_sql_guard(stage)

    sql = (
        """SELECT cl.id
           FROM campaign_leads cl
           WHERE cl.campaign_id = %s
             AND cl.id = ANY(%s)
             AND COALESCE(cl.removed_from_funnel, FALSE) = FALSE
             AND COALESCE(cl.cadence_status, 'active') NOT IN ('converted', 'lost') """
        + stage_guard
        + """ AND (
                 cl.status = 'pending'
                 OR (
                   cl.status = 'failed'
                   AND NOT (
                     NULLIF(TRIM(cl.last_sent_folder_id::text), '') IS NOT NULL
                     AND LOWER(TRIM(BOTH FROM cl.last_sent_folder_id::text)) = LOWER(%s)
                   )
                 )
                 OR (
                   cl.status = 'sent'
                   AND NOT (
                     NULLIF(TRIM(cl.last_sent_folder_id::text), '') IS NOT NULL
                     AND LOWER(TRIM(BOTH FROM cl.last_sent_folder_id::text)) = LOWER(%s)
                   )
                 )
               )
           ORDER BY cl.id"""
    )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (campaign_id, lead_ids, fid, fid))
        rows = cur.fetchall() or []
    out = []
    for r in rows:
        try:
            out.append(int(r["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def should_block_initial_rollover_for_pending_find(conn, campaign_id, send_row, folder_id):
    """
    Task 5 / D9: com ``UAZAPI_LEAD_RECONCILE_V2=1``, não marcar ``fu_rollover_done`` nem mover
    leads para FU1 enquanto existirem candidatos a ``message_find`` (D4/F3) neste send/pasta.
    Isto evita confiar só em ``succ+fail>=planned`` da API quando ainda há pendentes sem evidência.
    """
    if not _lead_reconcile_v2_enabled():
        return False
    return len(_lead_ids_needing_message_find(conn, campaign_id, send_row, folder_id)) > 0


def _ua_message_find_enabled():
    return os.environ.get("UAZAPI_MESSAGE_FIND", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _message_find_limit_and_max_pages():
    """F2: limite 1–200 por pedido; até N páginas (offset += limit)."""
    try:
        limit = int((os.environ.get("UAZAPI_MESSAGE_FIND_LIMIT") or "100").strip())
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 200))
    try:
        max_pages = int((os.environ.get("UAZAPI_MESSAGE_FIND_MAX_PAGES") or "2").strip())
    except ValueError:
        max_pages = 2
    max_pages = max(1, min(max_pages, 20))
    return limit, max_pages


def _should_run_scope_message_find(status, log_success):
    """
    Task 3: corre find no escopo quando há candidatos D4 e:
    - pasta ``done``/``partial``/estado terminal de falha na API, ou
    - send ``running`` com ``log_success > 0`` (reconexão com entregas já contabilizadas).
    """
    st = (status or "").lower()
    if st in (
        "done",
        "concluído",
        "completed",
        "concluido",
        "inconsistent",
        "partial",
        "failed",
        "error",
        "cancelled",
        "canceled",
    ):
        return True
    if st == "running" and int(log_success or 0) > 0:
        return True
    return False


def reconcile_send_leads_via_message_find_for_scope(
    uazapi_service,
    token,
    folder_id,
    campaign_id,
    conn,
    send_row,
    context=None,
):
    """
    Orquestra ``message_find`` para todos os candidatos deste send/pasta (D4 + F3 via SQL).

    Emite uma linha JSON com ``message_find_pages_used`` (F2) e contagens de escopo.

    Returns:
        ``(sent_ids, failed_ids, message_find_pages_used)``
    """
    fid = _normalize_folder_id(folder_id)
    if not send_row or not fid:
        return set(), set(), 0
    scope = _lead_ids_needing_message_find(conn, campaign_id, send_row, fid)
    scope_count = len(scope)
    send_id = send_row.get("id")
    if scope_count == 0:
        return set(), set(), 0

    if not _ua_message_find_enabled():
        print(
            json.dumps(
                {
                    "event": "uazapi_message_find_disabled_blocking_reconcile",
                    "campaign_id": campaign_id,
                    "send_id": send_id,
                    "folder_id": fid,
                    "find_scope_count": scope_count,
                    "find_positive_count": 0,
                    "find_negative_count": scope_count,
                    "message_find_pages_used": 0,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return set(), set(), 0

    sent_ids, failed_ids, pages_used = reconcile_leads_via_message_find(
        uazapi_service,
        token,
        fid,
        campaign_id,
        None,
        conn,
        context=context,
        send_row=send_row,
    )
    pos = len(sent_ids)
    print(
        json.dumps(
            {
                "event": "uazapi_reconcile_message_find_scope",
                "campaign_id": campaign_id,
                "send_id": send_id,
                "folder_id": fid,
                "find_scope_count": scope_count,
                "find_positive_count": pos,
                "find_negative_count": max(0, scope_count - pos),
                "message_find_pages_used": int(pages_used),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return sent_ids, failed_ids, pages_used


def reconcile_leads_via_message_find(
    uazapi_service,
    token,
    folder_id,
    campaign_id,
    lead_ids,
    conn,
    context=None,
    send_row=None,
):
    """
    Para cada lead do chunk, POST /message/find com chatid=55...@s.whatsapp.net
    e confirma envio se existir mensagem com send_folder_id igual ao folder da campanha.
    Retorna ``(sent_ids, failed_ids, message_find_pages_used)``. ``failed_ids`` fica vazio (reservado).

    Com ``send_row`` (linha ``campaign_stage_sends``), o escopo vem de
    ``_lead_ids_needing_message_find`` (candidatos D4 + partição F3). Sem ``send_row``,
    usa ``lead_ids`` como lista explícita (compatível com chamadas antigas).

    **F8:** se ``phone`` e ``whatsapp_link`` normalizam para dígitos distintos, tenta-se
    até dois chatids por lead no mesmo ciclo (para de encontrar match).

    **F2:** paginação ``limit``/``offset`` até ``UAZAPI_MESSAGE_FIND_MAX_PAGES`` páginas.
    """
    if not uazapi_service or not token or not folder_id:
        return set(), set(), 0
    if send_row is not None:
        lead_ids = _lead_ids_needing_message_find(conn, campaign_id, send_row, folder_id)
    elif isinstance(lead_ids, str):
        try:
            lead_ids = json.loads(lead_ids)
        except Exception:
            lead_ids = []
    if not lead_ids:
        return set(), set(), 0

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, phone, whatsapp_link
               FROM campaign_leads
               WHERE campaign_id = %s AND id = ANY(%s)""",
            (campaign_id, list(lead_ids)),
        )
        rows = cur.fetchall() or []

    sleep_s = float(os.environ.get("UAZAPI_MESSAGE_FIND_SLEEP_SEC", "0.05"))
    limit, max_pages = _message_find_limit_and_max_pages()
    sent_ids = set()
    pages_used = 0
    for row in rows:
        phones_to_try = []
        p_phone = _normalize_phone_for_api(row.get("phone") or "")
        p_link = _normalize_phone_for_api(row.get("whatsapp_link") or "")
        if p_phone:
            phones_to_try.append(p_phone)
        if p_link and p_link != p_phone:
            phones_to_try.append(p_link)
        if not phones_to_try and p_link:
            phones_to_try.append(p_link)
        if not phones_to_try:
            continue
        lead_matched = False
        for phone in phones_to_try:
            chatid = f"{phone}@s.whatsapp.net"
            for page_idx in range(max_pages):
                offset = page_idx * limit
                resp = uazapi_service.message_find(
                    token, chatid, limit=limit, offset=offset, context=context
                )
                pages_used += 1
                msgs = []
                if resp:
                    msgs = resp.get("messages") or []
                if any(_message_matches_folder(m, folder_id) for m in msgs):
                    sent_ids.add(int(row["id"]))
                    lead_matched = True
                    break
                if not isinstance(msgs, list) or len(msgs) < limit:
                    break
                if sleep_s > 0:
                    time.sleep(sleep_s)
            if lead_matched:
                break
            if sleep_s > 0:
                time.sleep(sleep_s)

    return sent_ids, set(), pages_used


def _sync_folder_via_listfolders(
    conn,
    campaign_id,
    uazapi_service,
    token,
    folders_list,
    folder_id,
    lead_ids,
    next_step,
    cur,
    stage_label=None,
    instance_id=None,
    instance_remote_jid=None,
):
    """
    Usa list_folders (log_sucess) como fonte de verdade — list_messages retorna só a 1ª msg do batch.
    Marca os primeiros log_success leads como sent. Funciona para status done, scheduled, sending, running.
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
    log_success = int(folder_info.get("log_sucess") or folder_info.get("log_success") or 0)
    if log_success <= 0:
        return 0
    if not isinstance(lead_ids, list) or not lead_ids:
        return 0
    n_take = min(int(log_success), len(lead_ids))
    ids_to_update = lead_ids[:n_take]
    if not ids_to_update:
        return 0
    step_guard = _cadence_stage_sql_guard(stage_label or "")
    cur.execute(
        """UPDATE campaign_leads
           SET status = 'sent',
               sent_at = NOW(),
               current_step = %s,
               last_message_sent_at = NOW(),
               last_sent_stage = COALESCE(%s, last_sent_stage),
               last_sent_instance_id = COALESCE(%s, last_sent_instance_id),
               last_sent_instance_remote_jid = COALESCE(%s, last_sent_instance_remote_jid),
               last_sent_folder_id = COALESCE(%s, last_sent_folder_id)
           WHERE id = ANY(%s)
             AND campaign_id = %s
             AND COALESCE(removed_from_funnel, FALSE) = FALSE
             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')"""
        + step_guard,
        (
            next_step,
            stage_label,
            instance_id,
            instance_remote_jid,
            folder_id,
            ids_to_update,
            campaign_id,
        ),
    )
    return cur.rowcount


def sync_campaign_stage_sends_before_new_chunk(conn, campaign_id, uazapi_service, stage=None):
    """
    Task 6: antes de novo ``create_advanced_campaign`` ou materialização de chunk na mesma etapa,
    corre ``sync_campaign_leads_from_uazapi`` para sends já materializados (message_find no escopo —
    Task 3), alinhado a ``_materialize_scheduled_stage_sends``, ``schedule_next_initial_chunk`` e
    ``app.py`` (continue-initial / ``_create_stage_campaign`` imediato).

    Se ``stage`` for informado, só dispara quando existir ``campaign_stage_sends`` com pasta nessa
    etapa (evita chamadas HTTP desnecessárias na primeira criação). ``stage=None`` exige qualquer
    send com ``uazapi_folder_id`` na campanha.

    Rollout: ``UAZAPI_RECONCILE_FIND_BEFORE_CHUNK=0`` desliga.
    """
    if not uazapi_service or not campaign_id:
        return
    raw = (os.environ.get("UAZAPI_RECONCILE_FIND_BEFORE_CHUNK") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return
    stage_key = stage if stage else None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM campaign_stage_sends css
                WHERE css.campaign_id = %s
                  AND css.uazapi_folder_id IS NOT NULL
                  AND BTRIM(css.uazapi_folder_id::text) <> ''
                  AND (%s::text IS NULL OR css.stage = %s::text)
            ) AS has_prior
            """,
            (campaign_id, stage_key, stage_key),
        )
        has_prior = (cur.fetchone() or {}).get("has_prior")
    if not has_prior:
        return
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.uazapi_folder_id, i.apikey
            FROM campaigns c
            JOIN campaign_instances ci ON ci.campaign_id = c.id
            JOIN instances i ON i.id = ci.instance_id
            WHERE c.id = %s
              AND COALESCE(c.use_uazapi_sender, FALSE) = TRUE
              AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
              AND i.apikey IS NOT NULL
              AND BTRIM(i.apikey::text) <> ''
            ORDER BY i.id ASC
            LIMIT 1
            """,
            (campaign_id,),
        )
        row = cur.fetchone()
    if not row or not row.get("apikey"):
        return
    try:
        sync_campaign_leads_from_uazapi(
            conn,
            campaign_id,
            (row.get("apikey") or "").strip(),
            row.get("uazapi_folder_id"),
            uazapi_service,
        )
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(
            json.dumps(
                {
                    "event": "uazapi_sync_before_chunk_failed",
                    "campaign_id": campaign_id,
                    "stage": stage_key,
                    "error": str(e),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def sync_campaign_leads_from_uazapi(conn, campaign_id, token, folder_id, uazapi_service, debug=False):
    """
    Sincroniza status de campaign_leads com Uazapi.
    Primeiro tenta list_folders (sem status) e usa log_sucess + lead_ids armazenados (F8, F9).
    Fallback: list_messages para Sent/Failed.
    Atualiza current_step conforme etapa (via campaign_stage_sends e _lead_step_after_confirmed_send).

    O bloco legado (folder único em campaigns + uazapi_last_send_lead_ids e fallback list_messages
    com current_step+1) só roda em campanhas antigas **sem** use_uazapi_sender e **sem** stage_sends.

    O ``token`` da assinatura é obrigatório **só** para esse legado; no fluxo por ``campaign_stage_sends``
    cada send usa ``i.apikey`` (evita sync vazio em ``process_rollover_fu_next`` quando a primeira
    instância da campanha não tem chave mas outra instância com send ativo tem).
    """
    if not uazapi_service:
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    debug = os.environ.get("DEBUG_SYNC_UAZAPI") == "1"
    updated_sent = 0
    updated_failed = 0

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config, enable_cadence,
                      use_uazapi_sender
               FROM campaigns WHERE id = %s""",
            (campaign_id,),
        )
        campaign_row = cur.fetchone() or {}

    # 1) Novo fluxo: sync por campaign_stage_sends (folder por instância)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT css.id, css.stage, css.instance_id, css.instance_remote_jid, css.uazapi_folder_id,
                      css.lead_ids, css.planned_count, css.status,
                      i.apikey
               FROM campaign_stage_sends css
               JOIN instances i ON i.id = css.instance_id
               WHERE css.campaign_id = %s
                 AND css.uazapi_folder_id IS NOT NULL
                 AND css.status IN ('scheduled', 'running', 'partial', 'done')""",
            (campaign_id,),
        )
        stage_sends = cur.fetchall() or []

    uses_modern_path = bool(
        campaign_row.get("enable_cadence")
        or campaign_row.get("use_uazapi_sender")
        or stage_sends
    )
    if not uses_modern_path and not (token or "").strip():
        return {"sent": 0, "failed": 0, "updated_sent": 0, "updated_failed": 0}

    for send in stage_sends:
        send_token = send.get("apikey")
        fid = _normalize_folder_id(send.get("uazapi_folder_id"))
        if not send_token or not fid:
            continue

        stage_guard = _cadence_stage_sql_guard(send.get("stage") or "")

        ctx = {"campaign_id": campaign_id, "instance_id": send.get("instance_id")}
        folders_raw = uazapi_service.list_folders(send_token, context=ctx)
        # None = falha de transporte/HTTP (ver UazapiService.list_folders). Não tratar como
        # "pasta ausente" nem correr probe→failed (Task 7 / AC5 — evita falso órfão).
        if folders_raw is None:
            if debug:
                print(
                    f"ℹ️ [Uazapi Sync] list_folders indisponível; sem SSOT neste ciclo — não inferir órfão. "
                    f"send_id={send.get('id')} folder_id={fid}"
                )
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE campaign_stage_sends
                       SET last_sync_at = NOW(), updated_at = NOW()
                       WHERE id = %s""",
                    (send["id"],),
                )
            continue
        if not isinstance(folders_raw, list):
            if debug:
                print(
                    f"ℹ️ [Uazapi Sync] list_folders retorno inesperado (não-lista); adiar sync. "
                    f"send_id={send.get('id')} folder_id={fid}"
                )
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE campaign_stage_sends
                       SET last_sync_at = NOW(), updated_at = NOW()
                       WHERE id = %s""",
                    (send["id"],),
                )
            continue
        folders_list = folders_raw
        folder_info = None
        for f in folders_list:
            cur_fid = _normalize_folder_id(f.get("id") or f.get("folder_id") or f.get("folderId"))
            if cur_fid == fid:
                folder_info = f
                break
        if not folder_info:
            # Pasta ausente em listfolders: pode ser atraso da API, ou pasta órfã (ex.: API devolveu
            # folder_id no advanced mas removeu da fila sem enviar — esperaríamos queued/scheduled).
            probe = uazapi_service.list_messages(
                send_token,
                fid,
                message_status="Scheduled",
                page=1,
                page_size=1,
                context=ctx,
            )
            if probe is None:
                # Uma linha JSON para agregadores / suporte (Task 4 spec n8n-sync-observabilidade).
                print(
                    json.dumps(
                        {
                            "event": "uazapi_stage_send_orphan_probe_null",
                            "campaign_id": campaign_id,
                            "send_id": send["id"],
                            "instance_id": send.get("instance_id"),
                            "folder_id": fid,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE campaign_stage_sends
                           SET success_count = 0, failed_count = 0, status = 'failed',
                               last_sync_at = NOW(), updated_at = NOW()
                           WHERE id = %s""",
                        (send["id"],),
                    )
                continue

            # Pasta ainda não listada em listfolders mas listmessages não deu 400: não fazer fallback
            # pesado (Sent/Failed/Scheduled em loop). Não é SSOT, sobrecarrega o servidor e o total em
            # listmessages não é fiável. O próximo sync (~10 min) reconsulta listfolders.
            if debug:
                print(
                    f"ℹ️ [Uazapi Sync] folder ausente em listfolders, listmessages OK (não órfã); "
                    f"sem batelada list_messages — SSOT=listfolders. send_id={send.get('id')} folder_id={fid}"
                )
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE campaign_stage_sends
                       SET last_sync_at = NOW(), updated_at = NOW()
                       WHERE id = %s""",
                    (send["id"],),
                )
            continue

        log_success = int(folder_info.get("log_sucess") or folder_info.get("log_success") or 0)
        log_failed = int(folder_info.get("log_failed") or 0)
        status = (folder_info.get("status") or "").lower() or "running"
        lead_ids = send.get("lead_ids") or []
        if isinstance(lead_ids, str):
            try:
                lead_ids = json.loads(lead_ids)
            except Exception:
                lead_ids = []
        planned_count = int(send.get("planned_count") or 0)
        if planned_count <= 0 and lead_ids:
            planned_count = len(lead_ids)

        # Evita poluir logs: status=done sem falhas repete a cada sync (~10 min) por send.
        # DEBUG_SYNC_UAZAPI=1: linha completa sempre. Caso contrário: só estados problemáticos ou done com failed>0.
        _show_sync_line = (
            debug
            or status in ("failed", "error", "cancelled", "canceled", "inconsistent", "partial")
            or (status == "done" and log_failed > 0)
        )
        if _show_sync_line:
            print(
                f"ℹ️ [Uazapi Sync] campaign={campaign_id} send_id={send.get('id')} stage={send.get('stage')} "
                f"folder_id={fid} status={status} success={log_success} failed={log_failed} planned={planned_count}"
            )

        # log_success em queued/scheduled costuma refletir fila aceita, não entrega no WhatsApp.
        skip_listfolders_leads = send.get("stage") == "initial" and status in (
            "queued",
            "scheduled",
        )
        listfolders_prefix_sent = _listfolders_prefix_sent_enabled()
        updated_from_listfolders = 0
        if (
            listfolders_prefix_sent
            and not skip_listfolders_leads
            and log_success > 0
            and lead_ids
        ):
            print(
                json.dumps(
                    {
                        "event": "uazapi_listfolders_prefix_sent_legacy_enabled",
                        "campaign_id": campaign_id,
                        "send_id": send.get("id"),
                        "folder_id": fid,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                n = _sync_folder_via_listfolders(
                    conn=conn,
                    campaign_id=campaign_id,
                    uazapi_service=uazapi_service,
                    token=send_token,
                    folders_list=folders_list,
                    folder_id=fid,
                    lead_ids=lead_ids,
                    next_step=_lead_step_after_confirmed_send(send.get("stage") or ""),
                    cur=cur,
                    stage_label=send.get("stage"),
                    instance_id=send.get("instance_id"),
                    instance_remote_jid=send.get("instance_remote_jid"),
                )
                updated_from_listfolders = n
                updated_sent += n

        reconciled_success = 0
        reconciled_failed = 0
        # Com prefixo desligado (Task 2), updated_from_listfolders fica 0 por desenho — não tratar
        # como "menos updates que log_success" para não forçar list_messages só por comparação falsa.
        needs_reconcile = (
            status == "done"
            and (
                not lead_ids
                or (
                    listfolders_prefix_sent
                    and log_success > 0
                    and updated_from_listfolders < log_success
                )
                or (planned_count > 0 and (log_success + log_failed) < planned_count)
            )
        )

        find_scope_count = 0
        if lead_ids and planned_count > 0 and folder_info and fid:
            find_scope_count = len(
                _lead_ids_needing_message_find(conn, campaign_id, send, fid)
            )
        # Evita list_messages Sent/Failed + message_find no mesmo send (F10 / conflito Task 3).
        skip_listmessages_reconcile = (
            _ua_message_find_enabled()
            and _should_run_scope_message_find(status, log_success)
            and find_scope_count > 0
        )

        # Task 4: ramo needs_reconcile com list_messages — DEPRECATED; exige UAZAPI_SYNC_RECONCILE_LISTMESSAGES=1.
        if (
            needs_reconcile
            and _sync_reconcile_listmessages_enabled()
            and not skip_listmessages_reconcile
        ):
            sent_phones_done = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Sent", context=ctx)
            failed_phones_done = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Failed", context=ctx)
            if lead_ids:
                sent_ids_done, failed_ids_done = _reconcile_send_by_messages(
                    conn=conn,
                    campaign_id=campaign_id,
                    lead_ids=lead_ids,
                    sent_phones=sent_phones_done,
                    failed_phones=failed_phones_done,
                )
                # Se lead_ids do send estiver desalinhado, faz fallback por etapa.
                # Isso evita travar avanço quando folder está done com sucesso na API.
                if not sent_ids_done and not failed_ids_done and (log_success > 0 or log_failed > 0):
                    sent_ids_done, failed_ids_done = _reconcile_stage_by_messages(
                        conn=conn,
                        campaign_id=campaign_id,
                        stage=send.get("stage"),
                        sent_phones=sent_phones_done,
                        failed_phones=failed_phones_done,
                    )
            else:
                sent_ids_done, failed_ids_done = _reconcile_stage_by_messages(
                    conn=conn,
                    campaign_id=campaign_id,
                    stage=send.get("stage"),
                    sent_phones=sent_phones_done,
                    failed_phones=failed_phones_done,
                )
            reconciled_success = len(sent_ids_done)
            reconciled_failed = len(failed_ids_done)

            if sent_ids_done:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE campaign_leads
                           SET status = 'sent',
                               sent_at = NOW(),
                               current_step = %s,
                               last_message_sent_at = NOW(),
                               last_sent_stage = COALESCE(%s, last_sent_stage),
                               last_sent_instance_id = COALESCE(%s, last_sent_instance_id),
                               last_sent_instance_remote_jid = COALESCE(%s, last_sent_instance_remote_jid),
                               last_sent_folder_id = COALESCE(%s, last_sent_folder_id)
                           WHERE id = ANY(%s)
                             AND campaign_id = %s
                             AND COALESCE(removed_from_funnel, FALSE) = FALSE
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')"""
                        + stage_guard,
                        (
                            _lead_step_after_confirmed_send(send.get("stage") or ""),
                            send.get("stage"),
                            send.get("instance_id"),
                            send.get("instance_remote_jid"),
                            fid,
                            list(sent_ids_done),
                            campaign_id,
                        ),
                    )
                    updated_sent += cur.rowcount

            if failed_ids_done:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE campaign_leads
                           SET status = 'failed',
                               sent_at = COALESCE(sent_at, NOW()),
                               last_sent_stage = COALESCE(%s, last_sent_stage),
                               last_sent_instance_id = COALESCE(%s, last_sent_instance_id),
                               last_sent_instance_remote_jid = COALESCE(%s, last_sent_instance_remote_jid),
                               last_sent_folder_id = COALESCE(%s, last_sent_folder_id)
                           WHERE id = ANY(%s)
                             AND campaign_id = %s
                             AND COALESCE(removed_from_funnel, FALSE) = FALSE
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')"""
                        + stage_guard,
                        (
                            send.get("stage"),
                            send.get("instance_id"),
                            send.get("instance_remote_jid"),
                            fid,
                            list(failed_ids_done),
                            campaign_id,
                        ),
                    )
                    updated_failed += cur.rowcount

        # Task 3: /message/find no escopo D4 (done/partial/falha API ou running+log_success>0).
        if (
            lead_ids
            and planned_count > 0
            and folder_info
            and _should_run_scope_message_find(status, log_success)
        ):
            mf_sent_ids, mf_failed_ids, _mf_pages = reconcile_send_leads_via_message_find_for_scope(
                uazapi_service,
                send_token,
                fid,
                campaign_id,
                conn,
                send,
                context=ctx,
            )
            reconciled_success = len(mf_sent_ids)
            reconciled_failed = len(mf_failed_ids) if mf_failed_ids else 0
            if mf_sent_ids:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE campaign_leads
                           SET status = 'sent',
                               sent_at = NOW(),
                               current_step = %s,
                               last_message_sent_at = NOW(),
                               last_sent_stage = COALESCE(%s, last_sent_stage),
                               last_sent_instance_id = COALESCE(%s, last_sent_instance_id),
                               last_sent_instance_remote_jid = COALESCE(%s, last_sent_instance_remote_jid),
                               last_sent_folder_id = COALESCE(%s, last_sent_folder_id)
                           WHERE id = ANY(%s)
                             AND campaign_id = %s
                             AND COALESCE(removed_from_funnel, FALSE) = FALSE
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')"""
                        + stage_guard,
                        (
                            _lead_step_after_confirmed_send(send.get("stage") or ""),
                            send.get("stage"),
                            send.get("instance_id"),
                            send.get("instance_remote_jid"),
                            fid,
                            list(mf_sent_ids),
                            campaign_id,
                        ),
                    )
                    updated_sent += cur.rowcount

        effective_success = max(log_success, reconciled_success)
        effective_failed = max(log_failed, reconciled_failed)
        if planned_count > 0:
            effective_success = min(effective_success, planned_count)
            _tail = planned_count - effective_success
            effective_failed = min(effective_failed, max(0, _tail))

        # list_folders é fonte de verdade: quando status=done, marcar done no DB.
        # Não depende de list_messages (que pode falhar com 401/400 em pastas archived).
        _api_done = status in ("done", "concluído", "completed", "concluido")
        if _api_done:
            normalized_status = "done"
        elif status in ("failed", "error", "cancelled", "canceled") and log_success == 0:
            normalized_status = "failed"
        elif effective_success > 0 or effective_failed > 0:
            normalized_status = "partial"
        else:
            normalized_status = "running"

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE campaign_stage_sends
                   SET success_count = %s,
                       failed_count = %s,
                       status = %s,
                       last_sync_at = NOW(),
                       updated_at = NOW()
                   WHERE id = %s""",
                (effective_success, effective_failed, normalized_status, send["id"]),
            )

    # Campanhas com cadência, Uazapi sender ou qualquer campaign_stage_sends: só sync por stage_sends.
    # Não usar folder único + uazapi_last_send_lead_ids (incompatível com multi-instância / next_step=2 forçado).
    if (
        campaign_row.get("enable_cadence")
        or campaign_row.get("use_uazapi_sender")
        or stage_sends
    ):
        conn.commit()
        return {"sent": 0, "failed": 0, "updated_sent": updated_sent, "updated_failed": updated_failed}

    # 2) Compat legado (campanhas antigas sem use_uazapi_sender e sem campaign_stage_sends)
    lead_ids_by_step = {}
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

    ctx_legacy = {"campaign_id": campaign_id}
    folders_list = uazapi_service.list_folders(token, context=ctx_legacy) if folder_id else None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if _listfolders_prefix_sent_enabled() and folders_list and folder_id and lead_ids_by_step.get(1):
            print(
                json.dumps(
                    {
                        "event": "uazapi_listfolders_prefix_sent_legacy_enabled",
                        "campaign_id": campaign_id,
                        "send_id": None,
                        "folder_id": str(folder_id),
                        "legacy_path": "uazapi_last_send_lead_ids_step1",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            n = _sync_folder_via_listfolders(
                conn, campaign_id, uazapi_service, token, folders_list,
                folder_id, lead_ids_by_step.get(1), 2, cur,
                stage_label="initial",
                instance_id=None,
                instance_remote_jid=None,
            )
            if n > 0:
                updated_sent += n
        for step, next_step in [(2, 3), (3, 4), (4, 4)]:
            fid = cfg.get(f"rollover_fu{step-1}_folder_id")
            if _listfolders_prefix_sent_enabled() and fid and folders_list and lead_ids_by_step.get(step):
                print(
                    json.dumps(
                        {
                            "event": "uazapi_listfolders_prefix_sent_legacy_enabled",
                            "campaign_id": campaign_id,
                            "send_id": None,
                            "folder_id": str(fid),
                            "legacy_path": f"rollover_fu_step{step}",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                n = _sync_folder_via_listfolders(
                    conn, campaign_id, uazapi_service, token, folders_list,
                    fid, lead_ids_by_step[step], next_step, cur,
                    stage_label={2: "follow1", 3: "follow2", 4: "breakup"}.get(step),
                    instance_id=None,
                    instance_remote_jid=None,
                )
                if n > 0:
                    updated_sent += n

    # 3) Fallback final por list_messages (campanhas sem stage_sends) — Task 4 / D3: mesmo gate que needs_reconcile.
    # DEPRECATED: marcar leads via Sent/Failed em massa; com V2=1 o defeito desliga (F11).
    if not folder_id:
        conn.commit()
        return {"sent": 0, "failed": 0, "updated_sent": updated_sent, "updated_failed": updated_failed}
    # Não testar stage_sends aqui: neste ramo já sabemos que está vazio (senão teríamos retornado
    # acima); um ``if not stage_sends`` tornava o fallback list_messages morto para sempre.

    sent_phones = []
    failed_phones = []
    if _sync_reconcile_listmessages_enabled():
        sent_phones = fetch_all_phones_by_status(uazapi_service, token, folder_id, "Sent", context=ctx_legacy)
        failed_phones = fetch_all_phones_by_status(uazapi_service, token, folder_id, "Failed", context=ctx_legacy)

        sent_normalized = set()
        for ph in sent_phones:
            sent_normalized |= normalize_phone_for_match(ph)
        failed_normalized = set()
        for ph in failed_phones:
            failed_normalized |= normalize_phone_for_match(ph)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id, phone, whatsapp_link, status, cadence_status, removed_from_funnel
                   FROM campaign_leads WHERE campaign_id = %s""",
                (campaign_id,),
            )
            leads = cur.fetchall()

        sent_ids = []
        failed_ids = []
        for lead in leads:
            if lead.get("removed_from_funnel"):
                continue
            if (lead.get("cadence_status") or "") in ("converted", "lost"):
                continue
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
                    """UPDATE campaign_leads
                       SET status = 'sent',
                           sent_at = NOW(),
                           current_step = LEAST(4, COALESCE(current_step, 1) + 1),
                           last_message_sent_at = NOW(),
                           last_sent_folder_id = COALESCE(last_sent_folder_id, %s)
                       WHERE id = ANY(%s)""",
                    (folder_id, sent_ids),
                )
                updated_sent = cur.rowcount
            if failed_ids:
                cur.execute(
                    """UPDATE campaign_leads
                       SET status = 'failed', sent_at = COALESCE(sent_at, NOW())
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
