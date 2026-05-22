"""
Regression: ensure_empty_slots_match_plan (services/database.py) —
после apply_pending_plan_change на UPGRADE-сценарии достраивает недостающие
empty config-rows до target_counts нового плана.

Сценарий: юзер на vpn_base (2 AWG / 1 VLESS) поставил pending=vpn_max
(3 AWG / 5 VLESS). Sub истекает → scheduler делает apply_pending → plan
теперь vpn_max, но configs остались 2/1. Юзер платит за max, видит 2+1
вместо 3+5. Helper создаёт +1 AWG row для покрытия.

Note: VLESS row count cap'нут предыдущим фиксом (collapse) до min(plan_slots,
N_active_servers), это покрыто отдельно в test_vless_slot_collapse.py.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import services.database as _db_mod
from services.database import (
    create_config_record,
    create_subscription,
    ensure_empty_slots_match_plan,
    upsert_user,
)


USER_ID = 9501


async def _make_sub_with_slots(plan: str, awg_n: int, vless_n: int) -> int:
    """User + sub + N AWG + N VLESS empty rows."""
    await upsert_user(USER_ID, "ensure_user", "Ensure User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"ensure_{plan}_{awg_n}_{vless_n}_{datetime.utcnow().timestamp()}",
        stars_paid=0,
        expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="gift",
    )
    for _ in range(awg_n):
        await create_config_record(sub_id, USER_ID, protocol="awg")
    for _ in range(vless_n):
        await create_config_record(sub_id, USER_ID, protocol="vless")
    return sub_id


async def _count(sub_id: int) -> dict[str, int]:
    from services.database import get_configs_for_subscription_by_protocol
    awg = await get_configs_for_subscription_by_protocol(sub_id, "awg")
    vle = await get_configs_for_subscription_by_protocol(sub_id, "vless")
    return {"awg": len(awg), "vless": len(vle)}


@pytest.mark.asyncio
async def test_upgrade_creates_missing_rows_to_target(fresh_db):
    """vpn_base (2 AWG / 1 VLESS) → vpn_max (target 3 AWG / 5 VLESS).
    После ensure: +1 AWG + 4 VLESS.

    Note: ensure_empty_slots_match_plan НЕ cap'ает VLESS до N_servers —
    это работа bootstrap_vless_for_sub который запускается ниже по pipeline
    и удалит excess. На промежуточном шаге у sub может быть rows > N_servers,
    но это OK (юзер не видит сырые rows в Mini App до bootstrap'а)."""
    sub_id = await _make_sub_with_slots("vpn_base", awg_n=2, vless_n=1)
    assert (await _count(sub_id)) == {"awg": 2, "vless": 1}

    created = await ensure_empty_slots_match_plan(sub_id, USER_ID, "vpn_max")

    # vpn_max: awg=3, vless=5; current 2/1 → +1 awg, +4 vless
    assert created == {"awg": 1, "vless": 4}, (
        f"helper должен довести rows до target: got {created}"
    )
    final = await _count(sub_id)
    assert final == {"awg": 3, "vless": 5}, f"final state: {final}"


@pytest.mark.asyncio
async def test_downgrade_no_inserts(fresh_db, monkeypatch):
    """vpn_max (3/5) → vpn_base (target 2/1). Delta отрицательная — ensure
    НЕ удаляет лишние, просто no-op. Down-сценарий ловится отдельным
    revoke_excess_configs_on_downgrade в scheduler."""
    sub_id = await _make_sub_with_slots("vpn_max", awg_n=3, vless_n=5)
    assert (await _count(sub_id)) == {"awg": 3, "vless": 5}

    monkeypatch.setattr(
        _db_mod, "get_all_active_servers",
        AsyncMock(side_effect=lambda proto: (
            [{"id": 11, "name": "AMS"}] if proto == "vless" else []
        )),
    )

    created = await ensure_empty_slots_match_plan(sub_id, USER_ID, "vpn_base")

    assert created == {}, f"downgrade — ничего не создаём, got {created}"
    final = await _count(sub_id)
    assert final == {"awg": 3, "vless": 5}, (
        f"helper не должен трогать лишние rows: final={final}"
    )


@pytest.mark.asyncio
async def test_already_at_target_no_op(fresh_db, monkeypatch):
    """vpn_base (target 2/1) уже имеет 2/1 → no-op."""
    sub_id = await _make_sub_with_slots("vpn_base", awg_n=2, vless_n=1)

    monkeypatch.setattr(
        _db_mod, "get_all_active_servers",
        AsyncMock(side_effect=lambda proto: (
            [{"id": 11, "name": "AMS"}] if proto == "vless" else []
        )),
    )

    created = await ensure_empty_slots_match_plan(sub_id, USER_ID, "vpn_base")

    assert created == {}, f"already at target → 0 inserts, got {created}"
    final = await _count(sub_id)
    assert final == {"awg": 2, "vless": 1}


@pytest.mark.asyncio
async def test_unknown_plan_returns_empty(fresh_db):
    """Если plan_key не в VPN_PLANS — helper возвращает {} (no-op)."""
    sub_id = await _make_sub_with_slots("vpn_base", awg_n=2, vless_n=1)

    created = await ensure_empty_slots_match_plan(
        sub_id, USER_ID, "vpn_completely_made_up",
    )
    assert created == {}
    final = await _count(sub_id)
    assert final == {"awg": 2, "vless": 1}, "rows не тронуты при unknown plan"
