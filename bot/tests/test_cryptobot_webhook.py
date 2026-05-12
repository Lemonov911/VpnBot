"""
Integration tests for the CryptoBot webhook endpoint
(bot/services/webapp_api.py:handle_cryptobot_webhook).

Freezes the CURRENT happy-path behavior so we can prove the upcoming
paid_amount/paid_asset validation fix doesn't break legitimate flows.
"""
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio
from aiohttp import web

import services.webapp_api as webapp_api
from services.webapp_api import handle_cryptobot_webhook, VPN_PLANS


def _sign(body: bytes, token: str) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _build_invoice_paid_body(*, user_id: int, plan_key: str, invoice_id: int,
                              paid_amount: str, paid_asset: str = "USDT") -> bytes:
    """Builds a realistic CryptoBot invoice_paid webhook body."""
    body = {
        "update_id": 12345,
        "update_type": "invoice_paid",
        "payload": {
            "invoice_id": invoice_id,
            "status": "paid",
            "payload": f"vpn:{user_id}:{plan_key}",
            "paid_amount": paid_amount,
            "paid_asset": paid_asset,
        },
    }
    return json.dumps(body).encode()


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, test_cryptobot_token):
    """Builds a tiny aiohttp app with only the webhook handler mounted.

    Stubs bot.send_message so no real Telegram traffic fires.
    """
    app = web.Application()
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    app["bot"] = fake_bot
    app.router.add_post("/api/cryptobot/webhook", handle_cryptobot_webhook)
    client = await aiohttp_client(app)
    client._fake_bot = fake_bot  # stash for asserts
    return client


async def _count_subs(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM subscriptions") as cur:
            return (await cur.fetchone())[0]


async def _count_configs(db_path, *, user_id=None):
    q = "SELECT COUNT(*) FROM configs"
    args: tuple = ()
    if user_id is not None:
        q += " WHERE user_id=?"
        args = (user_id,)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(q, args) as cur:
            return (await cur.fetchone())[0]


async def _configs_for(db_path, user_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM configs WHERE user_id=?", (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_C1_valid_invoice_paid_vpn_base_creates_subscription_and_configs(
    app_client, fresh_db, test_cryptobot_token,
):
    """C1. Valid signed invoice_paid for vpn_base with correct RUB amount → 200,
    one subscription row, vless_slots+awg_slots config rows."""
    user_id = 42
    plan = VPN_PLANS["vpn_base"]
    body = _build_invoice_paid_body(
        user_id=user_id, plan_key="vpn_base", invoice_id=1001,
        paid_amount=plan["rub"], paid_asset="RUB",
    )
    sig = _sign(body, test_cryptobot_token)

    resp = await app_client.post(
        "/api/cryptobot/webhook",
        data=body,
        headers={"crypto-pay-api-signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status == 200

    assert await _count_subs(fresh_db) == 1
    cfgs = await _configs_for(fresh_db, user_id)
    expected = plan["awg_slots"] + plan["vless_slots"]
    assert len(cfgs) == expected, f"expected {expected} configs, got {len(cfgs)}"
    # All should be the right protocol mix and status=empty
    vless = [c for c in cfgs if c["protocol"] == "vless"]
    awg = [c for c in cfgs if c["protocol"] == "awg"]
    assert len(vless) == plan["vless_slots"]
    assert len(awg) == plan["awg_slots"]
    assert all(c["status"] == "empty" for c in cfgs)

    # Telegram notification fired exactly once
    app_client._fake_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_C2_valid_invoice_paid_vpn_max_creates_more_slots(
    app_client, fresh_db, test_cryptobot_token,
):
    """C2. Same shape but vpn_max → more vless slots."""
    user_id = 99
    plan = VPN_PLANS["vpn_max"]
    body = _build_invoice_paid_body(
        user_id=user_id, plan_key="vpn_max", invoice_id=2002,
        paid_amount=plan["rub"], paid_asset="RUB",
    )
    sig = _sign(body, test_cryptobot_token)

    resp = await app_client.post(
        "/api/cryptobot/webhook",
        data=body,
        headers={"crypto-pay-api-signature": sig},
    )
    assert resp.status == 200
    assert await _count_subs(fresh_db) == 1
    cfgs = await _configs_for(fresh_db, user_id)
    assert len(cfgs) == plan["awg_slots"] + plan["vless_slots"]
    assert sum(1 for c in cfgs if c["protocol"] == "vless") == plan["vless_slots"]


@pytest.mark.asyncio
async def test_C3_non_invoice_paid_update_is_noop(
    app_client, fresh_db, test_cryptobot_token,
):
    """C3. update_type != invoice_paid → 200, no DB change."""
    body = json.dumps({
        "update_type": "invoice_expired",
        "payload": {"invoice_id": 7, "payload": "vpn:42:vpn_base"},
    }).encode()
    sig = _sign(body, test_cryptobot_token)

    resp = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"crypto-pay-api-signature": sig},
    )
    assert resp.status == 200
    assert await _count_subs(fresh_db) == 0
    assert await _count_configs(fresh_db) == 0


@pytest.mark.asyncio
async def test_C4_unknown_plan_key_is_noop(
    app_client, fresh_db, test_cryptobot_token,
):
    """C4. Unknown plan_key → 200, no DB change, warning logged."""
    body = _build_invoice_paid_body(
        user_id=42, plan_key="vpn_does_not_exist", invoice_id=3003,
        paid_amount="500",
    )
    sig = _sign(body, test_cryptobot_token)

    resp = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"crypto-pay-api-signature": sig},
    )
    assert resp.status == 200
    assert await _count_subs(fresh_db) == 0
    assert await _count_configs(fresh_db) == 0


@pytest.mark.asyncio
async def test_C5_replay_same_invoice_id_is_idempotent(
    app_client, fresh_db, test_cryptobot_token,
):
    """C5. Replaying same invoice_id (→ same payment_id) → idempotent: sub count stays 1."""
    user_id = 55
    plan = VPN_PLANS["vpn_base"]
    body = _build_invoice_paid_body(
        user_id=user_id, plan_key="vpn_base", invoice_id=5555,
        paid_amount=plan["rub"], paid_asset="RUB",
    )
    sig = _sign(body, test_cryptobot_token)

    r1 = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"crypto-pay-api-signature": sig},
    )
    assert r1.status == 200
    r2 = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"crypto-pay-api-signature": sig},
    )
    assert r2.status == 200

    assert await _count_subs(fresh_db) == 1
    cfgs = await _configs_for(fresh_db, user_id)
    assert len(cfgs) == plan["awg_slots"] + plan["vless_slots"]


@pytest.mark.asyncio
async def test_C6_bad_signature_returns_401(
    app_client, fresh_db, test_cryptobot_token,
):
    """C6. Bad signature → 401, no DB change."""
    body = _build_invoice_paid_body(
        user_id=42, plan_key="vpn_base", invoice_id=6006,
        paid_amount=VPN_PLANS["vpn_base"]["rub"],
    )
    resp = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"crypto-pay-api-signature": "deadbeef" * 8},
    )
    assert resp.status == 401
    assert await _count_subs(fresh_db) == 0
