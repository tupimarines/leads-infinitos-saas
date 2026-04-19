"""Política de planos: asserts mínimos para starter_trial (tech-spec provisionamento)."""

from utils.limits import get_plan_policy


def test_get_plan_policy_starter_trial_limits():
    policy = get_plan_policy("starter_trial")
    assert policy["instance_limit"] == 1
    assert policy["monthly_extraction_limit"] == 100
    assert policy["daily_sends_per_instance_default"] == 10
