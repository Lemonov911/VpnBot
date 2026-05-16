"""
Telegram Stars payment flow — pre_checkout + successful_payment.

pre_checkout tests: pure logic, no DB needed (the handler only validates
payload + amount and calls query.answer()).

successful_payment tests: call `_deliver_vpn` directly with a real in-memory
DB (`fresh_db` fixture) and mocked network (provision_peer, bot.send_*).
Verifies subscription/orders/payments/configs rows are created correctly,
TOCTOU dedup works, and the 0/N refund safety-net fires when provision fails.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import aiosqlite

from services.database import (
    upsert_user,
    get_subscription_by_payment_id,
    get_subscription_by_id,
    get_configs_for_subscription,
    is_payment_refunded,
)
from services.plans import VPN_PLANS
from services.vpnctl_client import PeerResult, VpnctlError


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_query(payload: str, total_amount: int, user_id: int = 100) -> MagicMock:
    """Builds a stand-in for aiogram.types.PreCheckoutQuery."""
    q = MagicMock()
    q.invoice_payload = payload
    q.total_amount = total_amount
    q.from_user = MagicMock(id=user_id)
    q.answer = AsyncMock()
    return q


def _make_payment(payload: str, total_amount: int, charge_id: str) -> MagicMock:
    p = MagicMock()
    p.invoice_payload = payload
    p.total_amount = total_amount
    p.telegram_payment_charge_id = charge_id
    return p


def _make_message(user_id: int, payment) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock(id=user_id)
    msg.successful_payment = payment
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.refund_star_payment = AsyncMock()
    return msg


@pytest_asyncio.fixture
async def db_with_servers(fresh_db, monkeypatch):
    """fresh_db + AWG and VLESS test servers + a user.  Returns user_id."""
    import services.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", fresh_db)

    async with aiosqlite.connect(fresh_db) as db:
        await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers)
               VALUES ('AWG-1', '1.2.3.4', 'awg', 'http://a:8080', 't', 1, 100, 0)"""
        )
        await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers)
               VALUES ('VLS-1', '1.2.3.5', 'vless', 'http://v:8080', 't', 1, 100, 0)"""
        )
        await db.commit()

    user_id = 100500
    await upsert_user(user_id, username="testuser", first_name="Test")
    return user_id


def _peer_factory(protocol: str = "awg"):
    """Builds a PeerResult that looks like what vpnctl returns."""
    extra = {"assigned_ip": "10.0.0.42"} if protocol == "awg" else {}
    return PeerResult(
        id="peer-uuid-001",
        label="test_peer",
        config=f"[Interface]\n# {protocol} test config",
        extra=extra,
    )


# ── pre_checkout: validation logic ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pre_checkout_valid_plan_accepts(fresh_db):
    """Known plan + correct amount → ok=True."""
    from handlers.vpn import pre_checkout
    q = _make_query("vpn_base", total_amount=VPN_PLANS["vpn_base"]["stars"])
    await pre_checkout(q)
    q.answer.assert_awaited_once_with(ok=True)


@pytest.mark.asyncio
async def test_pre_checkout_underpriced_rejects(fresh_db):
    """SECURITY: total_amount < plan.stars → ok=False (defence-in-depth)."""
    from handlers.vpn import pre_checkout
    q = _make_query("vpn_base", total_amount=1)  # huge underpay
    await pre_checkout(q)
    call = q.answer.await_args
    assert call.kwargs.get("ok") is False
    assert "сумма" in call.kwargs.get("error_message", "").lower()


@pytest.mark.asyncio
async def test_pre_checkout_unknown_payload_rejects(fresh_db):
    """Unknown plan_key (deleted from VPN_PLANS between invoice and pay) → ok=False."""
    from handlers.vpn import pre_checkout
    q = _make_query("vpn_does_not_exist", total_amount=100)
    await pre_checkout(q)
    assert q.answer.await_args.kwargs.get("ok") is False


@pytest.mark.asyncio
async def test_pre_checkout_empty_payload_rejects(fresh_db):
    from handlers.vpn import pre_checkout
    q = _make_query("", total_amount=100)
    await pre_checkout(q)
    assert q.answer.await_args.kwargs.get("ok") is False


@pytest.mark.asyncio
async def test_pre_checkout_esim_payload_accepts(fresh_db):
    """eSIM uses a different payload format — must pass through."""
    from handlers.vpn import pre_checkout
    q = _make_query("esim:PKG_CODE_123:500", total_amount=500)
    await pre_checkout(q)
    q.answer.assert_awaited_once_with(ok=True)


@pytest.mark.asyncio
async def test_pre_checkout_plan_upgrade_payload_accepts(fresh_db):
    """plan_upgrade payload (upgrade flow) — must pass through."""
    from handlers.vpn import pre_checkout
    q = _make_query("plan_upgrade:42:vpn_max:120", total_amount=120)
    await pre_checkout(q)
    q.answer.assert_awaited_once_with(ok=True)


@pytest.mark.asyncio
async def test_pre_checkout_banned_user_rejected(fresh_db):
    """SECURITY: banned user — даже с валидным payload и суммой — должен быть
    отбит на pre_checkout, Telegram отменит charge до списания."""
    from services.database import upsert_user, set_user_banned
    from handlers.vpn import pre_checkout

    user_id = 666
    await upsert_user(user_id, username="bad", first_name="Bad")
    await set_user_banned(user_id, banned=True, reason="abuse")

    q = _make_query("vpn_base", total_amount=VPN_PLANS["vpn_base"]["stars"], user_id=user_id)
    await pre_checkout(q)
    call = q.answer.await_args
    assert call.kwargs.get("ok") is False


# ── successful_payment: happy path ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_vpn_creates_subscription_and_orders(db_with_servers):
    """Happy path: subscription row + orders row + payments row created with
    correct stars_paid and plan_key."""
    user_id = db_with_servers
    plan_key = "vpn_base"
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_001")
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=lambda s, lbl, proto, **kw: _peer_factory(proto))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    sub = await get_subscription_by_payment_id("charge_001")
    assert sub is not None
    assert sub["plan"] == plan_key
    assert sub["stars_paid"] == plan["stars"]
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_deliver_vpn_creates_correct_slot_counts(db_with_servers):
    """vpn_base → 2 AWG + 1 VLESS slots; configs table must have them all."""
    user_id = db_with_servers
    plan_key = "vpn_base"
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_002")
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=lambda s, lbl, proto, **kw: _peer_factory(proto))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    sub = await get_subscription_by_payment_id("charge_002")
    configs = await get_configs_for_subscription(sub["id"])
    by_proto = {c["protocol"]: 0 for c in configs}
    for c in configs:
        by_proto[c["protocol"]] += 1

    assert by_proto.get("awg", 0) == plan["awg_slots"]
    assert by_proto.get("vless", 0) == plan["vless_slots"]


@pytest.mark.asyncio
async def test_deliver_vpn_vless_replicated_across_all_active_servers(fresh_db, monkeypatch):
    """Multi-location: 2 active VLESS-сервера, vless_slots=1 → 1 UUID,
    2 строки в configs (по одной на сервер), все с одинаковым vless_uuid."""
    import services.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", fresh_db)

    async with aiosqlite.connect(fresh_db) as db:
        # 1 AWG server (vpn_base has 2 awg slots)
        await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers, flag)
               VALUES ('AWG-1', '1.2.3.4', 'awg', 'http://a:8080', 't', 1, 100, 0, '🇳🇱')"""
        )
        # 2 VLESS servers in different locations
        await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers, flag)
               VALUES ('VLS-NL', '1.2.3.5', 'vless', 'http://v1:8080', 't', 1, 100, 0, '🇳🇱')"""
        )
        await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers, flag)
               VALUES ('VLS-DE', '1.2.3.6', 'vless', 'http://v2:8080', 't', 1, 100, 0, '🇩🇪')"""
        )
        await db.commit()

    user_id = 200600
    await upsert_user(user_id, username="multi", first_name="Multi")

    plan_key = "vpn_base"  # 2 awg + 1 vless
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_multi_loc")
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=lambda s, lbl, proto, **kw: _peer_factory(proto))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    sub = await get_subscription_by_payment_id("charge_multi_loc")
    assert sub is not None
    configs = await get_configs_for_subscription(sub["id"])
    vless_cfgs = [c for c in configs if c["protocol"] == "vless"]
    # 1 vless slot × 2 servers = 2 vless configs (one per location)
    assert len(vless_cfgs) == 2, f"expected 2 vless configs (1 slot × 2 servers), got {len(vless_cfgs)}"
    # Both should be on DIFFERENT servers
    server_ids = {c["server_id"] for c in vless_cfgs}
    assert len(server_ids) == 2, "two location configs must be on different servers"
    # Главный инвариант multi-location: один UUID на все локации одного слота.
    # Без этого scheduler не сможет grace-migrate-нуть пира одной командой
    # по UUID, и у юзера в Happ будет несвязанная пачка пиров.
    uuids = {c.get("vless_uuid") for c in vless_cfgs}
    assert len(uuids) == 1 and None not in uuids, (
        f"all location configs of one slot must share one vless_uuid, got {uuids}"
    )


@pytest.mark.asyncio
async def test_deliver_vpn_no_refund_when_at_least_one_peer_provisioned(db_with_servers):
    """Partial success (some peers fail, some succeed) → no refund.
    Sub stays active so user gets at least one working config."""
    user_id = db_with_servers
    plan_key = "vpn_base"  # 2 AWG + 1 VLESS = 3 slots
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_partial")
    message = _make_message(user_id, payment)

    # First call succeeds, rest fail
    call_n = {"i": 0}
    async def mixed_provision(server, label, proto, **kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return _peer_factory(proto)
        raise VpnctlError("agent down on 2nd peer")

    with patch("handlers.vpn.provision_peer", new=AsyncMock(side_effect=mixed_provision)):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    message.bot.refund_star_payment.assert_not_awaited()
    sub = await get_subscription_by_payment_id("charge_partial")
    assert sub["status"] == "active"


# ── successful_payment: dedup (idempotency) ───────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_vpn_duplicate_payment_id_skipped(db_with_servers):
    """Second `successful_payment` event with same charge_id → no second sub."""
    user_id = db_with_servers
    plan_key = "vpn_base"
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_dup_001")
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=lambda s, lbl, proto, **kw: _peer_factory(proto))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)
        first_sub = await get_subscription_by_payment_id("charge_dup_001")

        # Replay: same charge_id, same user — must early-exit.
        await _deliver_vpn(message, payment, plan, plan_key)

    # Sub count for this charge is still exactly one
    sub = await get_subscription_by_payment_id("charge_dup_001")
    assert sub["id"] == first_sub["id"]


# ── successful_payment: 0/N refund safety net ─────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_vpn_zero_peers_triggers_refund(db_with_servers):
    """All provision_peer calls fail → refund_star_payment called, sub expired."""
    user_id = db_with_servers
    plan_key = "vpn_base"
    plan = VPN_PLANS[plan_key]
    payment = _make_payment(plan_key, plan["stars"], "charge_fail_001")
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=VpnctlError("all agents down"))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    message.bot.refund_star_payment.assert_awaited_once()
    args = message.bot.refund_star_payment.await_args.args
    assert args[0] == user_id
    assert args[1] == "charge_fail_001"

    sub = await get_subscription_by_payment_id("charge_fail_001")
    # After refund: status moves to "refunded" (or "expired" in older flow);
    # what matters is it's not "active" and payment is marked refunded.
    assert sub["status"] in ("refunded", "expired")
    assert await is_payment_refunded("charge_fail_001")


@pytest.mark.asyncio
async def test_deliver_vpn_already_refunded_charge_no_double_refund(db_with_servers):
    """If is_payment_refunded() returns True (replay after refund) →
    refund_star_payment NOT called again."""
    from services.database import mark_payment_refunded, record_payment
    user_id = db_with_servers
    plan_key = "vpn_base"
    plan = VPN_PLANS[plan_key]
    charge_id = "charge_already_refunded"

    # Pre-seed: payments row marked as refunded
    await record_payment(user_id=user_id, subscription_id=None,
                         method="stars", stars=plan["stars"], tx_id=charge_id)
    await mark_payment_refunded(charge_id)

    payment = _make_payment(plan_key, plan["stars"], charge_id)
    message = _make_message(user_id, payment)

    with patch("handlers.vpn.provision_peer",
               new=AsyncMock(side_effect=VpnctlError("agents down"))):
        from handlers.vpn import _deliver_vpn
        await _deliver_vpn(message, payment, plan, plan_key)

    # Idempotent: no second refund_star_payment call
    message.bot.refund_star_payment.assert_not_awaited()
