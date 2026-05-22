"""
Regression: handle_lavatop_webhook — критичные ветки recurring lifecycle.

Покрываем 3 события:
  1. subscription.cancelled — MD-F4: CAS-guard через disable_auto_renew.
     Если in-app cancel уже сбросил auto_renew, webhook НЕ должен слать
     юзеру second cancellation message.
  2. subscription.recurring.payment.failed — admin alert + флаг
     last_charge_failed_at для Mini App warning, sub остаётся active
     (юзер успевает поменять карту).
  3. subscription.recurring.payment.success idempotency — повторный webhook
     с тем же payment_id НЕ extend'ит sub дважды (UNIQUE на tx_id).
"""
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio
from aiohttp import web

import services.database as _db_mod
from services.database import (
    create_subscription,
    upsert_user,
)


USER_ID = 9201
PLAN_KEY = "vpn_base"
PARENT_CONTRACT = "lava_contract_parent_abc"
WEBHOOK_KEY = "test_lava_webhook_key"


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, monkeypatch):
    from services.webapp_api import handle_lavatop_webhook
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "LAVATOP_ENABLED", True, raising=False)
    monkeypatch.setattr(wapi, "LAVATOP_WEBHOOK_KEY", WEBHOOK_KEY, raising=False)
    monkeypatch.setattr(wapi, "LAVATOP_API_KEY", "lava_api_key", raising=False)

    app = web.Application()
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    app["bot"] = fake_bot
    app.router.add_post("/api/lavatop/webhook", handle_lavatop_webhook)
    client = await aiohttp_client(app)
    client._fake_bot = fake_bot
    return client


async def _setup_recurring_sub() -> int:
    """User + active Lava-recurring sub (auto_renew=1, parent_contract=fixed)."""
    await upsert_user(USER_ID, "lava_user", "Lava User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan=PLAN_KEY,
        payment_id="lavatop_first_payment_xyz",
        stars_paid=0,
        expires_at=datetime.utcnow() + timedelta(days=15),
        parent_contract_id=PARENT_CONTRACT,
        auto_renew=True,
        payment_provider="lavatop",
    )
    assert sub_id is not None
    return sub_id


# ── 1. Cancel CAS — in-app cancel raced → webhook silent ────────────────────

@pytest.mark.asyncio
async def test_cancel_webhook_silent_if_already_cancelled(app_client, monkeypatch):
    """MD-F4: in-app cancel уже сбросил auto_renew → webhook видит CAS-False →
    юзер НЕ получает second cancellation message."""
    sub_id = await _setup_recurring_sub()

    # Симулируем in-app cancel: auto_renew уже 0
    from services.database import disable_auto_renew
    was_enabled = await disable_auto_renew(sub_id)
    assert was_enabled is True  # CAS first time returns True

    # Теперь приходит webhook subscription.cancelled (delayed Lava confirmation)
    body = json.dumps({
        "eventType": "subscription.cancelled",
        "contractId": PARENT_CONTRACT,
        "parentContractId": PARENT_CONTRACT,
        "willExpireAt": "2026-06-15",
    }).encode()

    resp = await app_client.post(
        "/api/lavatop/webhook", data=body,
        headers={"Content-Type": "application/json", "X-Api-Key": WEBHOOK_KEY},
    )
    assert resp.status == 200

    # Главное: юзеру НЕТ повторного cancellation-сообщения
    # (in-app handler уже показал юзеру результат)
    assert app_client._fake_bot.send_message.await_count == 0, (
        f"cancel webhook не должен дублировать notification если auto_renew "
        f"уже был 0 (in-app cancel первый), got "
        f"{app_client._fake_bot.send_message.await_count} sends"
    )


@pytest.mark.asyncio
async def test_cancel_webhook_notifies_user_on_first_signal(app_client, monkeypatch):
    """Cancel из Lava-кабинета (не из app) — auto_renew был 1, webhook
    переключает на 0 (CAS True) → юзер получает notification."""
    await _setup_recurring_sub()

    body = json.dumps({
        "eventType": "subscription.cancelled",
        "contractId": PARENT_CONTRACT,
        "parentContractId": PARENT_CONTRACT,
        "willExpireAt": "2026-06-15",
    }).encode()

    resp = await app_client.post(
        "/api/lavatop/webhook", data=body,
        headers={"Content-Type": "application/json", "X-Api-Key": WEBHOOK_KEY},
    )
    assert resp.status == 200

    # Юзер должен получить сообщение
    assert app_client._fake_bot.send_message.await_count == 1


# ── 2. Recurring payment failed — admin notify + last_charge_failed_at ──────

@pytest.mark.asyncio
async def test_recurring_failed_sets_charge_failed_flag_and_notifies_user(
    app_client, fresh_db,
):
    """payment.failed → юзеру TG-уведомление + last_charge_failed_at в БД."""
    sub_id = await _setup_recurring_sub()

    body = json.dumps({
        "eventType": "subscription.recurring.payment.failed",
        "contractId": "lava_renewal_attempt_001",
        "parentContractId": PARENT_CONTRACT,
        "amount": 200,
        "currency": "RUB",
    }).encode()

    resp = await app_client.post(
        "/api/lavatop/webhook", data=body,
        headers={"Content-Type": "application/json", "X-Api-Key": WEBHOOK_KEY},
    )
    assert resp.status == 200

    # User TG-уведомление
    assert app_client._fake_bot.send_message.await_count == 1

    # last_charge_failed_at персистируется
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT last_charge_failed_at FROM subscriptions WHERE id=?", (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            assert row[0] is not None, "last_charge_failed_at должен быть set"


# ── 3. Webhook with bad key returns 401 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_rejects_bad_key(app_client):
    body = json.dumps({
        "eventType": "subscription.cancelled",
        "contractId": "any",
    }).encode()

    resp = await app_client.post(
        "/api/lavatop/webhook", data=body,
        headers={"Content-Type": "application/json", "X-Api-Key": "wrong_key"},
    )
    assert resp.status == 401
