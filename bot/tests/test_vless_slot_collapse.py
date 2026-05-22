"""
VLESS slot collapse: legacy `plan.vless_slots` (например 5 для vpn_max)
не должен плодить лишние empty config-rows.

_resolve_vless_urls строит sub_url из ОДНОГО `users.vless_uuid` × N active
VLESS-серверов. Если plan_slots > N_servers, лишние rows никогда не
заполняются ни bootstrap_vless_for_sub, ни user-driven activate'ом —
просто мусор в БД и confusing-noise в Mini App («VLESS slot 3/5» без сервера).

Здесь два контракта:
  1. bootstrap_vless_for_sub: после provisioning N peers — удаляет excess empty rows
  2. admin_grant_subscription: pre-create только min(plan_slots, N_servers) rows
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.database import (
    create_config_record,
    create_subscription,
    upsert_user,
)


@dataclass
class _FakePeer:
    id: str
    config: str


async def _make_grant_sub(user_id: int, plan: str, vless_slots_in_db: int) -> int:
    """Создаёт user + sub + N empty VLESS-rows напрямую (без admin_grant логики —
    эмулируем «как было раньше» когда rows плодились без оглядки на N серверов)."""
    await upsert_user(user_id, f"u{user_id}", f"u{user_id}")
    sub_id = await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"slot_collapse_{user_id}_{vless_slots_in_db}",
        stars_paid=0,
        expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="gift",
    )
    for _ in range(vless_slots_in_db):
        await create_config_record(sub_id, user_id, protocol="vless")
    return sub_id


def _fake_server(srv_id: int, name: str):
    return {"id": srv_id, "name": name, "agent_url": "http://x",
            "flag": "🇳🇱", "city": name}


async def _count_vless_rows(sub_id: int) -> tuple[int, int]:
    """Returns (active_count, empty_count) для VLESS-rows этой sub."""
    from services.database import get_configs_for_subscription_by_protocol
    cfgs = await get_configs_for_subscription_by_protocol(sub_id, "vless")
    active = sum(1 for c in cfgs if c["status"] == "active")
    empty  = sum(1 for c in cfgs if c["status"] == "empty")
    return active, empty


def _make_fake_save():
    """save_peer_to_config-mock: реальный INSERT падает на FK serv, поэтому
    помечаем slot 'active' прямым UPDATE — без peer_name/server_id чтобы FK
    не возникал. Этого хватает для collapse-логики которая смотрит status."""
    import aiosqlite
    from services.database import DB_PATH

    async def fake_save(cfg_id, server_id, peer_id, assigned_ip,
                         config_data, peer_name, *, vless_uuid=None):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE configs SET status='active', vless_uuid=? WHERE id=?",
                (vless_uuid or peer_id, cfg_id),
            )
            await db.commit()
    return fake_save


# ── bootstrap_vless_for_sub: collapse excess ─────────────────────────────────

@pytest.mark.asyncio
async def test_bootstrap_collapses_excess_empty_when_slots_exceed_servers(
    fresh_db, monkeypatch,
):
    """5 empty slots в БД + 2 active servers → 2 active rows + 3 удалённых
    (итого 0 empty). Sub-URL юзера всё равно покрывает 2 локации."""
    from services import trial

    user_id = 5001
    sub_id = await _make_grant_sub(user_id, "vpn_max", vless_slots_in_db=5)
    assert (await _count_vless_rows(sub_id)) == (0, 5)

    servers = [_fake_server(11, "AMS"), _fake_server(14, "Charlotte")]
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=servers))

    async def fake_provision(server, label, tier, peer_id=None):
        return _FakePeer(id=peer_id, config=f"vless://{peer_id}@srv{server['id']}")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    # save_peer_to_config делает INSERT/UPDATE с FK на servers — в тесте
    # реальной server-row нет. Мокаем но руками UPDATE'им status='active'
    # чтобы _count_vless_rows и collapse-логика видели реальный state.
    monkeypatch.setattr(trial, "save_peer_to_config", _make_fake_save())

    n = await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert n == 2, f"должно провижить 2 peer'а, получили {n}"
    active, empty = await _count_vless_rows(sub_id)
    assert active == 2, f"active rows: {active}"
    assert empty == 0, (
        f"excess empty rows не удалены: {empty} осталось. "
        f"Должны были схлопнуться до N_servers=2."
    )


@pytest.mark.asyncio
async def test_bootstrap_no_excess_when_slots_match_servers(
    fresh_db, monkeypatch,
):
    """2 empty + 2 servers → 2 active + 0 empty (ничего не удаляем)."""
    from services import trial

    user_id = 5002
    sub_id = await _make_grant_sub(user_id, "vpn_max", vless_slots_in_db=2)

    servers = [_fake_server(11, "AMS"), _fake_server(14, "Charlotte")]
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=servers))
    async def fake_provision(server, label, tier, peer_id=None):
        return _FakePeer(id=peer_id, config=f"vless://{peer_id}@srv{server['id']}")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    # save_peer_to_config делает INSERT/UPDATE с FK на servers — в тесте
    # реальной server-row нет. Мокаем но руками UPDATE'им status='active'
    # чтобы _count_vless_rows и collapse-логика видели реальный state.
    monkeypatch.setattr(trial, "save_peer_to_config", _make_fake_save())

    n = await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert n == 2
    active, empty = await _count_vless_rows(sub_id)
    assert (active, empty) == (2, 0), f"got active={active} empty={empty}"


@pytest.mark.asyncio
async def test_bootstrap_more_servers_than_slots_keeps_all(
    fresh_db, monkeypatch,
):
    """2 empty + 5 servers → 2 active + 0 empty.
    Excess servers (3 шт.) НЕ заводят новые rows — это бы было over-engineering.
    bootstrap идёт `min(servers, empty_slots)`, и удалять нечего."""
    from services import trial

    user_id = 5003
    sub_id = await _make_grant_sub(user_id, "vpn_max", vless_slots_in_db=2)

    servers = [_fake_server(11 + i, f"srv{i}") for i in range(5)]
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=servers))
    async def fake_provision(server, label, tier, peer_id=None):
        return _FakePeer(id=peer_id, config=f"vless://{peer_id}@srv{server['id']}")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    # save_peer_to_config делает INSERT/UPDATE с FK на servers — в тесте
    # реальной server-row нет. Мокаем но руками UPDATE'им status='active'
    # чтобы _count_vless_rows и collapse-логика видели реальный state.
    monkeypatch.setattr(trial, "save_peer_to_config", _make_fake_save())

    n = await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert n == 2  # ровно по числу empty slots
    active, empty = await _count_vless_rows(sub_id)
    assert (active, empty) == (2, 0)


@pytest.mark.asyncio
async def test_bootstrap_zero_servers_no_delete(fresh_db, monkeypatch):
    """0 active VLESS-серверов → bootstrap early-return'ит ДО collapse-блока,
    rows остаются — мы не должны удалять данные просто потому что серверов нет
    (может это transient outage, через час добавят сервер обратно)."""
    from services import trial

    user_id = 5004
    sub_id = await _make_grant_sub(user_id, "vpn_max", vless_slots_in_db=5)

    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=[]))
    # alert админу — мокаем чтобы не падать
    monkeypatch.setattr(trial, "_alert_admin_bootstrap_failed", AsyncMock())

    n = await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert n == 0
    active, empty = await _count_vless_rows(sub_id)
    assert (active, empty) == (0, 5), (
        f"при N_servers=0 rows должны остаться нетронутыми (transient outage), "
        f"got active={active} empty={empty}"
    )


# ── admin_grant_subscription: не плодить лишние rows ────────────────────────

@pytest.mark.asyncio
async def test_admin_grant_caps_vless_rows_at_active_server_count(
    fresh_db, monkeypatch,
):
    """admin_grant с vpn_max (vless_slots=5) при 2 active VLESS-серверах
    создаёт ровно 2 пустых VLESS-row, не 5."""
    from services import database as db_mod

    monkeypatch.setattr(
        db_mod, "get_all_active_servers",
        AsyncMock(side_effect=lambda proto: [
            _fake_server(11, "AMS"), _fake_server(14, "Charlotte"),
        ] if proto == "vless" else []),
    )

    user_id = 5005
    result = await db_mod.admin_grant_subscription(
        admin_id=12345, target_user_id=user_id,
        plan_key="vpn_max", days=30, reason="test",
        target_username="testgrant",
    )
    assert result["action"] == "created"
    sub_id = result["subscription_id"]

    active, empty = await _count_vless_rows(sub_id)
    # Никаких active peers — admin_grant создаёт только пустые rows
    assert (active, empty) == (0, 2), (
        f"должно быть 2 пустых VLESS-row (= N active servers), "
        f"получили active={active} empty={empty}"
    )


@pytest.mark.asyncio
async def test_admin_grant_creates_zero_vless_when_no_active_servers(
    fresh_db, monkeypatch,
):
    """Если active VLESS-серверов нет — VLESS-rows вообще не создаём.
    Когда сервер добавят и админ перезапустит bootstrap — peer'ы появятся."""
    from services import database as db_mod

    monkeypatch.setattr(
        db_mod, "get_all_active_servers",
        AsyncMock(return_value=[]),
    )

    user_id = 5006
    result = await db_mod.admin_grant_subscription(
        admin_id=12345, target_user_id=user_id,
        plan_key="vpn_max", days=30, reason="test",
        target_username="testgrant2",
    )
    sub_id = result["subscription_id"]

    active, empty = await _count_vless_rows(sub_id)
    assert (active, empty) == (0, 0), (
        f"при 0 active VLESS-серверах VLESS-rows вообще не нужны: "
        f"active={active} empty={empty}"
    )


@pytest.mark.asyncio
async def test_admin_grant_awg_rows_unchanged(fresh_db, monkeypatch):
    """AWG rows не затронуты VLESS-cap логикой: плановое количество, как раньше."""
    from services import database as db_mod

    monkeypatch.setattr(
        db_mod, "get_all_active_servers",
        AsyncMock(side_effect=lambda proto: [
            _fake_server(11, "AMS"),
        ] if proto == "vless" else []),
    )

    user_id = 5007
    result = await db_mod.admin_grant_subscription(
        admin_id=12345, target_user_id=user_id,
        plan_key="vpn_max", days=30, reason="test",
        target_username="testgrant3",
    )
    sub_id = result["subscription_id"]

    from services.database import get_configs_for_subscription_by_protocol
    awg_cfgs = await get_configs_for_subscription_by_protocol(sub_id, "awg")
    # vpn_max имеет awg_slots=3
    assert len(awg_cfgs) == 3, f"AWG rows: {len(awg_cfgs)} (ожидали 3)"
    assert all(c["status"] == "empty" for c in awg_cfgs)


@pytest.mark.asyncio
async def test_admin_grant_extend_path_does_not_touch_rows(
    fresh_db, monkeypatch,
):
    """Если у юзера уже есть active sub того же плана — extend, НЕ create.
    Существующие VLESS rows не пересоздаются (даже если N серверов изменилось)."""
    from services import database as db_mod

    monkeypatch.setattr(
        db_mod, "get_all_active_servers",
        AsyncMock(side_effect=lambda proto: [
            _fake_server(11, "AMS"),
        ] if proto == "vless" else []),
    )

    user_id = 5008
    # Первый grant — создаёт sub + 1 VLESS-row (1 active server)
    r1 = await db_mod.admin_grant_subscription(
        admin_id=12345, target_user_id=user_id,
        plan_key="vpn_max", days=30, reason="initial",
        target_username="testgrant4",
    )
    sub_id = r1["subscription_id"]
    assert r1["action"] == "created"
    active_before, empty_before = await _count_vless_rows(sub_id)
    assert (active_before, empty_before) == (0, 1)

    # Второй grant того же плана — extend, не create.
    r2 = await db_mod.admin_grant_subscription(
        admin_id=12345, target_user_id=user_id,
        plan_key="vpn_max", days=30, reason="extend",
        target_username="testgrant4",
    )
    assert r2["action"] == "extended"
    assert r2["subscription_id"] == sub_id

    active_after, empty_after = await _count_vless_rows(sub_id)
    assert (active_after, empty_after) == (active_before, empty_before), (
        "extend не должен трогать существующие config-rows"
    )
