"""
Sincroniza campaign_leads com a API Uazapi (list_messages / list_folders).
Usado por app.py (rota sync-uazapi) e worker_cadence (antes do rollover).
"""

import json
import os
import re

from psycopg2.extras import RealDictCursor


def _normalize_folder_id(value):
    if value is None:
        return None
    return str(value).strip()


def _reconcile_send_by_messages(conn, campaign_id, lead_ids, sent_phones, failed_phones):
    """
    Reconcilia sucesso/falha por lead usando list_messages (Sent/Failed).
    Evita confiar apenas no agregado log_sucess/log_failed quando houver divergência.
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
    Reconcilia por etapa quando lead_ids do send não está disponível/confiável.
    Usa etapa atual do lead como janela de elegibilidade.
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
             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
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

    stage_to_next_step = {"initial": 2, "follow1": 3, "follow2": 4, "breakup": 4}

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT uazapi_folder_id, uazapi_last_send_lead_ids, cadence_config, enable_cadence
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
                 AND css.status IN ('scheduled', 'running', 'partial')""",
            (campaign_id,),
        )
        stage_sends = cur.fetchall() or []

    for send in stage_sends:
        send_token = send.get("apikey")
        fid = _normalize_folder_id(send.get("uazapi_folder_id"))
        if not send_token or not fid:
            continue

        folders_list = uazapi_service.list_folders(send_token) or []
        folder_info = None
        for f in folders_list:
            cur_fid = _normalize_folder_id(f.get("id") or f.get("folder_id") or f.get("folderId"))
            if cur_fid == fid:
                folder_info = f
                break
        if not folder_info:
            # Fallback resiliente: quando list_folders falha/diverge, reconciliar via list_messages.
            if debug:
                print(
                    f"ℹ️ [Uazapi Sync] folder não encontrado em list_folders; usando fallback list_messages "
                    f"(campaign={campaign_id}, send_id={send.get('id')}, stage={send.get('stage')}, folder_id={fid})"
                )

            sent_phones_fb = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Sent")
            failed_phones_fb = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Failed")
            counts_fb = get_uazapi_campaign_counts(uazapi_service, send_token, fid)
            scheduled_fb = int(counts_fb.get("scheduled") or 0)

            lead_ids = send.get("lead_ids") or []
            if isinstance(lead_ids, str):
                try:
                    lead_ids = json.loads(lead_ids)
                except Exception:
                    lead_ids = []

            if lead_ids:
                sent_ids_fb, failed_ids_fb = _reconcile_send_by_messages(
                    conn=conn,
                    campaign_id=campaign_id,
                    lead_ids=lead_ids,
                    sent_phones=sent_phones_fb,
                    failed_phones=failed_phones_fb,
                )
                if not sent_ids_fb and not failed_ids_fb:
                    sent_ids_fb, failed_ids_fb = _reconcile_stage_by_messages(
                        conn=conn,
                        campaign_id=campaign_id,
                        stage=send.get("stage"),
                        sent_phones=sent_phones_fb,
                        failed_phones=failed_phones_fb,
                    )
            else:
                sent_ids_fb, failed_ids_fb = _reconcile_stage_by_messages(
                    conn=conn,
                    campaign_id=campaign_id,
                    stage=send.get("stage"),
                    sent_phones=sent_phones_fb,
                    failed_phones=failed_phones_fb,
                )

            if sent_ids_fb:
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
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
                        (
                            stage_to_next_step.get(send.get("stage"), 4),
                            send.get("stage"),
                            send.get("instance_id"),
                            send.get("instance_remote_jid"),
                            fid,
                            list(sent_ids_fb),
                            campaign_id,
                        ),
                    )
                    updated_sent += cur.rowcount

            if failed_ids_fb:
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
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
                        (
                            send.get("stage"),
                            send.get("instance_id"),
                            send.get("instance_remote_jid"),
                            fid,
                            list(failed_ids_fb),
                            campaign_id,
                        ),
                    )
                    updated_failed += cur.rowcount

            success_fb = len(sent_ids_fb)
            failed_fb = len(failed_ids_fb)
            if scheduled_fb > 0:
                normalized_status_fb = "running"
            elif success_fb > 0 or failed_fb > 0:
                normalized_status_fb = "partial"
            else:
                normalized_status_fb = "running"

            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE campaign_stage_sends
                       SET success_count = %s,
                           failed_count = %s,
                           status = %s,
                           last_sync_at = NOW(),
                           updated_at = NOW()
                       WHERE id = %s""",
                    (success_fb, failed_fb, normalized_status_fb, send["id"]),
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

        if debug or status in ("done", "failed", "error", "cancelled", "canceled", "inconsistent"):
            print(
                f"ℹ️ [Uazapi Sync] campaign={campaign_id} send_id={send.get('id')} stage={send.get('stage')} "
                f"folder_id={fid} status={status} success={log_success} failed={log_failed} planned={planned_count}"
            )

        updated_from_listfolders = 0
        if log_success > 0 and lead_ids:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                n = _sync_folder_via_listfolders(
                    conn=conn,
                    campaign_id=campaign_id,
                    uazapi_service=uazapi_service,
                    token=send_token,
                    folders_list=folders_list,
                    folder_id=fid,
                    lead_ids=lead_ids,
                    next_step=stage_to_next_step.get(send.get("stage"), 4),
                    cur=cur,
                    stage_label=send.get("stage"),
                    instance_id=send.get("instance_id"),
                    instance_remote_jid=send.get("instance_remote_jid"),
                )
                updated_from_listfolders = n
                updated_sent += n

        reconciled_success = 0
        reconciled_failed = 0
        needs_reconcile = (
            status == "done"
            and (
                not lead_ids
                or (log_success > 0 and updated_from_listfolders < log_success)
                or (planned_count > 0 and (log_success + log_failed) < planned_count)
            )
        )
        if needs_reconcile:
            sent_phones_done = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Sent")
            failed_phones_done = fetch_all_phones_by_status(uazapi_service, send_token, fid, "Failed")
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
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
                        (
                            stage_to_next_step.get(send.get("stage"), 4),
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
                             AND COALESCE(cadence_status, 'active') NOT IN ('converted', 'lost')""",
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

        effective_success = max(log_success, reconciled_success)
        effective_failed = max(log_failed, reconciled_failed)
        reconciled_total = effective_success + effective_failed

        # Regra estrita de conclusão: só fecha done quando list_folders confirma status done
        # e sucesso >= planejado para o send da instância.
        if status == "done" and planned_count > 0 and reconciled_total >= planned_count:
            normalized_status = "done"
        elif status in ("failed", "error", "cancelled", "canceled") and log_success == 0:
            normalized_status = "failed"
        elif status == "done" and planned_count > 0 and reconciled_total < planned_count:
            normalized_status = "inconsistent"
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

    # Regra para cadências: sincronizar exclusivamente via campaign_stage_sends.
    # Evita regressão de etapa por fallback legado (folder principal / payload antigo).
    if campaign_row.get("enable_cadence"):
        conn.commit()
        return {"sent": 0, "failed": 0, "updated_sent": updated_sent, "updated_failed": updated_failed}

    # 2) Compat legado (campanhas antigas sem stage_sends)
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

    folders_list = uazapi_service.list_folders(token) if folder_id else None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if folders_list and folder_id and lead_ids_by_step.get(1):
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
            if fid and folders_list and lead_ids_by_step.get(step):
                n = _sync_folder_via_listfolders(
                    conn, campaign_id, uazapi_service, token, folders_list,
                    fid, lead_ids_by_step[step], next_step, cur,
                    stage_label={2: "follow1", 3: "follow2", 4: "breakup"}.get(step),
                    instance_id=None,
                    instance_remote_jid=None,
                )
                if n > 0:
                    updated_sent += n

    # 3) Fallback final por list_messages (status principal)
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
