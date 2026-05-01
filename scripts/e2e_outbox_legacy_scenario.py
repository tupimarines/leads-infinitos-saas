#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cenário manual: campanha legada ``campaign_stage_sends`` (chunks mistos) + migração outbox.

Objetivos:
- Validar offset da migração (AC8 / proposta 10 done + 8 scheduled → índice 19).
- Opcionalmente aplicar ``migrate_campaign_to_outbox`` e rodar ticks do worker com delays curtos (5–10 s).

Requisitos:
- Postgres acessível (mesmas envs que o app: ``DB_*``).
- Para envio real Uazapi: ``USE_MESSAGE_OUTBOX=1``, worker ou este script com ``--ticks``,
  instância ``api_provider=uazapi`` com token válido e telefones nos leads.

Uso (raiz do repo, venv activo):

  # Só criar dados + imprimir JSON do offset (sem migrate)
  python scripts/e2e_outbox_legacy_scenario.py --apply-seed --scenario ac8

  # Atualizar telefones dos leads seedados (válidos ou inválidos — ver tentativas em campaign_send_attempts)
  python scripts/e2e_outbox_legacy_scenario.py --campaign-id ID --set-phones 5511999990001,5511999990002

  # Dry-run da migração (mesmo CLI que migrate_campaign_to_outbox.py)
  python scripts/migrate_campaign_to_outbox.py --campaign-id ID --dry-run

  # Aplicar migração + N ticks do outbox (HTTP real se token válido)
  set USE_MESSAGE_OUTBOX=1
  python scripts/e2e_outbox_legacy_scenario.py --campaign-id ID --migrate --ticks 3

Depois rode também: ``pytest tests/test_outbox_spec_acceptance.py tests/test_admin_campaign_crud.py -q``
para evidência automatizada dos ACs cobertos em CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

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


def _migrate_mod():
    import importlib.util

    path = os.path.join(ROOT, "scripts", "migrate_campaign_to_outbox.py")
    spec = importlib.util.spec_from_file_location("migrate_campaign_to_outbox", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def seed_ac8(conn, *, marker: str) -> dict:
    """
    Cria utilizador alvo, instância Uazapi, campanha ``running``, 25 leads pendentes,
    10 chunks ``done`` (1 lead) + 1 ``scheduled`` (8 leads). Cooldown outbox 5–10 s.
    """
    from psycopg2.extras import RealDictCursor

    label = f"E2E_OUTBOX_LEGACY_{marker}"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM users WHERE email = %s",
            (f"{label.lower()}@example.invalid",),
        )
        row = cur.fetchone()
        if row:
            uid = int(row["id"])
        else:
            cur.execute(
                """
                INSERT INTO users (email, password_hash, is_admin)
                VALUES (%s, %s, false)
                RETURNING id
                """,
                (f"{label.lower()}@example.invalid", "x"),
            )
            uid = int(cur.fetchone()["id"])

        cur.execute(
            """INSERT INTO instances (user_id, name, apikey, status, api_provider)
               VALUES (%s, %s, %s, 'connected', 'uazapi')
               RETURNING id""",
            (uid, f"{label}_inst", os.environ.get("UAZAPI_TEST_TOKEN", "REPLACE_ME")),
        )
        iid = int(cur.fetchone()["id"])

        msg = json.dumps([f"[{label}] Olá {{nome}}, teste legado→outbox."])
        cur.execute(
            """
            INSERT INTO campaigns (
                user_id, name, message_template, status,
                use_uazapi_sender, rotation_mode,
                send_hour_start, send_hour_end, send_saturday, send_sunday,
                outbox_delay_min_seconds, outbox_delay_max_seconds,
                enable_cadence
            )
            VALUES (
                %s, %s, %s, 'running',
                true, 'single',
                0, 23, true, true,
                5, 10,
                false
            )
            RETURNING id
            """,
            (uid, label, msg),
        )
        cid = int(cur.fetchone()["id"])

        cur.execute(
            "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s)",
            (cid, iid),
        )
        cur.execute(
            """
            INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template)
            VALUES (%s, 1, 'Inicial', %s::text)
            """,
            (cid, msg),
        )

        lead_ids = []
        for i in range(25):
            cur.execute(
                """
                INSERT INTO campaign_leads (campaign_id, phone, name, status)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id
                """,
                (cid, f"5511999{i:05d}", f"L{i}"),
            )
            lead_ids.append(int(cur.fetchone()["id"]))

        for j in range(10):
            cur.execute(
                """
                INSERT INTO campaign_stage_sends (
                    campaign_id, stage, instance_id, status,
                    planned_count, success_count, failed_count, lead_ids
                )
                VALUES (%s, 'initial', %s, 'done', 1, 1, 0, %s::jsonb)
                """,
                (cid, iid, json.dumps([lead_ids[j]])),
            )

        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'scheduled', 8, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(lead_ids[10:18])),
        )

        # Extras pedidos: um chunk ``failed`` e um status ``pending`` no stage_send (delta 0 na migração)
        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'failed', 2, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(lead_ids[18:20])),
        )
        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'pending', 3, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(lead_ids[20:23])),
        )

    conn.commit()

    mig = _migrate_mod()
    ordered = mig.ordered_campaign_lead_ids(conn, cid)
    off, detail = mig.compute_legacy_initial_migration_offset(conn, cid)
    summary = {
        "campaign_id": cid,
        "user_id": uid,
        "instance_id": iid,
        "marker": label,
        "ordered_lead_count": len(ordered),
        "offset_0based": off,
        "first_outbox_lead_index_1based": off + 1 if off < len(ordered) else None,
        "first_outbox_lead_id": ordered[off] if off < len(ordered) else None,
        "chunks": detail,
        "note": "Cenário misto: done×10 + scheduled×8 + failed×2 + pending(chunk)×3 → offset 10+8+2=20 (pending não conta).",
    }
    return summary


def seed_ac8_pure(conn, *, marker: str) -> dict:
    """Somente AC8 estrito (11 chunks), sem failed/pending extra."""
    from psycopg2.extras import RealDictCursor

    label = f"E2E_AC8_ONLY_{marker}"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM users WHERE email = %s",
            (f"{label.lower()}@example.invalid",),
        )
        row = cur.fetchone()
        if row:
            uid = int(row["id"])
        else:
            cur.execute(
                """
                INSERT INTO users (email, password_hash, is_admin)
                VALUES (%s, %s, false)
                RETURNING id
                """,
                (f"{label.lower()}@example.invalid", "x"),
            )
            uid = int(cur.fetchone()["id"])

        cur.execute(
            """INSERT INTO instances (user_id, name, apikey, status, api_provider)
               VALUES (%s, %s, %s, 'connected', 'uazapi')
               RETURNING id""",
            (uid, f"{label}_inst", os.environ.get("UAZAPI_TEST_TOKEN", "REPLACE_ME")),
        )
        iid = int(cur.fetchone()["id"])

        msg = json.dumps([f"[{label}] ping {{nome}}"])
        cur.execute(
            """
            INSERT INTO campaigns (
                user_id, name, message_template, status,
                use_uazapi_sender, rotation_mode,
                send_hour_start, send_hour_end, send_saturday, send_sunday,
                outbox_delay_min_seconds, outbox_delay_max_seconds,
                enable_cadence
            )
            VALUES (
                %s, %s, %s, 'running',
                true, 'single',
                0, 23, true, true,
                5, 10,
                false
            )
            RETURNING id
            """,
            (uid, label, msg),
        )
        cid = int(cur.fetchone()["id"])

        cur.execute(
            "INSERT INTO campaign_instances (campaign_id, instance_id) VALUES (%s, %s)",
            (cid, iid),
        )
        cur.execute(
            """
            INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template)
            VALUES (%s, 1, 'Inicial', %s::text)
            """,
            (cid, msg),
        )

        lead_ids = []
        for i in range(25):
            cur.execute(
                """
                INSERT INTO campaign_leads (campaign_id, phone, name, status)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id
                """,
                (cid, f"5511888{i:05d}", f"L{i}"),
            )
            lead_ids.append(int(cur.fetchone()["id"]))

        for j in range(10):
            cur.execute(
                """
                INSERT INTO campaign_stage_sends (
                    campaign_id, stage, instance_id, status,
                    planned_count, success_count, failed_count, lead_ids
                )
                VALUES (%s, 'initial', %s, 'done', 1, 1, 0, %s::jsonb)
                """,
                (cid, iid, json.dumps([lead_ids[j]])),
            )

        cur.execute(
            """
            INSERT INTO campaign_stage_sends (
                campaign_id, stage, instance_id, status,
                planned_count, success_count, failed_count, lead_ids
            )
            VALUES (%s, 'initial', %s, 'scheduled', 8, 0, 0, %s::jsonb)
            """,
            (cid, iid, json.dumps(lead_ids[10:18])),
        )

    conn.commit()

    mig = _migrate_mod()
    ordered = mig.ordered_campaign_lead_ids(conn, cid)
    off, detail = mig.compute_legacy_initial_migration_offset(conn, cid)
    return {
        "campaign_id": cid,
        "user_id": uid,
        "instance_id": iid,
        "marker": label,
        "ordered_lead_count": len(ordered),
        "offset_0based": off,
        "first_outbox_lead_index_1based": off + 1 if off < len(ordered) else None,
        "first_outbox_lead_id": ordered[off] if off < len(ordered) else None,
        "chunks": detail,
        "expect_ac8_index_19": off == 18 and ordered[off] == lead_ids[18],
    }


def update_phones(conn, campaign_id: int, phones_csv: str) -> None:
    from psycopg2.extras import RealDictCursor

    phones = [p.strip() for p in phones_csv.split(",") if p.strip()]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id FROM campaign_leads
            WHERE campaign_id = %s AND status = 'pending'
            ORDER BY COALESCE(send_batch, 999) ASC, id ASC
            """,
            (campaign_id,),
        )
        rows = cur.fetchall() or []
    if not phones:
        raise SystemExit("Nenhum telefone em --set-phones")
    with conn.cursor() as cur:
        for i, r in enumerate(rows):
            phone = phones[i % len(phones)]
            cur.execute("UPDATE campaign_leads SET phone = %s WHERE id = %s", (phone, r["id"]))
    conn.commit()
    print(f"OK: atualizados {min(len(rows), len(phones))} leads com lista cíclica de {len(phones)} números.")


def run_ticks(n: int) -> None:
    import utils.config as cfg

    if not getattr(cfg, "USE_MESSAGE_OUTBOX", False):
        print("AVISO: USE_MESSAGE_OUTBOX não está ligado neste processo — defina USE_MESSAGE_OUTBOX=1 antes de --ticks.")
    import worker_message_outbox as wmo

    c = get_db_connection()
    try:
        for i in range(n):
            wmo.process_message_outbox_tick(c)
            print(f"tick {i + 1}/{n} OK")
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Seed legado + verificação migração outbox (manual).")
    p.add_argument("--apply-seed", action="store_true", help="Persiste linhas no Postgres")
    p.add_argument("--scenario", choices=("ac8", "mixed"), default="mixed", help="ac8=só AC8; mixed=+failed+pending chunk")
    p.add_argument("--marker", default="demo", help="Sufixo único no nome da campanha")
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--set-phones", type=str, default=None, help="Lista separada por vírgula")
    p.add_argument("--migrate", action="store_true", help="Chama run_migrate no migrate_campaign_to_outbox")
    p.add_argument("--migrate-force", action="store_true", help="Passa --force à migração")
    p.add_argument("--ticks", type=int, default=0, help="Executa N ticks do worker outbox neste processo")
    args = p.parse_args(argv)

    conn = get_db_connection()
    try:
        if args.apply_seed:
            fn = seed_ac8_pure if args.scenario == "ac8" else seed_ac8
            out = fn(conn, marker=args.marker)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            if args.scenario == "ac8" and not out.get("expect_ac8_index_19"):
                print("ERRO: AC8 esperado índice 19 — revisar dados.", file=sys.stderr)
                return 2
            return 0

        if args.set_phones:
            if not args.campaign_id:
                print("--campaign-id obrigatório com --set-phones", file=sys.stderr)
                return 1
            update_phones(conn, args.campaign_id, args.set_phones)
            return 0

        if args.migrate:
            if not args.campaign_id:
                print("--campaign-id obrigatório com --migrate", file=sys.stderr)
                return 1
            mig = _migrate_mod()
            code = mig.run_migrate(
                args.campaign_id,
                dry_run=False,
                force=args.migrate_force,
                offset_override=None,
            )
            return code

        if args.ticks > 0:
            run_ticks(args.ticks)
            return 0

        p.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
