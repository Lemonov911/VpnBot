"""Регрешн-гард 443-консолидации (28.05.2026).

Баг, который этот тест ловит (найден code-review 28.05 после консолидации):
trial.py хардкодил `provision_peer(..., "vless-base", ...)` (порт 8443), а
подписка `_resolve_vless_urls` и revoke перешли на `vless_service_for_plan`
→ `vless-max` (443). Рассинхрон: пир в 8443-инбаунде, клиент коннектится на
443 → UUID не найден → Reality auth fail → EOF в Happ у каждого нового триала.

Инвариант который защищаем: для ЛЮБОГО плана (включая vpn_trial) сервис, в
который провижинится normal-пир, == сервис, по которому подписка строит URL,
== сервис, по которому revoke его удаляет. Все три берут из единого источника
`vless_service_for_plan`. Если кто-то снова захардкодит tier — тест упадёт.
"""
import pytest

from services.plans import vless_service_for_plan, vless_slow_service_for_plan
from services.revoke import current_vless_service
from services.trial import TRIAL_PLAN


ALL_PLANS = [
    "vpn_base", "vpn_max", "vpn_pro", "vpn_family",
    "vpn_base_3m", "vpn_max_12m", TRIAL_PLAN, "unknown_xyz",
]


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_normal_tier_is_max_for_all_plans(plan):
    """После консолидации normal-tier у всех планов = vless-max (единый 443)."""
    assert vless_service_for_plan(plan) == "vless-max"


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_revoke_matches_provision_tier_no_marker(plan):
    """current_vless_service (revoke) для конфига БЕЗ slow/grace-маркера
    обязан совпасть с тем, куда provision кладёт пир (vless_service_for_plan).
    Это и есть инвариант, поломка которого = orphan peer + counter drift."""
    cfg_normal = f"vless://uuid@host:443?security=reality&sni=www.microsoft.com"
    assert current_vless_service(cfg_normal, plan) == vless_service_for_plan(plan)


def test_trial_specifically_not_vless_base():
    """Прямой гард на исходный баг: триал НЕ должен резолвиться в vless-base."""
    cfg = "vless://uuid@host:443?security=reality"
    assert current_vless_service(cfg, TRIAL_PLAN) == "vless-max"
    assert vless_service_for_plan(TRIAL_PLAN) == "vless-max"


@pytest.mark.parametrize("marker,expected", [
    (":9448", "vless-max-slow"),
    (":9443", "vless-base-slow"),
    (":9453", "vless-grace"),
])
def test_port_markers_win_over_plan(marker, expected):
    """slow/grace-маркеры в config_data приоритетнее плана — иначе revoke
    throttled/grace-пира бил бы в normal-инбаунд (404, пир висит)."""
    cfg = f"vless://uuid@host{marker}?security=reality"
    # даже для триала маркер должен победить
    assert current_vless_service(cfg, TRIAL_PLAN) == expected
    assert current_vless_service(cfg, "vpn_max") == expected


@pytest.mark.parametrize("plan", ALL_PLANS)
def test_slow_tier_is_max_slow_for_all(plan):
    """Throttle-сервис тоже консолидирован: vless-max-slow для всех планов."""
    assert vless_slow_service_for_plan(plan) == "vless-max-slow"
