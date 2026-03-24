"""
Humanização de ritmo para envios Uazapi (API 2.0): atrasos por faixas ponderadas.
Não expor minutos exatos na UI — apenas usar no create_advanced_campaign.
"""

from __future__ import annotations

import random
from typing import List, Sequence, Tuple


# (peso, min_min, min_max) — tempos entre mensagens dentro da sub-campanha (create_advanced_campaign).
# Pesos relativos 20 / 30 / 40 (normalizados para somar 1).
_BUCKET_RANGES: Sequence[Tuple[float, int, int]] = (
    (20 / 90, 4, 8),
    (30 / 90, 8, 12),
    (40 / 90, 10, 15),
)

# Pausa longa entre sub-campanhas: só atrasa o agendamento da próxima linha (sem folder/campanha Uazapi só de pausa).
_LONG_GAP_MIN = 25
_LONG_GAP_MAX = 45
_LONG_GAP_WEIGHT = 0.10  # chance entre cada par de segmentos consecutivos


def _pick_bucket_delay_minutes() -> Tuple[int, int]:
    r = random.random()
    acc = 0.0
    for w, lo, hi in _BUCKET_RANGES:
        acc += w
        if r <= acc:
            return lo, hi
    lo, hi = _BUCKET_RANGES[-1][1], _BUCKET_RANGES[-1][2]
    return lo, hi


def default_inter_message_delay_range_minutes() -> Tuple[int, int]:
    """Faixa aleatória (ponderada) para um envio — usado quando o UI não define min/max."""
    return _pick_bucket_delay_minutes()


def _random_partition(n: int, k: int) -> List[int]:
    """Parte n em k inteiros positivos (ordem preservada)."""
    if k <= 1 or n <= 1:
        return [n]
    k = min(k, n)
    cuts = sorted(random.sample(range(1, n), k - 1))
    out: List[int] = []
    lo = 0
    for c in cuts:
        out.append(c - lo)
        lo = c
    out.append(n - lo)
    return [x for x in out if x > 0]


def plan_weighted_segments(n_leads: int) -> List[Tuple[int, int, int]]:
    """
    Para n leads, devolve lista de segmentos: (count, delay_min, delay_max).
    Soma dos counts = n. Vários segmentos ⇒ várias sub-campanhas Uazapi com ritmos distintos.
    """
    if n_leads <= 0:
        return []
    if n_leads <= 3:
        d0, d1 = _pick_bucket_delay_minutes()
        return [(n_leads, d0, d1)]
    k = random.randint(2, min(5, max(2, n_leads // 5)))
    sizes = _random_partition(n_leads, k)
    return [(sz, *_pick_bucket_delay_minutes()) for sz in sizes]


def estimate_segment_span_minutes(count: int, delay_min: int, delay_max: int) -> float:
    """Estimativa conservadora do tempo para esgotar fila (entre mensagens)."""
    if count <= 1:
        return 0.0
    avg = (delay_min + delay_max) / 2.0
    return float(count - 1) * avg


def maybe_long_gap_minutes() -> int:
    """~10% de chance de pausa longa (minutos) antes do próximo segmento; não cria campanha/disparo extra."""
    if random.random() < _LONG_GAP_WEIGHT:
        return random.randint(_LONG_GAP_MIN, _LONG_GAP_MAX)
    return 0


def build_pacing_segments_for_leads(
    leads: Sequence,
) -> List[Tuple[Sequence, int, int, int]]:
    """
    Divide `leads` em sub-listas com (sub_leads, dmin, dmax, gap_after_minutes).
    gap_after aplica-se após cada segmento exceto o último (sempre 0 no último).
    """
    n = len(leads)
    if n == 0:
        return []
    plan = plan_weighted_segments(n)
    out: List[Tuple[Sequence, int, int, int]] = []
    offset = 0
    for idx, (cnt, dmin, dmax) in enumerate(plan):
        chunk = leads[offset : offset + cnt]
        offset += cnt
        gap = 0 if idx == len(plan) - 1 else maybe_long_gap_minutes()
        out.append((chunk, dmin, dmax, gap))
    return out


def stagger_scheduled_utc_naive(base_utc_naive, delta_minutes: float):
    """Retorna base + timedelta(minutes) como datetime naive UTC."""
    from datetime import datetime, timedelta

    return base_utc_naive + timedelta(minutes=delta_minutes)
