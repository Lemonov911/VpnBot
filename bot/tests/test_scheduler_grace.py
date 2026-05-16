"""
Scheduler grace/expiry flows — integration tests with mocked VPN agent.

Tests call _process_expired_subscriptions and _process_grace_expired_subscriptions
directly.  The VPN agent (VpnctlClient) and Telegram bot.send_message are
replaced with AsyncMocks so no real network calls happen.

DB is real (in-memory SQLite via fresh_db fixture) so we can assert state
transitions without mocking the persistence layer.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import aiosqlite

from services.database import (
    create_subscription,
    create_config_record,
    activate_config_slot,
    get_subscription_by_id,
    get_config_by_id,
    get_grace_expired_subscriptions,
    mark_subscription_grace,
)

PAST    = (datetime.utcnow() - timedelta(days=1)).isoformat()
LONG_AGO = (datetime.utcnow() - timedelta(days=20)).isoformat()  # > GRACE_DAYS=14


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_with_server(fresh_db, monkeypatch):
    """Расширяет fresh_db: вставляет тестовый сервер с agent_url.
    Возвращает server_id."""
    import services.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", fresh_db)

    async with aiosqlite.connect(fresh_db) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active)
               VALUES ('Test', '1.2.3.4', 'http://agent:8080', 'tok', 1)"""
        )
        await db.commit()
        return cur.lastrowid


async def _make_sub(user_id: int, expires_at: str, plan: str = "vpn_trial") -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"t_{user_id}_{expires_at[:10]}",
        stars_paid=0, expires_at=datetime.fromisoformat(expires_at),
    )


async def _make_active_awg(sub_id: int, server_id: int,
                            user_id: int = 1,
                            assigned_ip: str = "10.0.0.2") -> int:
    cfg_id = await create_config_record(sub_id, user_id, protocol="awg",
                                         server_id=server_id)
    await activate_config_slot(
        cfg_id, peer_name=f"peer_{cfg_id}",
        config_data="[Interface]\n...",
        server_id=server_id, assigned_ip=assigned_ip,
    )
    return cfg_id


async def _make_active_vless(sub_id: int, server_id: int,
                              user_id: int = 1,
                              vless_uuid: str = "uuid-0001",
                              service: str = "vless-base") -> int:
    # _current_vless_service() detects the inbound by port marker in config_data:
    #   :9453 → vless-grace, :9443 → vless-base-slow, :9448 → vless-max-slow
    # Anything else falls through to vless_service_for_plan(plan_key).
    _port_for_service = {
        "vless-grace": ":9453",
        "vless-base-slow": ":9443",
        "vless-max-slow": ":9448",
    }
    config_data = _port_for_service.get(service, f"vless://{service}.example.com/")
    cfg_id = await create_config_record(sub_id, user_id, protocol="vless",
                                         server_id=server_id)
    await activate_config_slot(
        cfg_id, peer_name=f"vless_{cfg_id}",
        config_data=config_data,
        server_id=server_id, vless_uuid=vless_uuid,
    )
    return cfg_id


def _fake_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _mock_client():
    """Returns an AsyncMock that mimics VpnctlClient."""
    client = AsyncMock()
    client.throttle_peer = AsyncMock()
    client.unthrottle_peer = AsyncMock()
    client.remove_peer = AsyncMock()
    client.add_peer = AsyncMock()
    # add_peer returns an object with .config attr (used to update config_data)
    client.add_peer.return_value = MagicMock(config="service:vless-grace")
    return client


# ── AWG: active → grace ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awg_expiry_throttle_called_and_sub_goes_to_grace(
        fresh_db, db_with_server):
    """When an AWG sub expires: throttle_peer must be called and the sub
    must transition to status='grace'."""
    server_id = db_with_server
    sub_id = await _make_sub(user_id=1, expires_at=PAST)
    await _make_active_awg(sub_id, server_id, assigned_ip="10.0.0.2")

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    mock_client.throttle_peer.assert_awaited_once()
    call_args = mock_client.throttle_peer.await_args
    assert call_args.args[0] == "awg"        # protocol
    assert call_args.kwargs.get("kbps") == 256

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"
    assert sub["grace_until"] is not None


@pytest.mark.asyncio
async def test_awg_expiry_no_server_still_marks_grace(fresh_db, db_with_server):
    """Config with no server_id (slot never activated on agent): sub still
    transitions to grace — throttle failure must not block the state change."""
    sub_id = await _make_sub(user_id=2, expires_at=PAST)
    # config with server_id=None (empty slot that was never fully provisioned)
    cfg_id = await create_config_record(sub_id, user_id=2, protocol="awg",
                                         server_id=None)
    await activate_config_slot(cfg_id, peer_name="orphan",
                                config_data="...", server_id=None)

    with patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"


# ── Bot-offline guard ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bot_offline_guard_skips_grace_goes_straight_to_expired(
        fresh_db, db_with_server):
    """Sub expired 20 days ago (> GRACE_DAYS=14): scheduler must skip grace
    and mark it expired immediately.  throttle_peer must NOT be called."""
    server_id = db_with_server
    sub_id = await _make_sub(user_id=3, expires_at=LONG_AGO)
    await _make_active_awg(sub_id, server_id)

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    mock_client.throttle_peer.assert_not_awaited()

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired", (
        "Sub expired >GRACE_DAYS ago should go straight to 'expired', not 'grace'"
    )


# ── AWG: grace → expired ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awg_grace_expiry_unthrottle_remove_and_slot_reset(
        fresh_db, db_with_server):
    """When AWG grace expires: unthrottle_peer + remove_peer called,
    config slot reset to empty, sub marked expired."""
    server_id = db_with_server
    sub_id = await _make_sub(user_id=4, expires_at=PAST)
    cfg_id = await _make_active_awg(sub_id, server_id, assigned_ip="10.0.0.3")

    # Move sub to grace with an already-expired grace_until
    grace_until_past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await mark_subscription_grace(sub_id, grace_until_past)

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    mock_client.unthrottle_peer.assert_awaited_once()
    mock_client.remove_peer.assert_awaited_once()
    assert mock_client.remove_peer.await_args.args[0] == "awg"

    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"
    assert cfg["peer_name"] is None

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


# ── VLESS: active → grace ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vless_expiry_moves_to_grace_inbound(fresh_db, db_with_server):
    """VLESS sub expiry: add_peer(vless-grace) then remove_peer(current_svc)."""
    server_id = db_with_server
    sub_id = await _make_sub(user_id=5, expires_at=PAST, plan="vpn_base")
    await _make_active_vless(sub_id, server_id, vless_uuid="uuid-vless-1",
                              service="vless-base")

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # add_peer to vless-grace first, then remove from current service
    mock_client.add_peer.assert_awaited_once()
    assert mock_client.add_peer.await_args.args[0] == "vless-grace"

    mock_client.remove_peer.assert_awaited_once()
    removed_svc = mock_client.remove_peer.await_args.args[0]
    assert removed_svc != "vless-grace", "Should remove from original service, not grace"

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"


@pytest.mark.asyncio
async def test_vless_grace_move_compensating_remove_on_add_failure(
        fresh_db, db_with_server):
    """If add_peer(vless-grace) fails → compensating remove must NOT be called
    (grace_added=False).  Sub still transitions to grace for the other configs."""
    from services.vpnctl_client import VpnctlError

    server_id = db_with_server
    sub_id = await _make_sub(user_id=6, expires_at=PAST, plan="vpn_base")
    await _make_active_vless(sub_id, server_id, vless_uuid="uuid-fail")

    mock_client = _mock_client()
    mock_client.add_peer.side_effect = VpnctlError("agent down")

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # add_peer failed → no remove_peer (nothing to compensate)
    mock_client.remove_peer.assert_not_awaited()
    # Sub still transitions to grace despite agent failure
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"


# ── VLESS: grace → expired ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vless_grace_expiry_removes_from_grace_inbound(
        fresh_db, db_with_server):
    """VLESS grace expiry: remove_peer from vless-grace, slot reset, sub expired."""
    server_id = db_with_server
    sub_id = await _make_sub(user_id=7, expires_at=PAST, plan="vpn_base")
    cfg_id = await _make_active_vless(sub_id, server_id, vless_uuid="uuid-grace-1",
                                       service="vless-grace")

    grace_until_past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await mark_subscription_grace(sub_id, grace_until_past)

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    mock_client.remove_peer.assert_awaited_once()
    assert mock_client.remove_peer.await_args.args[0] == "vless-grace"

    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


# ── edge cases ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_expired_subs_is_noop(fresh_db):
    """No expired subs → no errors, nothing changes."""
    with patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())  # must not raise


@pytest.mark.asyncio
async def test_multiple_subs_all_processed(fresh_db, db_with_server):
    """Two expired AWG subs → both get throttled and marked grace."""
    server_id = db_with_server
    sub1 = await _make_sub(user_id=10, expires_at=PAST)
    sub2 = await _make_sub(user_id=11, expires_at=PAST)
    await _make_active_awg(sub1, server_id, assigned_ip="10.0.0.10")
    await _make_active_awg(sub2, server_id, assigned_ip="10.0.0.11")

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    assert mock_client.throttle_peer.await_count == 2
    assert (await get_subscription_by_id(sub1))["status"] == "grace"
    assert (await get_subscription_by_id(sub2))["status"] == "grace"
