"""
Subscription state-machine: active → grace → expired.

Pure DB-layer tests — no agent, no bot, no mocks.
Each test gets a fresh in-memory SQLite via the `fresh_db` fixture.
"""
from datetime import datetime, timedelta

import pytest

from services.database import (
    create_subscription,
    create_config_record,
    activate_config_slot,
    get_expired_subscriptions,
    get_grace_expired_subscriptions,
    mark_subscription_grace,
    mark_subscription_expired,
    reset_config_slot,
    get_configs_for_subscription,
    get_subscription_by_id,
    get_config_by_id,
)

# ── helpers ───────────────────────────────────────────────────────────────────

PAST   = (datetime.utcnow() - timedelta(days=1)).isoformat()
FUTURE = (datetime.utcnow() + timedelta(days=30)).isoformat()
LONG_AGO = (datetime.utcnow() - timedelta(days=20)).isoformat()  # > GRACE_DAYS=14


async def _make_sub(user_id: int = 1, plan: str = "vpn_trial",
                    expires_at: str = FUTURE) -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"test_{user_id}_{plan}_{expires_at[:10]}",
        stars_paid=0, expires_at=datetime.fromisoformat(expires_at),
    )


async def _make_active_config(sub_id: int, user_id: int = 1,
                               protocol: str = "awg",
                               assigned_ip: str = "10.0.0.2") -> int:
    cfg_id = await create_config_record(sub_id, user_id, protocol=protocol, server_id=1)
    await activate_config_slot(
        cfg_id, peer_name=f"peer_{cfg_id}",
        config_data="[Interface]\n...",
        server_id=1, assigned_ip=assigned_ip,
    )
    return cfg_id


# ── expired-subscriptions query ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_future_sub_not_in_expired_list(fresh_db):
    """Sub with expires_at in the future must NOT appear in the expired list."""
    await _make_sub(expires_at=FUTURE)
    assert await get_expired_subscriptions() == []


@pytest.mark.asyncio
async def test_active_past_sub_appears_in_expired_list(fresh_db):
    """Sub with expires_at in the past IS returned."""
    sub_id = await _make_sub(expires_at=PAST)
    expired = await get_expired_subscriptions()
    assert len(expired) == 1
    assert expired[0]["id"] == sub_id


@pytest.mark.asyncio
async def test_expired_query_includes_expires_at_and_pending_plan(fresh_db):
    """expires_at and pending_plan must be present in the result so the
    scheduler bot-offline guard and downgrade logic can read them."""
    await _make_sub(expires_at=PAST)
    row = (await get_expired_subscriptions())[0]
    assert "expires_at" in row
    assert "pending_plan" in row


@pytest.mark.asyncio
async def test_long_ago_sub_has_expires_at_before_grace_cutoff(fresh_db):
    """Sub expired 20 days ago has expires_at < (now - 14d) — the scheduler
    should skip grace and go straight to expired.  We verify the DB field."""
    await _make_sub(expires_at=LONG_AGO)
    row = (await get_expired_subscriptions())[0]
    cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
    assert row["expires_at"] < cutoff, "expires_at should be before grace cutoff"


# ── active → grace ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_grace_sets_status_and_grace_until(fresh_db):
    sub_id = await _make_sub(expires_at=PAST)
    grace_until = (datetime.utcnow() + timedelta(days=14)).isoformat()
    await mark_subscription_grace(sub_id, grace_until)

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"
    assert sub["grace_until"] == grace_until


@pytest.mark.asyncio
async def test_grace_sub_not_in_expired_list(fresh_db):
    """Grace sub must NOT appear in get_expired_subscriptions (status != active)."""
    sub_id = await _make_sub(expires_at=PAST)
    await mark_subscription_grace(sub_id, FUTURE)
    assert await get_expired_subscriptions() == []


# ── grace → expired ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grace_sub_with_future_grace_until_not_in_grace_expired(fresh_db):
    sub_id = await _make_sub(expires_at=PAST)
    await mark_subscription_grace(sub_id, FUTURE)
    assert await get_grace_expired_subscriptions() == []


@pytest.mark.asyncio
async def test_grace_sub_with_past_grace_until_in_grace_expired(fresh_db):
    sub_id = await _make_sub(expires_at=PAST)
    grace_until_past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await mark_subscription_grace(sub_id, grace_until_past)

    grace_expired = await get_grace_expired_subscriptions()
    assert len(grace_expired) == 1
    assert grace_expired[0]["id"] == sub_id


@pytest.mark.asyncio
async def test_mark_expired_sets_status_and_clears_pending_plan(fresh_db):
    sub_id = await _make_sub(expires_at=PAST)
    await mark_subscription_grace(sub_id, PAST)
    await mark_subscription_expired(sub_id)

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"
    assert sub["pending_plan"] is None


@pytest.mark.asyncio
async def test_expired_sub_not_in_grace_expired_list(fresh_db):
    """Once fully expired, sub must not re-appear in grace_expired."""
    sub_id = await _make_sub(expires_at=PAST)
    grace_until_past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await mark_subscription_grace(sub_id, grace_until_past)
    await mark_subscription_expired(sub_id)
    assert await get_grace_expired_subscriptions() == []


# ── config slot lifecycle ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_configs_returns_only_active_slots(fresh_db):
    """get_configs_for_subscription must skip empty/activating slots."""
    sub_id = await _make_sub()
    active_cfg = await _make_active_config(sub_id)
    _empty_cfg = await create_config_record(sub_id, user_id=1, protocol="awg")

    configs = await get_configs_for_subscription(sub_id)
    cfg_ids = {c["id"] for c in configs}
    assert active_cfg in cfg_ids
    assert _empty_cfg not in cfg_ids


@pytest.mark.asyncio
async def test_reset_config_slot_clears_peer_data(fresh_db):
    sub_id = await _make_sub()
    cfg_id = await _make_active_config(sub_id, assigned_ip="10.0.0.5")

    cfg_before = await get_config_by_id(cfg_id)
    assert cfg_before["status"] == "active"
    assert cfg_before["peer_name"] is not None

    await reset_config_slot(cfg_id)

    cfg_after = await get_config_by_id(cfg_id)
    assert cfg_after["status"] == "empty"
    assert cfg_after["peer_name"] is None
    assert cfg_after["config_data"] is None
    assert cfg_after["vless_uuid"] is None
    assert cfg_after["server_id"] is None


@pytest.mark.asyncio
async def test_reset_config_slot_not_in_active_configs(fresh_db):
    sub_id = await _make_sub()
    cfg_id = await _make_active_config(sub_id)

    await reset_config_slot(cfg_id)
    configs = await get_configs_for_subscription(sub_id)
    assert not any(c["id"] == cfg_id for c in configs)


# ── full cycle DB-only ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_cycle_active_grace_expired(fresh_db):
    """Smoke-test: sub goes through the full active→grace→expired path."""
    sub_id = await _make_sub(expires_at=PAST)
    cfg_id = await _make_active_config(sub_id)

    # Step 1: active → grace
    grace_until = (datetime.utcnow() + timedelta(days=14)).isoformat()
    await mark_subscription_grace(sub_id, grace_until)
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"
    assert (await get_configs_for_subscription(sub_id)) != []  # config still active

    # Step 2: grace → expired (simulate grace_until passed)
    grace_until_past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await mark_subscription_grace(sub_id, grace_until_past)  # rewind for test
    await reset_config_slot(cfg_id)
    await mark_subscription_expired(sub_id)

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"
    assert await get_configs_for_subscription(sub_id) == []
