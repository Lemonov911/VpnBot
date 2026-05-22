"""
Regression: cross-method dedup в CryptoBot/OxaPay/Lava webhook handlers
пишет sentinel payment-row через UNIQUE(tx_id), чтобы webhook-ретраи от
платёжки не спамили админу alert каждый раз.

Сценарий: юзер ужe заплатил Stars (active sub), параллельно отправил CryptoBot
invoice — между моментом создания invoice и приходом webhook'а Stars-flow
успел создать active sub. CryptoBot webhook падает в cross-method dedup
branch:
  - первый раз: записывает payment-row + шлёт admin alert "🚨 dup payment"
  - ретрай webhook'а от CryptoBot (они любят ретраить на 5xx): silent,
    второй record_payment вернёт False (UNIQUE), no admin alert.

Без sentinel-row каждый retry = новый admin alert (раньше за час могло
прилететь 5-10 одинаковых уведомлений).
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


USER_ID = 7001
PLAN_KEY = "vpn_max"


def _sign(body: bytes, token: str) -> str:
    """HMAC-SHA256 подпись CryptoBot webhook (как в test_cryptobot_webhook.py)."""
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _cb_invoice_paid_body(*, user_id: int, plan_key: str, invoice_id: int,
                           paid_amount: str = "10.00") -> bytes:
    """Realistic CryptoBot invoice_paid webhook payload."""
    body = {
        "update_id": 999,
        "update_type": "invoice_paid",
        "payload": {
            "invoice_id": invoice_id,
            "status": "paid",
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": "500",  # vpn_max RUB (must match VPN_PLANS['vpn_max']['rub'])
            "payload": f"vpn:{user_id}:{plan_key}",
            "paid_amount": paid_amount,
            "paid_asset": "USDT",
        },
    }
    return json.dumps(body).encode()


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, test_cryptobot_token):
    """aiohttp test client с замоканным bot.send_message — чтобы считать
    сколько раз шёл admin alert."""
    from services.webapp_api import handle_cryptobot_webhook
    app = web.Application()
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    app["bot"] = fake_bot
    app.router.add_post("/api/cryptobot/webhook", handle_cryptobot_webhook)
    client = await aiohttp_client(app)
    client._fake_bot = fake_bot  # stash для assert
    return client


async def _setup_user_with_active_sub() -> int:
    """Создаём user + active vpn_max sub (имитация Stars-purchase которая
    создалась за миг до прихода нашего CryptoBot webhook'а)."""
    await upsert_user(USER_ID, "racing_user", "Racing User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=PLAN_KEY,
        payment_id=f"stars_charge_{USER_ID}",
        stars_paid=450,
        expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="stars",
    )
    assert sub_id is not None
    return sub_id


# ── Кейс 1: первый dup-webhook → admin alert + sentinel row ─────────────────

@pytest.mark.asyncio
async def test_first_dup_webhook_writes_sentinel_and_alerts_admin(
    app_client, fresh_db, monkeypatch, test_cryptobot_token,
):
    """Первое попадание в cross-method dedup branch — admin alert + sentinel row."""
    import services.webapp_api as wapi
    # ADMIN_ID должен быть выставлен чтобы admin alert полетел
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    await _setup_user_with_active_sub()
    body = _cb_invoice_paid_body(user_id=USER_ID, plan_key=PLAN_KEY,
                                   invoice_id=12345)
    sig = _sign(body, test_cryptobot_token)

    resp = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={
            "Content-Type": "application/json",
            "crypto-pay-api-signature": sig,
        },
    )
    assert resp.status == 200

    # Sentinel payment-row создан с tx_id = crypto_12345
    assert await is_payment_recorded("crypto_12345"), (
        "sentinel payment-row не записан — webhook retry будет снова alert'ить"
    )

    # Admin alert ушёл ровно один раз
    assert app_client._fake_bot.send_message.await_count == 1, (
        f"admin alert send_message звался "
        f"{app_client._fake_bot.send_message.await_count} раз (ожидали 1)"
    )
    args = app_client._fake_bot.send_message.await_args
    assert "CryptoBot dup payment" in args[0][1] or "dup payment" in args[0][1]


# ── Кейс 2: ретрай webhook'а от CryptoBot → silent (no new alert) ────────────

@pytest.mark.asyncio
async def test_retry_webhook_is_silent_no_double_alert(
    app_client, fresh_db, monkeypatch, test_cryptobot_token,
):
    """Тот же invoice_id приходит повторно (CryptoBot ретраит на 5xx) →
    sentinel payment-row уже есть → silent return 200, нет нового alert."""
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    await _setup_user_with_active_sub()
    body = _cb_invoice_paid_body(user_id=USER_ID, plan_key=PLAN_KEY,
                                   invoice_id=22222)
    sig = _sign(body, test_cryptobot_token)

    # 1-й вызов — должен alert'нуть
    r1 = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"Content-Type": "application/json",
                 "crypto-pay-api-signature": sig},
    )
    assert r1.status == 200
    assert app_client._fake_bot.send_message.await_count == 1

    # 2-й вызов с тем же payload — webhook retry
    r2 = await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"Content-Type": "application/json",
                 "crypto-pay-api-signature": sig},
    )
    assert r2.status == 200, "retry должен вернуть 200 silent"

    # Главное: admin alert НЕ улетел повторно
    assert app_client._fake_bot.send_message.await_count == 1, (
        f"админ получил {app_client._fake_bot.send_message.await_count} alerts "
        f"на ОДИН dup payment — sentinel-dedup не работает"
    )


# ── Кейс 3: sentinel-row проверяемо persistent ───────────────────────────────

@pytest.mark.asyncio
async def test_sentinel_row_links_payment_to_racing_sub(
    app_client, fresh_db, monkeypatch, test_cryptobot_token,
):
    """Sentinel row должна линковаться к ID racing-sub (для админ-аудита
    «какой sub юзера конфликтнул с дублем»). Проверяем структуру row."""
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_ID", 99999, raising=False)

    racing_sub_id = await _setup_user_with_active_sub()
    body = _cb_invoice_paid_body(user_id=USER_ID, plan_key=PLAN_KEY,
                                   invoice_id=33333)
    sig = _sign(body, test_cryptobot_token)

    await app_client.post(
        "/api/cryptobot/webhook", data=body,
        headers={"Content-Type": "application/json",
                 "crypto-pay-api-signature": sig},
    )

    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, subscription_id, method, tx_id "
            "FROM payments WHERE tx_id=?",
            ("crypto_33333",),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None, "sentinel payment-row не найден"
    assert row["user_id"] == USER_ID
    assert row["method"] == "crypto"
    assert row["subscription_id"] == racing_sub_id, (
        "sentinel должна линковаться к ID существующей racing-sub — "
        "это для аудита 'какая sub конфликтнула с дублем'"
    )
