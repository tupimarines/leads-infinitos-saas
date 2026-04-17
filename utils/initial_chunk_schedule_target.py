"""
Alvo de agendamento para chunks ``initial`` Uazapi (worker / T6).

Mantém a lógica de slot matinal + dia útil isolada de ``worker_cadence`` para testes
unitários sem importar o worker (``load_dotenv`` / serviços pesados).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytz

from utils.next_valid_uazapi_send import is_campaign_send_window

BRAZIL_TZ = pytz.timezone("America/Sao_Paulo")


def cadence_next_send_datetime(from_dt, delay_days, send_hour_start, send_saturday, send_sunday):
    """
    Calcula próximo dia útil no horário send_hour_start.
    Pula sábado/domingo se send_saturday/send_sunday forem False.
    delay_days=0: envia no próximo ciclo (~2 min) para testes.
    """
    if delay_days <= 0:
        return from_dt + timedelta(minutes=2)
    send_sat = bool(send_saturday)
    send_sun = bool(send_sunday)
    d = from_dt.date()
    remaining = delay_days
    for _ in range(30):
        wd = d.weekday()
        if wd == 5 and not send_sat:
            d += timedelta(days=1)
            continue
        if wd == 6 and not send_sun:
            d += timedelta(days=1)
            continue
        if remaining <= 0:
            break
        remaining -= 1
        d += timedelta(days=1)
    target = datetime(d.year, d.month, d.day, send_hour_start or 8, 0, 0, tzinfo=BRAZIL_TZ)
    return target


def cadence_next_initial_send_slot(now_brazil, send_hour, send_sat, send_sun):
    """
    Próximo horário de envio para chunk inicial (worker): APENAS o primeiro slot do dia.

    Antes este cálculo vivia em ``worker_cadence._next_initial_send_slot``; mantém-se aqui
    para testes sem importar o worker.

    - Antes de send_hour hoje: slot às send_hour (primeiro disparo do dia).
    - Depois: próximo dia útil às send_hour (não encadeia chunks automaticamente).

    Chunks adicionais no mesmo dia: via Continuar / same-day (D1) quando aplicável.

    **Eixos (não confundir):** (1) *Calendário* — no máximo um insert automático por dia
    civil no slot matinal (anti-flood de agendamento). (2) *Cota / plano* —
    ``check_initial_chunk_daily_quota_for_campaign``, ``get_user_daily_limit`` e
    ``campaigns.daily_limit`` (TD-12). (3) *Janela BRT* na hora de materializar —
    ``is_campaign_send_window`` / ``next_valid_send_utc_naive`` em
    ``_materialize_scheduled_stage_sends``, não nesta função.

    ``can_create_campaign_today`` permanece sempre ``True`` no código atual; não usar
    como SSOT de volume.
    """
    send_hour = send_hour or 8
    today_at = datetime(
        now_brazil.year,
        now_brazil.month,
        now_brazil.day,
        send_hour,
        0,
        0,
        tzinfo=BRAZIL_TZ,
    )
    if now_brazil < today_at:
        return today_at
    return cadence_next_send_datetime(now_brazil, 1, send_hour, send_sat, send_sun)


def uazapi_same_day_initial_chunk_after_unlock_enabled():
    """
    TD-4 / T6 (D1): mesmo dia após destrave só com gatilho explícito (default desligado).
    ``UAZAPI_SAME_DAY_INITIAL_CHUNK_AFTER_UNLOCK=1`` ativa o ramo ``same_day_after_unlock``.
    """
    v = os.environ.get("UAZAPI_SAME_DAY_INITIAL_CHUNK_AFTER_UNLOCK", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def resolve_initial_chunk_schedule_target(
    *,
    now_brazil: datetime,
    send_hour_start: int,
    send_sat: bool,
    send_sun: bool,
    scheduled_start_raw,
    campaign_send_window: dict,
    same_day_env_enabled: bool,
    quota_allows_today: bool,
):
    """
    T6: decide ``target_dt`` (BRT aware) e flags antes do INSERT de ``campaign_stage_sends``.

    Retorna ``(target_dt, use_immediate, reason)`` onde ``reason`` é ``same_day_after_unlock``
    quando o env D1 + janela BRT + cota + ausência de ``scheduled_start`` permitem chunk no mesmo
    dia civil em vez de empurrar para o próximo slot matinal (D+1).
    """
    send_hour = int(send_hour_start or 8)
    today_at = datetime(
        now_brazil.year,
        now_brazil.month,
        now_brazil.day,
        send_hour,
        0,
        0,
        tzinfo=BRAZIL_TZ,
    )
    use_immediate = False
    reason = None

    sched_start = scheduled_start_raw
    if sched_start:
        if getattr(sched_start, "tzinfo", None) is None:
            sched_start = pytz.UTC.localize(sched_start).astimezone(BRAZIL_TZ)
        else:
            sched_start = sched_start.astimezone(BRAZIL_TZ)
        delta_sec = (now_brazil - sched_start).total_seconds()
        if delta_sec >= 0:
            use_immediate = True
            target_dt = now_brazil + timedelta(seconds=30)
        else:
            target_dt = cadence_next_initial_send_slot(now_brazil, send_hour, send_sat, send_sun)
    else:
        target_dt = cadence_next_initial_send_slot(now_brazil, send_hour, send_sat, send_sun)

    if (
        not use_immediate
        and scheduled_start_raw is None
        and same_day_env_enabled
        and quota_allows_today
        and now_brazil >= today_at
        and is_campaign_send_window(campaign_send_window, now_brazil=now_brazil)
    ):
        target_dt = now_brazil + timedelta(seconds=30)
        reason = "same_day_after_unlock"

    return target_dt, use_immediate, reason
