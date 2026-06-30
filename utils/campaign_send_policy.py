"""
Política de cota para envios iniciais (chunk Uazapi / destrave).

TD-12 (tech-spec recuperacao-scheduled-stale-worker-cadence-uazapi):
- **g1**: apenas teto global do utilizador (plano / get_user_daily_limit).
- **g2**: teto global **e** `campaigns.daily_limit` por campanha
  (ambos devem permitir mais um envio inicial contado como em check_daily_limit).
- **g3** (default): apenas teto da campanha.

Contagens em BRT alinham-se a `get_sent_today_count` / `check_daily_limit` em utils.limits.
"""

from __future__ import annotations

from typing import Literal, TypedDict

InitialChunkQuotaPolicy = Literal["g1", "g2", "g3"]

# SSOT da escolha de produto/engineering para este repositório (TD-12).
INITIAL_CHUNK_DAILY_QUOTA_POLICY: InitialChunkQuotaPolicy = "g3"


class EffectiveInitialDailyCaps(TypedDict):
    """Caps efetivos para gating de iniciais no dia BRT corrente."""

    policy: InitialChunkQuotaPolicy
    user_cap: int | None
    campaign_cap: int | None


def effective_initial_daily_caps(
    plan_daily_limit: int,
    campaign_daily_limit: int,
    policy: InitialChunkQuotaPolicy | str | None = None,
) -> EffectiveInitialDailyCaps:
    """
    Devolve os tetos usados pela política escolhida (sem consultar BD).

    - g1: só `user_cap` (plano); `campaign_cap` é None (ignorado no gate).
    - g2: `user_cap` e `campaign_cap` (ambos obrigatórios no gate).
    - g3: só `campaign_cap`; `user_cap` é None.
    """
    pol = (policy or INITIAL_CHUNK_DAILY_QUOTA_POLICY).lower()
    if pol not in ("g1", "g2", "g3"):
        pol = INITIAL_CHUNK_DAILY_QUOTA_POLICY

    if pol == "g1":
        return {"policy": "g1", "user_cap": int(plan_daily_limit), "campaign_cap": None}
    if pol == "g3":
        return {"policy": "g3", "user_cap": None, "campaign_cap": int(campaign_daily_limit)}
    return {
        "policy": "g2",
        "user_cap": int(plan_daily_limit),
        "campaign_cap": int(campaign_daily_limit),
    }


def initial_chunk_daily_quota_allows(
    sent_today_user: int,
    sent_today_campaign_initial: int,
    *,
    plan_daily_limit: int,
    campaign_daily_limit: int,
    policy: InitialChunkQuotaPolicy | str | None = None,
) -> bool:
    """
    True se, pela política, ainda cabe pelo menos mais um envio inicial
    (comparável a `check_daily_limit`: contagem estritamente menor que o teto).
    """
    caps = effective_initial_daily_caps(plan_daily_limit, campaign_daily_limit, policy)
    pol = caps["policy"]

    if pol == "g1":
        assert caps["user_cap"] is not None
        return sent_today_user < caps["user_cap"]
    if pol == "g3":
        assert caps["campaign_cap"] is not None
        return sent_today_campaign_initial < caps["campaign_cap"]
    assert caps["user_cap"] is not None and caps["campaign_cap"] is not None
    return sent_today_user < caps["user_cap"] and sent_today_campaign_initial < caps["campaign_cap"]


def uazapi_initial_chunk_distribution_limits(
    campaign_daily_limit: int, n_instances: int
) -> tuple[int, int]:
    """
    TD-10 / AC-RULE-4: espelha `app._create_campaign_core` (~5116–5118) para o primeiro lote
    Uazapi por instância (ceil por instância, teto 30 por pasta na API).

    Retorna ``(per_instance_limit, total_limit)`` com ``total_limit == campaign_daily_limit``
    (quando o limite da campanha é válido; ver fallback abaixo).
    """
    n = max(1, int(n_instances))
    d = int(campaign_daily_limit)
    if d <= 0:
        d = 30
    per_instance = min(30, -(-d // n))
    return per_instance, d
