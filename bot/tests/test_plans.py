"""
E. Plan-lookup tests.

Freezes that:
  - both VPN_PLANS dicts (handlers/vpn.py and services/webapp_api.py) agree on
    the v2 tariff keys (vpn_base, vpn_max)
  - vless_service_for_plan / vless_slow_service_for_plan map correctly
"""
import pytest

from handlers.vpn import (
    VPN_PLANS as PLANS_BOT,
    vless_service_for_plan,
    vless_slow_service_for_plan,
)
from services.webapp_api import VPN_PLANS as PLANS_API


V2_KEYS = {"vpn_base", "vpn_max"}


def test_E1_v2_plan_keys_match_across_modules():
    """E1. Both dicts contain the v2 keys. Documents duplicate-source-of-truth smell."""
    assert V2_KEYS.issubset(set(PLANS_BOT.keys())), \
        f"handlers.vpn.VPN_PLANS missing v2 keys: {V2_KEYS - set(PLANS_BOT.keys())}"
    assert V2_KEYS.issubset(set(PLANS_API.keys())), \
        f"services.webapp_api.VPN_PLANS missing v2 keys: {V2_KEYS - set(PLANS_API.keys())}"


def test_E1_v2_plan_core_fields_match():
    """For each v2 key, common fields (stars, duration, slot counts) match across modules."""
    for k in V2_KEYS:
        a = PLANS_BOT[k]
        b = PLANS_API[k]
        for f in ("stars", "duration_days", "awg_slots", "vless_slots"):
            assert a[f] == b[f], (
                f"plan {k} field {f} mismatched: bot={a[f]} api={b[f]}"
            )


# 443-консолидация (28.05.2026): ВСЕ планы → vless-max (единый инбаунд на 443),
# т.к. не-443 порты режет РФ-мобильный DPI. См. services/plans.py.
@pytest.mark.parametrize("plan_key,expected", [
    ("vpn_base", "vless-max"),
    ("vpn_max",  "vless-max"),
    ("vpn_pro",  "vless-max"),
    ("vpn_family", "vless-max"),
    ("vpn_trial", "vless-max"),
    ("unknown_xyz", "vless-max"),
])
def test_E2_vless_service_for_plan(plan_key, expected):
    assert vless_service_for_plan(plan_key) == expected


@pytest.mark.parametrize("plan_key,expected", [
    ("vpn_base", "vless-max-slow"),
    ("vpn_max",  "vless-max-slow"),
    ("vpn_pro",  "vless-max-slow"),
    ("vpn_family", "vless-max-slow"),
    ("anything", "vless-max-slow"),
])
def test_E3_vless_slow_service_for_plan(plan_key, expected):
    assert vless_slow_service_for_plan(plan_key) == expected
