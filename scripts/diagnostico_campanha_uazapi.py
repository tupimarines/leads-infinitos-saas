#!/usr/bin/env python3
"""
Diagnóstico operacional: usuário diz que a campanha "não disparou".

Monta um pacote (DB + API Uazapi) e um texto pronto para colar em um agente/LLM,
com heurísticas de causa provável e medidas sugeridas.

Token da instância: primeira instância Uazapi vinculada em campaign_instances
(mesma regra de _get_uazapi_instance_for_campaign no app).

Uso:
  python scripts/diagnostico_campanha_uazapi.py <campaign_id>
  python scripts/diagnostico_campanha_uazapi.py <campaign_id> --json-out /tmp/diag.json
  python scripts/diagnostico_campanha_uazapi.py <campaign_id> --no-api
  python scripts/diagnostico_campanha_uazapi.py <campaign_id> --print-token   # admin: token completo no stdout

Workflow n8n de referência (I/O da API, teste manual):
  scripts/n8n-workflows/uazapi-diagnostico-campanha-advanced.json
  No n8n (MCP), substitua o header `token` pelos nós HTTP com credencial ou valor
  obtido com --print-token em ambiente seguro.

Requer .env com DB_* ; UAZAPI_URL opcional (default neurix.uazapi.com).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _mask_token(t: Optional[str]) -> str:
    s = (t or "").strip()
    if not s:
        return "[vazio]"
    if len(s) < 14:
        return f"[definido, len={len(s)}]"
    return f"{s[:4]}…{s[-4:]} (len={len(s)})"


def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "devpassword"),
        port=os.environ.get("DB_PORT", "5432"),
        cursor_factory=RealDictCursor,
    )


def _load_db_bundle(cur, campaign_id: int, chunks_limit: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT c.*, u.email AS user_email
        FROM campaigns c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (campaign_id,),
    )
    campaign = cur.fetchone()
    if not campaign:
        return {"error": "campaign_not_found", "campaign_id": campaign_id}

    c = dict(campaign)
    for blob in ("message_template",):
        if isinstance(c.get(blob), str) and len(c[blob] or "") > 2000:
            c[blob] = (c[blob][:2000] + "… [truncado]")

    cur.execute(
        """
        SELECT status, COUNT(*)::int AS n
        FROM campaign_leads
        WHERE campaign_id = %s AND COALESCE(removed_from_funnel, FALSE) = FALSE
        GROUP BY status
        ORDER BY status
        """,
        (campaign_id,),
    )
    lead_status_counts = [dict(r) for r in (cur.fetchall() or [])]

    cur.execute(
        """
        SELECT COUNT(*)::int AS total,
               COUNT(*) FILTER (WHERE status = 'sent')::int AS sent,
               COUNT(*) FILTER (WHERE status = 'pending')::int AS pending
        FROM campaign_leads
        WHERE campaign_id = %s AND COALESCE(removed_from_funnel, FALSE) = FALSE
        """,
        (campaign_id,),
    )
    lead_totals = dict(cur.fetchone() or {})

    cur.execute(
        """
        SELECT i.id AS instance_id, i.name AS instance_name,
               COALESCE(i.api_provider, 'megaapi') AS api_provider,
               i.apikey
        FROM campaign_instances ci
        JOIN instances i ON i.id = ci.instance_id
        WHERE ci.campaign_id = %s
        ORDER BY i.id
        """,
        (campaign_id,),
    )
    instances = [dict(r) for r in (cur.fetchall() or [])]

    cur.execute(
        """
        SELECT id, stage, instance_id, scheduled_for, status, planned_count,
               success_count, failed_count, uazapi_folder_id,
               created_at, updated_at, last_sync_at
        FROM campaign_stage_sends
        WHERE campaign_id = %s
        ORDER BY scheduled_for NULLS LAST, id
        LIMIT %s
        """,
        (campaign_id, chunks_limit),
    )
    stage_sends = []
    for r in cur.fetchall() or []:
        row = dict(r)
        for k, v in list(row.items()):
            if isinstance(v, datetime):
                row[k] = v.isoformat()
        stage_sends.append(row)

    uaz_rows = [i for i in instances if (i.get("api_provider") or "").lower() == "uazapi"]
    primary = None
    for i in uaz_rows:
        if (i.get("apikey") or "").strip():
            primary = {
                "instance_id": i["instance_id"],
                "instance_name": i.get("instance_name"),
                "apikey_masked": _mask_token(i.get("apikey")),
            }
            break

    return {
        "campaign": c,
        "lead_status_counts": lead_status_counts,
        "lead_totals_funnel": lead_totals,
        "instances": [
            {
                **{k: v for k, v in inst.items() if k != "apikey"},
                "apikey_masked": _mask_token(inst.get("apikey")),
            }
            for inst in instances
        ],
        "uazapi_primary_instance": primary,
        "stage_sends": stage_sends,
    }


def _fetch_primary_uazapi_token(cur, campaign_id: int) -> tuple[Optional[str], Optional[int]]:
    cur.execute(
        """
        SELECT i.id AS instance_id, i.apikey
        FROM campaign_instances ci
        JOIN instances i ON i.id = ci.instance_id
        WHERE ci.campaign_id = %s AND COALESCE(i.api_provider, 'megaapi') = 'uazapi'
          AND i.apikey IS NOT NULL AND length(trim(i.apikey)) > 0
        ORDER BY i.id
        LIMIT 1
        """,
        (campaign_id,),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return (row.get("apikey") or "").strip(), row.get("instance_id")


def _folder_ids_from_bundle(bundle: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    camp = bundle.get("campaign") or {}
    fid = camp.get("uazapi_folder_id")
    if fid:
        ids.append(str(fid).strip())
    for row in bundle.get("stage_sends") or []:
        f = row.get("uazapi_folder_id")
        if f:
            s = str(f).strip()
            if s and s not in ids:
                ids.append(s)
    return ids


def _heuristics(bundle: dict[str, Any], api: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Sinais e medidas em PT-BR (pré-LLM)."""
    signals: list[str] = []
    measures: list[str] = []

    camp = bundle.get("campaign") or {}
    sends = bundle.get("stage_sends") or []
    lt = bundle.get("lead_totals_funnel") or {}
    pending = int(lt.get("pending") or 0)
    sent = int(lt.get("sent") or 0)

    if not camp.get("use_uazapi_sender"):
        signals.append("Campanha não está com use_uazapi_sender.")
        measures.append("Confirmar se o disparo deveria ser Uazapi; ajustar configuração da campanha.")

    if not bundle.get("uazapi_primary_instance"):
        signals.append("Nenhuma instância Uazapi com apikey preenchido vinculada à campanha.")
        measures.append("Vincular instância Uazapi à campanha e garantir apikey válido na tabela instances.")

    scheduled_no_folder = [
        s for s in sends if (s.get("status") == "scheduled" and not s.get("uazapi_folder_id"))
    ]
    if scheduled_no_folder:
        signals.append(
            f"Há {len(scheduled_no_folder)} chunk(s) em status 'scheduled' sem uazapi_folder_id "
            "(fila local criada, pasta Uazapi ainda não atribuída ou create_advanced não persistiu)."
        )
        measures.append(
            "Verificar logs do worker_cadence ao materializar o chunk; checar UAZAPI_DEBUG=1. "
            "Reexecutar processamento do envio ou corrigir erro na criação da campanha advanced na API."
        )

    running_like = [s for s in sends if s.get("status") in ("running", "partial", "scheduled", "queued")]
    if pending > 0 and not running_like and camp.get("status") == "running":
        signals.append("Há leads pending mas nenhum stage_send ativo/pendente visível na janela consultada.")
        measures.append(
            "Ampliar LIMIT de chunks ou verificar se chunks antigos foram arquivados; validar enable_cadence e worker."
        )

    if api:
        if api.get("instance_status_error"):
            signals.append(f"Falha ao consultar /instance/status: {api['instance_status_error']}")
        st = api.get("instance_status")
        if st is None and not api.get("instance_status_error"):
            signals.append("GET /instance/status retornou vazio (token inválido ou erro não propagado).")
            measures.append("Validar apikey; repetir o diagnóstico com rede estável.")
        if isinstance(st, dict):
            conn = (st.get("instance") or {}).get("status") or st.get("status")
            if conn and str(conn).lower() != "connected":
                signals.append(f"Instância WhatsApp não está 'connected' (status={conn}).")
                measures.append("Reconectar a instância no painel Uazapi / QR code antes de esperar novos disparos.")

        if api.get("listfolders") is None:
            signals.append("listfolders retornou None (rede, HTTP≠200, 401 token inválido ou corpo tratado como silencioso).")
            measures.append(
                "Conferir apikey na instância; testar GET /sender/listfolders com o mesmo token. "
                "Se 401, atualizar token no cadastro da instância."
            )
        else:
            for chk in api.get("folder_checks") or []:
                fid = chk.get("folder_id")
                if fid and not chk.get("in_listfolders"):
                    signals.append(f"Pasta {fid} não aparece em listfolders (arquivada, outra instância ou removida).")
                    measures.append(
                        "Validar na Uazapi se a pasta existe para este token; se necessário, reassociar campanha "
                        "ou sincronizar estados com sync/admin."
                    )
                counts = chk.get("uazapi_counts")
                if counts and int(counts.get("scheduled") or 0) > 0 and int(chk.get("db_failed_count") or 0) == 0:
                    signals.append(
                        f"Na pasta {fid}, API ainda reporta mensagens Scheduled (>0) — fila Uazapi pode estar aguardando janela."
                    )
                    measures.append(
                        "Aguardar processamento ou revisar delay/agendamento na Uazapi; checar horário scheduled_for do chunk."
                    )

    for s in sends:
        if int(s.get("failed_count") or 0) > 0:
            signals.append(
                f"Chunk id={s.get('id')} tem failed_count={s.get('failed_count')} (falhas parciais de envio)."
            )
            measures.append(
                "Consultar listmessages com messageStatus=Failed para esse folder_id; "
                "causas comuns: número inválido, bloqueio, instância desconectada durante o envio."
            )
            break

    if pending > 0 and sent > 0 and api and api.get("listfolders"):
        signals.append("Campanha parcialmente enviada: investigar chunks com falha ou Scheduled remanescente.")
        measures.append(
            "Comparar soma success_count+failed_count dos chunks com lead_totals; rodar POST /api/campaigns/<id>/sync-uazapi "
            "ou admin sync se o DB estiver defasado em relação à Uazapi."
        )

    # dedupe measures order-preserving
    seen = set()
    uniq_measures = []
    for m in measures:
        if m not in seen:
            seen.add(m)
            uniq_measures.append(m)

    return {"signals": signals, "recommended_actions": uniq_measures}


def _run_api(token: str, instance_id: int, campaign_id: int, folder_ids: list[str]) -> dict[str, Any]:
    from services.uazapi import UazapiService
    from utils.sync_uazapi import get_uazapi_campaign_counts

    u = UazapiService()
    ctx = {"campaign_id": campaign_id, "instance_id": instance_id}
    out: dict[str, Any] = {
        "base_url": u.base_url,
        "instance_id": instance_id,
    }

    try:
        out["instance_status"] = u.get_status(token)
        out["instance_status_error"] = None
    except Exception as e:
        out["instance_status"] = None
        out["instance_status_error"] = str(e)

    folders = u.list_folders(token, context=ctx)
    out["listfolders"] = folders
    out["listfolders_count"] = len(folders) if isinstance(folders, list) else None

    folder_index = {}
    if isinstance(folders, list):
        for f in folders:
            if f.get("id"):
                folder_index[str(f["id"])] = f

    checks = []
    for fid in folder_ids:
        row: dict[str, Any] = {
            "folder_id": fid,
            "in_listfolders": fid in folder_index if folders is not None else None,
            "folder_row": folder_index.get(fid) if fid in folder_index else None,
        }
        if folders is not None and fid in folder_index:
            row["uazapi_counts"] = get_uazapi_campaign_counts(u, token, fid, context=ctx)
        else:
            row["uazapi_counts"] = None
        checks.append(row)
    out["folder_checks"] = checks
    return out


def _agent_prompt(bundle: dict[str, Any], api: Optional[dict[str, Any]], heur: dict[str, Any]) -> str:
    safe_api = None
    if api:
        safe_api = {
            "base_url": api.get("base_url"),
            "instance_id": api.get("instance_id"),
            "instance_status": api.get("instance_status"),
            "instance_status_error": api.get("instance_status_error"),
            "listfolders_count": api.get("listfolders_count"),
            "folder_checks": api.get("folder_checks"),
        }
    payload = {
        "role": "Você é um analista de suporte técnico da plataforma Leads Infinitos.",
        "task": (
            "Com base no JSON abaixo (estado da campanha no nosso banco + leituras da API Uazapi), "
            "explique em português claro: o que provavelmente ocorreu, onde está o problema, "
            "e quais passos concretos o operador ou o cliente devem seguir para corrigir. "
            "Se faltar dado, diga exatamente qual dado coletar a seguir."
        ),
        "data": {
            "db": bundle,
            "uazapi": safe_api,
            "precomputed_signals": heur.get("signals"),
            "precomputed_recommendations": heur.get("recommended_actions"),
        },
        "constraints": (
            "Não invente IDs de pasta ou token. Não afirme culpa sem evidência no JSON. "
            "Diferencie problema de instância (WhatsApp desconectado), token inválido, pasta inexistente em listfolders, "
            "chunk agendado sem folder_id, e defasagem entre DB e API."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico DB + Uazapi para campanha que 'não disparou'"
    )
    parser.add_argument("campaign_id", type=int, help="ID da campanha")
    parser.add_argument("--chunks-limit", type=int, default=200, help="Máx. linhas campaign_stage_sends")
    parser.add_argument("--no-api", action="store_true", help="Somente banco, sem chamar Uazapi")
    parser.add_argument("--json-out", type=str, default="", help="Grava pacote completo (sem token) em arquivo")
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Imprime apikey completo da instância Uazapi primária (uso restrito admin / n8n)",
    )
    args = parser.parse_args()
    cid = args.campaign_id

    conn = _connect()
    cur = conn.cursor()
    bundle = _load_db_bundle(cur, cid, args.chunks_limit)
    token, instance_id = _fetch_primary_uazapi_token(cur, cid)
    conn.close()

    if bundle.get("error") == "campaign_not_found":
        print(json.dumps(bundle, ensure_ascii=False))
        return 1

    api_part: Optional[dict[str, Any]] = None
    if not args.no_api:
        if not token or instance_id is None:
            print(
                "\n[aviso] Instância Uazapi sem apikey ou não vinculada — API não consultada.",
                file=sys.stderr,
            )
        else:
            fids = _folder_ids_from_bundle(bundle)
            api_part = _run_api(token, int(instance_id), cid, fids)

    db_failed_by_folder: dict[str, int] = {}
    for s in bundle.get("stage_sends") or []:
        f = s.get("uazapi_folder_id")
        if f:
            db_failed_by_folder[str(f)] = max(
                int(db_failed_by_folder.get(str(f), 0)), int(s.get("failed_count") or 0)
            )
    if api_part and api_part.get("folder_checks"):
        for chk in api_part["folder_checks"]:
            fid = chk.get("folder_id")
            if fid:
                chk["db_failed_count"] = db_failed_by_folder.get(str(fid), 0)

    heur = _heuristics(bundle, api_part)

    out_all: dict[str, Any] = {
        "campaign_id": cid,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "db": bundle,
        "uazapi_api": api_part,
        "heuristics": heur,
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out_all, f, ensure_ascii=False, indent=2, default=str)
        print(f"JSON gravado em {args.json_out}")

    print("=== Diagnóstico campanha Uazapi ===\n")
    print(f"Campaign ID: {cid}")
    print(f"Nome: {(bundle.get('campaign') or {}).get('name')}")
    print(f"Usuário: {(bundle.get('campaign') or {}).get('user_email')}")
    print(f"Status campanha: {(bundle.get('campaign') or {}).get('status')}")
    print(f"Uazapi sender: {(bundle.get('campaign') or {}).get('use_uazapi_sender')}")
    print(f"Instância primária (Uazapi): instance_id={instance_id} token={_mask_token(token)}")
    print("\n--- Totais leads (funil, não removidos) ---")
    print(json.dumps(bundle.get("lead_totals_funnel"), ensure_ascii=False, indent=2))
    print("\n--- Sinais (heurística) ---")
    for s in heur.get("signals") or []:
        print(f" • {s}")
    print("\n--- Medidas sugeridas (heurística) ---")
    for m in heur.get("recommended_actions") or []:
        print(f" • {m}")

    if api_part:
        print("\n--- API: pastas x listfolders ---")
        for chk in api_part.get("folder_checks") or []:
            print(json.dumps(chk, ensure_ascii=False, default=str))

    if args.print_token:
        if not token:
            print("\n[print-token] Sem token disponível.")
        else:
            print("\n--- TOKEN (confidencial) ---\n" + token)

    print("\n========== PROMPT PARA AGENTE (cole no chat) ==========\n")
    print(_agent_prompt(bundle, api_part, heur))
    print("\n========== fim do prompt ==========\n")
    print(
        "n8n: importe scripts/n8n-workflows/uazapi-diagnostico-campanha-advanced.json "
        "e use o token acima (credencial) para reproduzir chamadas isoladas à API."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
