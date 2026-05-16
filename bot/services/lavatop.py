"""
Lava.top API client (https://gate.lava.top/docs).

Auth:
    Header `X-Api-Key: <api-key>` — один ключ из кабинета Lava → API.

Особенности:
    - Покупки идентифицируются по `email` (нет custom payload).
    - `offerId` создаётся в кабинете Lava per товар; для recurring подписки
      товар должен иметь periodicity != ONE_TIME (MONTHLY/YEARLY/etc).
    - Webhook'и приходят на наш `url_callback`, заданный в Integration tab.
      Auth webhook'а — тот же X-Api-Key (или отдельный shared secret).

Event types в webhook:
    - payment.success                       — первая оплата (one-time или sub start)
    - payment.failed                        — неудача первой оплаты
    - subscription.recurring.payment.success — recurring продление
    - subscription.recurring.payment.failed  — recurring неудача
    - subscription.cancelled                 — юзер отменил из кабинета Lava

Статусы (поле status в webhook'е):
    - completed              — one-time куплено
    - subscription-active    — подписка активна (после успешной оплаты)
    - subscription-failed    — неудача
    - subscription-expired   — истекла после cancel
"""
from __future__ import annotations

import json
import logging
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

API_BASE = "https://gate.lava.top"


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


async def create_invoice(
    *,
    api_key: str,
    email: str,
    offer_id: str,
    currency: str = "RUB",
    payment_method: str | None = None,
    periodicity: str | None = None,
    buyer_language: str = "RU",
) -> dict:
    """POST /api/v2/invoice → {id, status, amountTotal, paymentUrl}.

    Если periodicity не указан и продукт-подписка, Lava ставит дефолт продукта.
    Возвращает paymentUrl — куда редиректим юзера.
    """
    body: dict = {
        "email":         email,
        "offerId":       offer_id,
        "currency":      currency,
        "buyerLanguage": buyer_language,
    }
    if payment_method:
        body["paymentMethod"] = payment_method
    if periodicity:
        body["periodicity"] = periodicity

    headers = {
        "X-Api-Key":    api_key,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            f"{API_BASE}/api/v2/invoice",
            data=json.dumps(body).encode(),
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            text = await r.text()
            if r.status not in (200, 201):
                logger.warning("lava.top create_invoice %d: %s", r.status, text[:500])
                raise RuntimeError(f"lava.top create_invoice failed: {r.status}")
            try:
                data = json.loads(text)
            except Exception:
                logger.exception("lava.top create_invoice: bad JSON | %.200s", text)
                raise RuntimeError("lava.top create_invoice: bad JSON")
    return data


async def cancel_subscription(*, api_key: str, contract_id: str) -> bool:
    """Отменяет recurring подписку. Контракт остаётся активным до конца
    оплаченного периода — Lava пришлёт subscription.cancelled webhook с
    willExpireAt. Используется когда юзер жмёт «Отменить автопродление».

    POST /api/v1/subscriptions/{id}/cancel (по факту endpoint меняется
    между версиями API — пробуем v2 path первым).
    """
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
    async with aiohttp.ClientSession(connector=connector) as session:
        for path in (
            f"/api/v2/subscriptions/{contract_id}/cancel",
            f"/api/v1/subscriptions/{contract_id}/cancel",
        ):
            try:
                async with session.post(
                    f"{API_BASE}{path}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status in (200, 202, 204):
                        return True
                    body = await r.text()
                    logger.warning("lava.top cancel %s %d: %s", path, r.status, body[:200])
            except Exception as e:
                logger.warning("lava.top cancel %s error: %s", path, e)
    return False


def verify_webhook_key(request_key: str | None, expected_key: str) -> bool:
    """Сравнение X-Api-Key в webhook'е с настроенным секретом.
    constant-time чтобы не давать timing-leak.
    """
    if not request_key or not expected_key:
        return False
    import hmac as _hmac
    return _hmac.compare_digest(request_key.encode(), expected_key.encode())
