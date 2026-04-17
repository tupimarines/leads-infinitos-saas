"""Garantias de agendamento de chunks initial (UAZAPI) em worker_cadence."""

import datetime as dt

import pytz

BRAZIL_TZ = pytz.timezone("America/Sao_Paulo")


def test_initial_chunk_active_statuses_excludes_terminal_and_failed():
    """Após sync marcar send órfão como failed, a instância deve poder receber novo chunk."""
    from utils.limits import INITIAL_CHUNK_ACTIVE_SEND_STATUSES

    active = set(INITIAL_CHUNK_ACTIVE_SEND_STATUSES)
    assert active == {"scheduled", "running", "partial", "queued"}
    assert "failed" not in active
    assert "done" not in active


def test_resolve_initial_chunk_same_day_after_unlock_afternoon():
    """T6 / D1: após slot matinal, com env + janela + cota, alvo passa a ser hoje (now+30s)."""
    from utils.initial_chunk_schedule_target import resolve_initial_chunk_schedule_target

    # Quarta 14:00 BRT; send_hour_start 8 → slot clássico seria quinta 8h; same-day deve antecipar.
    now_brazil = BRAZIL_TZ.localize(dt.datetime(2026, 4, 15, 14, 0, 0))
    camp_win = {
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": False,
        "send_sunday": False,
    }
    target_dt, use_immediate, reason = resolve_initial_chunk_schedule_target(
        now_brazil=now_brazil,
        send_hour_start=8,
        send_sat=False,
        send_sun=False,
        scheduled_start_raw=None,
        campaign_send_window=camp_win,
        same_day_env_enabled=True,
        quota_allows_today=True,
    )
    assert use_immediate is False
    assert reason == "same_day_after_unlock"
    assert (target_dt - now_brazil).total_seconds() == 30


def test_resolve_initial_chunk_same_day_not_before_morning_slot():
    """Antes do send_hour_start não ativa same-day (mantém slot das 8h do mesmo dia)."""
    from utils.initial_chunk_schedule_target import resolve_initial_chunk_schedule_target

    now_brazil = BRAZIL_TZ.localize(dt.datetime(2026, 4, 15, 7, 30, 0))
    camp_win = {
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": False,
        "send_sunday": False,
    }
    target_dt, use_immediate, reason = resolve_initial_chunk_schedule_target(
        now_brazil=now_brazil,
        send_hour_start=8,
        send_sat=False,
        send_sun=False,
        scheduled_start_raw=None,
        campaign_send_window=camp_win,
        same_day_env_enabled=True,
        quota_allows_today=True,
    )
    assert reason is None
    assert target_dt.hour == 8 and target_dt.minute == 0
    assert target_dt.date() == now_brazil.date()


def test_resolve_initial_chunk_same_day_respects_quota_flag():
    from utils.initial_chunk_schedule_target import resolve_initial_chunk_schedule_target

    now_brazil = BRAZIL_TZ.localize(dt.datetime(2026, 4, 15, 14, 0, 0))
    camp_win = {
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": False,
        "send_sunday": False,
    }
    target_dt, _, reason = resolve_initial_chunk_schedule_target(
        now_brazil=now_brazil,
        send_hour_start=8,
        send_sat=False,
        send_sun=False,
        scheduled_start_raw=None,
        campaign_send_window=camp_win,
        same_day_env_enabled=True,
        quota_allows_today=False,
    )
    assert reason is None
    assert target_dt.date() > now_brazil.date()


def test_resolve_initial_chunk_scheduled_start_future_blocks_same_day():
    from utils.initial_chunk_schedule_target import resolve_initial_chunk_schedule_target

    now_brazil = BRAZIL_TZ.localize(dt.datetime(2026, 4, 15, 14, 0, 0))
    future_start = BRAZIL_TZ.localize(dt.datetime(2026, 4, 16, 10, 0, 0))
    camp_win = {
        "send_hour_start": 8,
        "send_hour_end": 20,
        "send_saturday": False,
        "send_sunday": False,
    }
    _, _, reason = resolve_initial_chunk_schedule_target(
        now_brazil=now_brazil,
        send_hour_start=8,
        send_sat=False,
        send_sun=False,
        scheduled_start_raw=future_start.astimezone(pytz.UTC).replace(tzinfo=None),
        campaign_send_window=camp_win,
        same_day_env_enabled=True,
        quota_allows_today=True,
    )
    assert reason is None
