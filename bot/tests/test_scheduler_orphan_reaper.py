"""Tests for `_process_orphan_active_configs` — orphan reaper.

Контекст (audit 2026-05-23): после того как grace→expired transition атомарно
помечает sub expired, peer revoke на агенте может упасть (5xx/timeout). Старый
код безусловно reset'ил slot → orphan-peer на сервере. Новая логика оставляет
slot active при failed revoke; этот reaper retry'нет revoke на следующих тиках.

Покрывает:
  1. Happy path — orphan picked up, revoked, slot reset
  2. Legacy `server_id IS NULL` — log only, NOT reset (no reverse-orphan)
  3. Circuit breaker `dead_servers` — first VpnctlError on server X → skip rest
  4. Bool-gate — remove_peer returns False (404) → counter NOT decremented
  5. Empty snapshot — noop
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from services.database import (
    upsert_user,
    create_subscription,
    create_config_record,
    activate_config_slot,
    claim_config_slot_for_activation,
    mark_subscription_grace,
    mark_subscription_expired_from_grace,
    get_config_by_id,
)
from services.vpnctl_client import VpnctlError


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_with_server(fresh_db):
    async with aiosqlite.connect(fresh_db) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active, active_peers)
               VALUES ('Test', '1.2.3.4', 'http://agent:8080', 'tok', 1, 10)"""
        )
        await db.commit()
        return cur.lastrowid


async def _make_expired_sub(*, user_id: int, plan: str = "vpn_base") -> int:
    """Sub в status='expired' (post-grace). Это и есть state, который orphan-reaper
    подбирает (вместе с cfg.status='active')."""
    await upsert_user(user_id, "u", "U")
    sub_id = await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"t_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145,
        expires_at=datetime.utcnow() - timedelta(days=15),
    )
    gu = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    assert await mark_subscription_grace(sub_id, gu)
    assert await mark_subscription_expired_from_grace(sub_id)
    return sub_id


async def _make_active_awg(sub_id: int, server_id: int, *,
                            user_id: int,
                            peer_name: str = "p_orphan",
                            assigned_ip: str = "10.0.0.42") -> int:
    cfg_id = await create_config_record(sub_id, user_id, protocol="awg",
                                         server_id=server_id)
    assert await claim_config_slot_for_activation(cfg_id)
    assert await activate_config_slot(
        cfg_id, peer_name=peer_name, config_data="[Interface]\n...",
        server_id=server_id, assigned_ip=assigned_ip,
    )
    return cfg_id


def _mock_client():
    client = AsyncMock()
    client.unthrottle_peer = AsyncMock()
    client.remove_peer = AsyncMock(return_value=True)  # default 200 success
    return client


async def _read_active_peers(fresh_db: str, server_id: int) -> int:
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT active_peers FROM servers WHERE id=?", (server_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0]


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orphan_reaper_happy_path_revokes_and_resets(fresh_db, db_with_server):
    """Orphan cfg (status='active' под sub.status='expired') → reaper подбирает,
    дёргает remove_peer, декрементит counter, resetает slot."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=900)
    cfg_id = await _make_active_awg(sub_id, db_with_server, user_id=900)

    client = _mock_client()  # remove_peer → True (success)

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    # Slot сброшен
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty", "orphan cfg должен быть reset'нут после успешного revoke"

    # Peer удалён через агента
    client.remove_peer.assert_awaited_once_with("awg", "p_orphan")

    # Counter декрементирован один раз (был 10, стал 9)
    assert await _read_active_peers(fresh_db, db_with_server) == 9


@pytest.mark.asyncio
async def test_orphan_reaper_skips_decrement_when_remove_returns_false(
        fresh_db, db_with_server):
    """remove_peer → False (404, peer уже удалён ранее) → counter НЕ декрементим.
    Slot всё равно reset'им (БД-сторона убирает запись). Audit 2026-05-23:
    double-decrement prevention при idempotent retry."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=901)
    cfg_id = await _make_active_awg(sub_id, db_with_server, user_id=901,
                                     peer_name="p_already_gone")

    client = _mock_client()
    client.remove_peer = AsyncMock(return_value=False)  # 404 — peer уже не было

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty", "slot должен быть reset даже если peer уже был удалён"

    # Counter НЕ декрементирован (был 10, остался 10) — кто-то другой уже это сделал
    assert await _read_active_peers(fresh_db, db_with_server) == 10, (
        "counter не должен декрементиться при remove_peer=False (404)"
    )


@pytest.mark.asyncio
async def test_orphan_reaper_skips_legacy_null_server_id(fresh_db, db_with_server):
    """Cfg с server_id IS NULL (legacy SSH provisioned) — НЕ reset'им slot,
    error-log. Раньше делали безусловно `reset_config_slot` → reverse-orphan
    на железке (peer есть, БД чистая). Audit 2026-05-23: explicit skip."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=902)
    # Создаём cfg БЕЗ server_id (NULL) — legacy SSH-provisioned
    cfg_id = await create_config_record(sub_id, 902, protocol="awg",
                                         server_id=None)
    assert await claim_config_slot_for_activation(cfg_id)
    assert await activate_config_slot(
        cfg_id, peer_name="legacy_peer", config_data="[Interface]\n...",
        server_id=None, assigned_ip="10.99.0.1",
    )

    with patch("services.scheduler.client_for_server") as mock_factory:
        await _process_orphan_active_configs()
        # Никакой agent client не должен быть создан (server_id IS NULL)
        mock_factory.assert_not_called()

    # CRITICAL: slot НЕ reset — иначе создали бы reverse-orphan
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "active", (
        "legacy cfg с server_id IS NULL должен ОСТАТЬСЯ active — "
        "reset → peer на железке без БД-записи (reverse-orphan)"
    )


@pytest.mark.asyncio
async def test_orphan_reaper_circuit_breaker_skips_rest_of_dead_server(
        fresh_db, db_with_server):
    """Первый VpnctlError на сервере X → добавляем в dead_servers, остальные
    cfg на том же сервере skip'аем в этом тике. Защита от storm: 100 cfg ×
    30s timeout = 50 мин wall-clock без этого."""
    from services.scheduler import _process_orphan_active_configs

    # Делаем 3 orphan-cfg на одном сервере
    sub_id = await _make_expired_sub(user_id=903)
    cfg1 = await _make_active_awg(sub_id, db_with_server, user_id=903,
                                   peer_name="p1", assigned_ip="10.0.0.51")
    cfg2 = await _make_active_awg(sub_id, db_with_server, user_id=903,
                                   peer_name="p2", assigned_ip="10.0.0.52")
    cfg3 = await _make_active_awg(sub_id, db_with_server, user_id=903,
                                   peer_name="p3", assigned_ip="10.0.0.53")

    client = _mock_client()
    # Все remove_peer вызовы кидают VpnctlError (агент полностью down)
    client.remove_peer = AsyncMock(side_effect=VpnctlError("connection refused"))

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    # Только ОДИН remove_peer call (на первом cfg) — после которого
    # server добавлен в dead_servers и остальные skip'нуты
    assert client.remove_peer.await_count == 1, (
        f"После первого VpnctlError circuit-breaker должен skip'нуть остальные "
        f"orphan'ы на этом сервере; got {client.remove_peer.await_count} calls"
    )

    # Ни один cfg НЕ reset'нут — все ждут next-tick retry
    for cfg_id in (cfg1, cfg2, cfg3):
        cfg = await get_config_by_id(cfg_id)
        assert cfg["status"] == "active", (
            f"cfg #{cfg_id} должен остаться active (failed revoke + retry на след. тике)"
        )


@pytest.mark.asyncio
async def test_orphan_reaper_noop_when_no_orphans(fresh_db, db_with_server):
    """Без orphan'ов reaper ничего не делает — нет agent calls, нет log spam."""
    from services.scheduler import _process_orphan_active_configs

    with patch("services.scheduler.client_for_server") as mock_factory:
        await _process_orphan_active_configs()
        mock_factory.assert_not_called()


# ── VLESS path coverage ──────────────────────────────────────────────────────

async def _make_active_vless(sub_id: int, server_id: int, *,
                              user_id: int,
                              vless_uuid: str = "uuid-orphan-1",
                              port_marker: str = ":8443") -> int:
    """Создаёт active VLESS cfg. `port_marker` определяет какой inbound будет
    выбран `_current_vless_service` (`:8443` → vless-base, `:9453` → vless-grace
    и т.д.). Это важно для orphan'ов на throttled/grace-tier."""
    cfg_id = await create_config_record(sub_id, user_id, protocol="vless",
                                         server_id=server_id)
    assert await claim_config_slot_for_activation(cfg_id)
    config_data = f"vless://{vless_uuid}@1.2.3.4{port_marker}?sni=yandex.ru&type=tcp"
    assert await activate_config_slot(
        cfg_id, peer_name=f"vless_{vless_uuid[:6]}",
        config_data=config_data, server_id=server_id, assigned_ip="",
    )
    # Set vless_uuid отдельно — activate_config_slot не принимает его
    async with aiosqlite.connect(fresh_db_path_holder["path"]) as db:
        await db.execute(
            "UPDATE configs SET vless_uuid=? WHERE id=?", (vless_uuid, cfg_id),
        )
        await db.commit()
    return cfg_id


# Helper для shared state — fresh_db path; pytest-asyncio fixture scope hack
fresh_db_path_holder = {"path": None}


@pytest_asyncio.fixture(autouse=True)
async def _capture_fresh_db_path(fresh_db):
    fresh_db_path_holder["path"] = fresh_db
    yield


@pytest.mark.asyncio
async def test_orphan_reaper_vless_happy_path(fresh_db, db_with_server):
    """VLESS orphan: reaper routes через `current_vless_service`. После
    443-консолидации (28.05) normal-тир всегда → vless-max (единый 443-инбаунд),
    независимо от стейл-маркера :8443 в config_data. Slow/grace-маркеры
    (:9443/:9448/:9453) детектятся отдельно — см. test ниже."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=910)
    cfg_id = await _make_active_vless(sub_id, db_with_server, user_id=910,
                                       vless_uuid="vless-uuid-base",
                                       port_marker=":8443")

    client = _mock_client()  # remove_peer → True

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    # Normal-тир → vless-max (443-консолидация; пир смержен в vless-max инбаунд)
    client.remove_peer.assert_awaited_once_with("vless-max", "vless-uuid-base")

    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"
    assert await _read_active_peers(fresh_db, db_with_server) == 9


@pytest.mark.asyncio
async def test_orphan_reaper_vless_grace_tier_routing(fresh_db, db_with_server):
    """VLESS orphan на grace-tier (port :9453): reaper не должен попасть
    в vless-base — peer там не зарегистрирован, agent вернёт 404, peer
    останется в vless-grace навсегда. `current_vless_service` правильно
    мапит порт → vless-grace."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=911)
    await _make_active_vless(sub_id, db_with_server, user_id=911,
                              vless_uuid="vless-uuid-grace",
                              port_marker=":9453")  # grace-tier port

    client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    # CRITICAL: должен попасть в vless-grace, не vless-base
    client.remove_peer.assert_awaited_once_with("vless-grace", "vless-uuid-grace")


@pytest.mark.asyncio
async def test_orphan_reaper_vless_bool_gate_on_404(fresh_db, db_with_server):
    """VLESS path: remove_peer → False (404) → counter НЕ декремент.
    Аналог AWG-теста для VLESS branch (раздельные ветки protocol == 'awg'
    vs protocol in ('vless','vless-reality'))."""
    from services.scheduler import _process_orphan_active_configs

    sub_id = await _make_expired_sub(user_id=912)
    cfg_id = await _make_active_vless(sub_id, db_with_server, user_id=912,
                                       vless_uuid="ghost-vless-uuid")

    client = _mock_client()
    client.remove_peer = AsyncMock(return_value=False)  # 404

    with patch("services.scheduler.client_for_server", return_value=client):
        await _process_orphan_active_configs()

    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty", "slot reset даже когда peer уже был удалён"
    assert await _read_active_peers(fresh_db, db_with_server) == 10, (
        "counter НЕ должен декрементиться при remove_peer=False в VLESS branch"
    )
