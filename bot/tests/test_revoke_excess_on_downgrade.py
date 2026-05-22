"""
Regression: revoke_excess_configs_on_downgrade (services/revoke.py) —
после apply_pending_plan_change на DOWNGRADE-сценарии удаляет лишние
config-rows до нового plan target.

Сценарий: юзер на vpn_max (3 AWG / 5 VLESS) поставил pending=vpn_base
(2 AWG / 1 VLESS). Sub истекает → apply_pending → revoke_excess убирает
1 AWG и 4 VLESS. Empty rows deleted; active peers — agent revoke + delete.

Стратегия per protocol:
  - empty rows первыми (без agent-вызовов — cheap)
  - дальше oldest active (юзер уже привык к ним меньше всего)
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import services.database as _db_mod
from services.database import (
    create_config_record,
    create_subscription,
    upsert_user,
)


USER_ID = 9301


async def _activate_cfg(cfg_id: int, server_id: int):
    """Прямой UPDATE (FK на servers выключаем чтобы избежать setup-сложности)."""
    import aiosqlite
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.execute(
            "UPDATE configs SET status='active', server_id=?, "
            "vless_uuid=?, peer_name=? WHERE id=?",
            (server_id,
             "aaaaaaaa-1111-2222-3333-444444444444",
             f"peer_{cfg_id}", cfg_id),
        )
        await db.commit()


async def _setup_max_sub(awg_active: int, awg_empty: int,
                          vless_active: int, vless_empty: int) -> int:
    """Создаёт vpn_max sub с заданными количествами active/empty rows."""
    await upsert_user(USER_ID, "downg_user", "Downgrader")
    sub_id = await create_subscription(
        user_id=USER_ID, plan="vpn_max",
        payment_id=f"chg_{USER_ID}_max_{awg_active}_{vless_active}",
        stars_paid=450,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    # AWG
    for _ in range(awg_active):
        cfg_id = await create_config_record(sub_id, USER_ID, protocol="awg")
        await _activate_cfg(cfg_id, server_id=8)
    for _ in range(awg_empty):
        await create_config_record(sub_id, USER_ID, protocol="awg")
    # VLESS
    for _ in range(vless_active):
        cfg_id = await create_config_record(sub_id, USER_ID, protocol="vless")
        await _activate_cfg(cfg_id, server_id=11)
    for _ in range(vless_empty):
        await create_config_record(sub_id, USER_ID, protocol="vless")
    return sub_id


async def _count(sub_id: int) -> dict[str, dict[str, int]]:
    from services.database import get_configs_for_subscription_by_protocol
    res: dict[str, dict[str, int]] = {}
    for proto in ("awg", "vless"):
        cfgs = await get_configs_for_subscription_by_protocol(sub_id, proto)
        res[proto] = {
            "active": sum(1 for c in cfgs if c["status"] == "active"),
            "empty":  sum(1 for c in cfgs if c["status"] == "empty"),
        }
    return res


def _patch_agent_ok(monkeypatch):
    """Стабит agent calls. revoke.py делает top-level импорт
    `from services.vpnctl_client import client_for_server` и
    `from services.database import get_server_by_id` — патчим на
    services.revoke namespace, не на source-модули."""
    fake_client = type("C", (), {})()
    fake_client.remove_peer = AsyncMock(return_value=None)
    fake_client.unthrottle_peer = AsyncMock(return_value=None)

    import services.revoke as revoke_mod
    monkeypatch.setattr(revoke_mod, "client_for_server",
                         lambda srv: fake_client)
    monkeypatch.setattr(revoke_mod, "get_server_by_id",
                         AsyncMock(return_value={"id": 8, "agent_url": "http://x"}))
    return fake_client


# ── empty rows revoked first (cheap, no agent) ──────────────────────────────

@pytest.mark.asyncio
async def test_downgrade_max_to_base_removes_excess_empty_first(
    fresh_db, monkeypatch,
):
    """vpn_max (3 AWG / 5 VLESS) → vpn_base (target 2/1).
    Excess: 1 AWG + 4 VLESS. У нас 3 empty AWG + 5 empty VLESS → всё empty,
    агент НЕ зовётся."""
    sub_id = await _setup_max_sub(
        awg_active=0, awg_empty=3, vless_active=0, vless_empty=5,
    )
    fake_client = _patch_agent_ok(monkeypatch)

    from services.revoke import revoke_excess_configs_on_downgrade
    revoked, failed = await revoke_excess_configs_on_downgrade(
        sub_id, "vpn_max", "vpn_base",
    )

    # vpn_max: awg=3, vless=5; vpn_base: 2/1; excess = 1 AWG + 4 VLESS = 5
    assert (revoked, failed) == (5, 0)
    assert fake_client.remove_peer.await_count == 0, (
        "agent НЕ должен звать для empty-rows"
    )

    final = await _count(sub_id)
    assert final["awg"] == {"active": 0, "empty": 2}
    assert final["vless"] == {"active": 0, "empty": 1}


# ── active rows revoked when empty pool exhausted ───────────────────────────

@pytest.mark.asyncio
async def test_downgrade_revokes_active_when_empty_exhausted(
    fresh_db, monkeypatch,
):
    """vpn_max с 3 active AWG, 0 empty → downgrade в vpn_base (target 2 AWG)
    → 1 active AWG нужно revoke через агент."""
    # 3 AWG active + 1 VLESS active (vless_empty=0!) — оба будут revoke'ться
    # через agent (excess AWG=1 → 1 active, excess VLESS=4 → 1 active row).
    sub_id = await _setup_max_sub(
        awg_active=3, awg_empty=0, vless_active=1, vless_empty=0,
    )
    fake_client = _patch_agent_ok(monkeypatch)

    from services.revoke import revoke_excess_configs_on_downgrade
    revoked, failed = await revoke_excess_configs_on_downgrade(
        sub_id, "vpn_max", "vpn_base",
    )

    # vpn_max awg=3 → vpn_base awg=2 → excess AWG=1 (1 active → agent revoke)
    # vpn_max vless=5 → vpn_base vless=1 → excess VLESS=4, но у нас 1 active.
    # → 2 active revoke = 2 agent calls.
    assert revoked == 2, f"revoked={revoked} failed={failed}"
    assert failed == 0
    assert fake_client.remove_peer.await_count == 2, (
        f"agent.remove_peer должен быть вызван 2 раза (1 awg + 1 vless), "
        f"got {fake_client.remove_peer.await_count}"
    )


# ── upgrade — no-op ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upgrade_is_no_op(fresh_db, monkeypatch):
    """vpn_base → vpn_max — new_count > old_count для всех протоколов →
    revoke ничего не делает."""
    sub_id = await _setup_max_sub(
        awg_active=0, awg_empty=2, vless_active=0, vless_empty=1,
    )
    # plan-key sub нерелевантен — функция использует именно old_plan_key arg
    fake_client = _patch_agent_ok(monkeypatch)

    from services.revoke import revoke_excess_configs_on_downgrade
    revoked, failed = await revoke_excess_configs_on_downgrade(
        sub_id, "vpn_base", "vpn_max",
    )
    assert (revoked, failed) == (0, 0)
    assert fake_client.remove_peer.await_count == 0


# ── unknown plan_key — safe no-op ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_plan_key_no_op(fresh_db, monkeypatch):
    """Если plan_key не найден в VPN_PLANS — функция не упадёт, return (0,0)."""
    sub_id = await _setup_max_sub(
        awg_active=0, awg_empty=3, vless_active=0, vless_empty=5,
    )
    _patch_agent_ok(monkeypatch)

    from services.revoke import revoke_excess_configs_on_downgrade
    revoked, failed = await revoke_excess_configs_on_downgrade(
        sub_id, "vpn_made_up", "vpn_also_made_up",
    )
    assert (revoked, failed) == (0, 0)
