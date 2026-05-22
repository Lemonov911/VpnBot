"""
Regression: handle_oxapay_webhook — cross-method dedup pisht sentinel
payment-row (мой A3 fix mirror'нут на OxaPay).

Сценарий: юзер заплатил Stars (active sub created), параллельно подтвердился
OxaPay invoice. OxaPay webhook падает в cross-method dedup branch:
  - первый раз: записывает payment-row method='oxapay' tx_id='oxapay_{track_id}'
                + admin alert "🚨 OxaPay dup payment"
  - retry того же webhook'а (OxaPay тоже ретраит на 5xx) → silent (UNIQUE)
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio
from aiohttp import web

from services.database import (
    create_subscription,
    is_payment_recorded,
    upsert_user,
)


USER_ID = 7501
PLAN_KEY = "vpn_max"


def _sign_oxapay(body: bytes, api_key: str) -> str:
    return hmac.new(api_key.encode(), body, hashlib.sha512).hexdigest()


def _oxapay_paid_body(*, user_id: int, plan_key: str, track_id: str,
                       amount: str = "500") -> bytes:
    body = {
        "status": "Paid",
        "track_id": track_id,
        "order_id": f"vpn-{user_id}-{plan_key}-99999999",
        "amount": amount,
        "currency": "RUB",
    }
    return json.dumps(body).encode()


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, monkeypatch):
    from services.webapp_api import handle_oxapay_webhook
    import services.webapp_api as wapi
    # OxaPay enabled + ключ для подписи
    monkeypatch.setattr(wapi, "OXAPAY_ENABLED", True, raising=False)
    monkeypatch.setattr(wapi, "OXAPAY_API_KEY", "test_oxa_key", raising=False)

    app = web.Application()
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    app["bot"] = fake_bot
    app.router.add_post("/api/oxapay/webhook", handle_oxapay_webhook)
    client = await aiohttp_client(app)
    client._fake_bot = fake_bot
    return client


async def _setup_racing_active_sub() -> int:
    await upsert_user(USER_ID, "race_oxa", "Race OxaPay")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=PLAN_KEY,
        payment_id=f"stars_charge_{USER_ID}",
        stars_paid=450,
        expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="stars",
    )
    assert sub_id is not None
    return sub_id


# ── Кейс 1: первый dup → sentinel + admin alert ──────────────────────────

@pytest.mark.asyncio
async def test_first_dup_writes_sentinel_and_alerts(app_client, monkeypatch):
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    await _setup_racing_active_sub()
    body = _oxapay_paid_body(user_id=USER_ID, plan_key=PLAN_KEY, track_id="ox1")
    sig = _sign_oxapay(body, "test_oxa_key")

    resp = await app_client.post(
        "/api/oxapay/webhook", data=body,
        headers={"Content-Type": "application/json", "HMAC": sig},
    )
    assert resp.status == 200

    # Sentinel записан
    assert await is_payment_recorded("oxapay_ox1"), (
        "sentinel должен быть записан"
    )
    # Admin alert ушёл (1 раз)
    assert app_client._fake_bot.send_message.await_count == 1
    args = app_client._fake_bot.send_message.await_args
    assert "OxaPay dup payment" in args[0][1]


# ── Кейс 2: ретрай → silent ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_webhook_silent_no_double_alert(app_client, monkeypatch):
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    await _setup_racing_active_sub()
    body = _oxapay_paid_body(user_id=USER_ID, plan_key=PLAN_KEY, track_id="ox2")
    sig = _sign_oxapay(body, "test_oxa_key")

    r1 = await app_client.post(
        "/api/oxapay/webhook", data=body,
        headers={"Content-Type": "application/json", "HMAC": sig},
    )
    assert r1.status == 200
    assert app_client._fake_bot.send_message.await_count == 1

    r2 = await app_client.post(
        "/api/oxapay/webhook", data=body,
        headers={"Content-Type": "application/json", "HMAC": sig},
    )
    assert r2.status == 200
    assert app_client._fake_bot.send_message.await_count == 1, (
        f"admin получил {app_client._fake_bot.send_message.await_count} alerts "
        f"на retry — sentinel-dedup сломан для OxaPay"
    )


# ── Кейс 3: sentinel linked to racing sub ─────────────────────────────────

@pytest.mark.asyncio
async def test_sentinel_links_to_racing_sub(app_client, fresh_db, monkeypatch):
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    racing_sub_id = await _setup_racing_active_sub()
    body = _oxapay_paid_body(user_id=USER_ID, plan_key=PLAN_KEY, track_id="ox3")
    sig = _sign_oxapay(body, "test_oxa_key")

    await app_client.post(
        "/api/oxapay/webhook", data=body,
        headers={"Content-Type": "application/json", "HMAC": sig},
    )

    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, subscription_id, method FROM payments WHERE tx_id=?",
            ("oxapay_ox3",),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["method"] == "oxapay"
    assert row["subscription_id"] == racing_sub_id
