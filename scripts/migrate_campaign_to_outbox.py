#!/usr/bin/env python3
"""
Migração legado (pastas / campaign_stage_sends) → fila Postgres ``campaign_message_outbox``.

Task 9 (tech-spec envio-individual-fila-intercalada-campanhas):
- Ordenação canónica de leads alinhada a ``_create_campaign_core`` / worker:
  ``ORDER BY COALESCE(send_batch, 999) ASC, id ASC`` (F15).
- Índice de retomada: soma de posições “consumidas” pelos chunks ``initial`` em
  ``campaign_stage_sends`` (ordem de criação ``id ASC``), conforme proposta de produto:
  chunks em ``scheduled``, ``failed`` ou ``queued`` contam na totalidade como saltados;
  ``done`` usa ``success_count`` (fallback ``planned_count``); ``running`` / ``partial``
  usam ``success_count + failed_count`` (fallback tamanho do chunk) para não reenviar
  leads já cobertos pelo legado.

Uso:
  python scripts/migrate_campaign_to_outbox.py --campaign-id 123 [--dry-run]
  python scripts/migrate_campaign_to_outbox.py --campaign-id 123 --force

Requisitos: schema com ``campaign_message_outbox``; campanha ``use_uazapi_sender``;
recomenda-se ``USE_MESSAGE_OUTBOX=1`` no ambiente antes do worker enviar.

Não apaga ``campaign_stage_sends`` nem pastas remotas Uazapi (checklist F20 continua manual).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def get_db_connection():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "leads_infinitos"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "devpassword"),
        port=os.environ.get("DB_PORT", "5432"),
    )


def ordered_campaign_lead_ids(conn, campaign_id: int) -> List[int]:
    """
    Mesma ordenação que ``_create_campaign_core`` / materialização initial (F15).
    Inclui todos os leads da campanha (não só pendentes) para índice estável.
    """
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id
            FROM campaign_leads
            WHERE campaign_id = %s
              AND COALESCE(removed_from_funnel, FALSE) = FALSE
            ORDER BY COALESCE(send_batch, 999) ASC, COALESCE(csv_row_order, id) ASC, id ASC
            """,
            (campaign_id,),
        )
        rows = cur.fetchall() or []
    return [int(r["id"]) for r in rows]


def _chunk_size_from_row(row: dict) -> int:
    lj = row.get("lead_ids")
    if isinstance(lj, list):
        return len(lj)
    if isinstance(lj, str):
        try:
            parsed = json.loads(lj)
            if isinstance(parsed, list):
                return len(parsed)
        except Exception:
            pass
    try:
        return max(0, int(row.get("planned_count") or 0))
    except Exception:
        return 0


def compute_legacy_initial_migration_offset(conn, campaign_id: int) -> Tuple[int, list]:
    """
    Retorna (offset_0based, detalhe_por_chunk) para fatiar ``ordered_campaign_lead_ids``.
    """
    from psycopg2.extras import RealDictCursor

    detail = []
    offset = 0
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, status, planned_count, success_count, failed_count, lead_ids
            FROM campaign_stage_sends
            WHERE campaign_id = %s AND stage = 'initial'
            ORDER BY id ASC
            """,
            (campaign_id,),
        )
        rows = cur.fetchall() or []

    for row in rows:
        st = (row.get("status") or "").lower()
        n = _chunk_size_from_row(row)
        sc = int(row.get("success_count") or 0)
        fc = int(row.get("failed_count") or 0)
        delta = 0
        if st in ("scheduled", "failed", "queued"):
            delta = n
        elif st == "done":
            delta = sc if sc > 0 else n
        elif st in ("running", "partial"):
            touched = sc + fc
            delta = touched if touched > 0 else n
        else:
            delta = 0
        offset += delta
        detail.append(
            {
                "stage_send_id": row.get("id"),
                "status": st,
                "chunk_size": n,
                "delta_positions": delta,
                "offset_after": offset,
            }
        )
    return offset, detail


def compute_outbox_migration_start_index(campaign_id: int) -> dict:
    """
    API estável para testes / inspeção: devolve índice 1-based do primeiro lead
    da ordenação canónica a considerar para outbox após saltar o prefixo legado.
    """
    conn = get_db_connection()
    try:
        ordered = ordered_campaign_lead_ids(conn, campaign_id)
        off, detail = compute_legacy_initial_migration_offset(conn, campaign_id)
        first_lead_id = None
        one_based = None
        if off < len(ordered):
            first_lead_id = ordered[off]
            one_based = off + 1
        return {
            "campaign_id": campaign_id,
            "ordered_lead_count": len(ordered),
            "offset_0based": off,
            "first_outbox_lead_id": first_lead_id,
            "first_outbox_lead_index_1based": one_based,
            "chunks": detail,
        }
    finally:
        conn.close()


def run_migrate(
    campaign_id: int,
    dry_run: bool,
    force: bool,
    offset_override: Optional[int],
) -> int:
    from psycopg2.extras import RealDictCursor

    try:
        from app import _get_uazapi_instances_for_campaign, _parse_iso_datetime_local
    except ImportError as e:
        print(f"Falha ao importar app (execute na raiz do repo com venv): {e}")
        return 1

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, user_id, use_uazapi_sender, rotation_mode, scheduled_start,
                       outbox_delay_min_seconds, outbox_delay_max_seconds, status
                FROM campaigns WHERE id = %s
                """,
                (campaign_id,),
            )
            camp = cur.fetchone()
        if not camp:
            print(f"Campanha {campaign_id} não encontrada.")
            return 1
        if not camp.get("use_uazapi_sender"):
            print("Esta campanha não usa Uazapi (use_uazapi_sender=false). Abortado.")
            return 1

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM campaign_message_outbox WHERE campaign_id = %s",
                (campaign_id,),
            )
            existing = cur.fetchone()[0]
        if existing and not force:
            print(
                f"Já existem {existing} linhas em campaign_message_outbox para esta campanha. "
                "Use --force para continuar mesmo assim."
            )
            return 1

        ordered = ordered_campaign_lead_ids(conn, campaign_id)
        off, chunk_detail = compute_legacy_initial_migration_offset(conn, campaign_id)
        if offset_override is not None:
            print(f"Override: offset_0based {off} → {offset_override}")
            off = max(0, int(offset_override))

        if off > len(ordered):
            print(
                f"Aviso: offset {off} excede número de leads ordenados ({len(ordered)}). "
                "Nada a enfileirar."
            )
            return 0

        first_id = ordered[off] if off < len(ordered) else None
        print(
            json.dumps(
                {
                    "campaign_id": campaign_id,
                    "ordered_lead_count": len(ordered),
                    "offset_0based": off,
                    "first_outbox_lead_index_1based": off + 1 if first_id else None,
                    "first_outbox_lead_id": first_id,
                    "chunks_evaluated": chunk_detail,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        user_id = int(camp["user_id"])
        instances = _get_uazapi_instances_for_campaign(campaign_id, user_id)
        if not instances:
            print("Nenhuma instância Uazapi para a campanha. Abortado.")
            return 1

        try:
            from utils.limits import can_create_campaign_today
        except ImportError:
            can_create_campaign_today = lambda _iid: True  # type: ignore

        allowed = [inst for inst in instances if can_create_campaign_today(inst["instance_id"])]
        if not allowed:
            print("Nenhuma instância passou em can_create_campaign_today hoje. Abortado.")
            return 1

        rotation_mode = (camp.get("rotation_mode") or "single").strip()
        n_allowed = len(allowed)

        scheduled_start = camp.get("scheduled_start")
        parsed_ss = _parse_iso_datetime_local(scheduled_start) if scheduled_start else None
        now_utc = datetime.utcnow()
        if parsed_ss:
            next_run_at_val = max(now_utc, parsed_ss)
        else:
            next_run_at_val = now_utc

        # ADR-2: garantir cooldown outbox na campanha
        dmin = camp.get("outbox_delay_min_seconds")
        dmax = camp.get("outbox_delay_max_seconds")
        delay_patch = None
        if dmin is None or dmax is None:
            _dlo = random.randint(600, 900)
            _dhi = random.randint(600, 900)
            dmin, dmax = min(_dlo, _dhi), max(_dlo, _dhi)
            delay_patch = (dmin, dmax)

        to_enqueue: List[int] = []
        for lid in ordered[off:]:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, status FROM campaign_leads WHERE id = %s AND campaign_id = %s",
                    (lid, campaign_id),
                )
                lr = cur.fetchone()
            if not lr:
                continue
            if (lr.get("status") or "").lower() != "pending":
                continue
            to_enqueue.append(int(lr["id"]))

        print(f"Leads pendentes a enfileirar (initial): {len(to_enqueue)}")
        if dry_run:
            print("--dry-run: nenhum INSERT/COMMIT.")
            return 0

        with conn.cursor() as cur:
            if delay_patch:
                cur.execute(
                    """
                    UPDATE campaigns
                    SET outbox_delay_min_seconds = %s,
                        outbox_delay_max_seconds = %s
                    WHERE id = %s
                    """,
                    (delay_patch[0], delay_patch[1], campaign_id),
                )

            for i, lead_id in enumerate(to_enqueue):
                if rotation_mode == "round_robin":
                    inst = allowed[i % n_allowed]
                else:
                    inst = allowed[0]
                instance_id = int(inst["instance_id"])
                idempotency_key = f"campaign-{campaign_id}-lead-{lead_id}-initial"
                payload_summary = json.dumps(
                    {
                        "stage": "initial",
                        "enqueue": "migrate_campaign_to_outbox",
                        "rotation_mode": rotation_mode,
                    },
                    ensure_ascii=False,
                )
                cur.execute(
                    """
                    INSERT INTO campaign_message_outbox (
                        campaign_id, campaign_lead_id, instance_id,
                        stage, step_priority, status, queued_at,
                        next_run_at, idempotency_key, payload_summary
                    )
                    VALUES (
                        %s, %s, %s, 'initial', 0, 'pending',
                        NOW(), %s, %s, %s::jsonb
                    )
                    ON CONFLICT (campaign_lead_id, stage) DO NOTHING
                    """,
                    (
                        campaign_id,
                        lead_id,
                        instance_id,
                        next_run_at_val,
                        idempotency_key,
                        payload_summary,
                    ),
                )

            if to_enqueue:
                cur.execute(
                    """
                    UPDATE campaign_leads
                    SET current_step = 1
                    WHERE campaign_id = %s AND id = ANY(%s)
                    """,
                    (campaign_id, to_enqueue),
                )

        conn.commit()
        print(f"OK: {len(to_enqueue)} linhas outbox (ON CONFLICT ignorado se duplicado).")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"Erro: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        conn.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Migra campanha legado Uazapi para outbox (Task 9).")
    p.add_argument("--campaign-id", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="Permite migrar mesmo com outbox já existente.")
    p.add_argument(
        "--offset-override",
        type=int,
        default=None,
        help="Força offset 0-based (ignora cálculo a partir de campaign_stage_sends).",
    )
    args = p.parse_args(argv)
    return run_migrate(
        args.campaign_id,
        dry_run=args.dry_run,
        force=args.force,
        offset_override=args.offset_override,
    )


if __name__ == "__main__":
    sys.exit(main())
