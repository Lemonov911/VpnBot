"""
OxaPay Merchant API client (https://docs.oxapay.com/).

Аутентификация: заголовок `merchant_api_key`.
Подпись webhook'а: заголовок `HMAC` = HMAC-SHA512(raw_body, merchant_api_key).
Статус оплаты: "Paid" — подтверждено блокчейном.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

API_BASE = "https://api.oxapay.com/v1"

# Время жизни инвойса 60 мин — достаточно для крипто-транзакции.
INVOICE_LIFETIME_MIN = 60


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def verify_signature(raw_body: bytes, received_hmac: str, api_key: str) -> bool:
    """Проверяет HMAC-SHA512 подпись webhook'а OxaPay.

    OxaPay шлёт подпись в заголовке `HMAC`.
    Подпись = HMAC-SHA512(raw_body, merchant_api_key) — hex digest.
    """
    if not received_hmac or not api_key:
        return False
    expected = hmac.new(api_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, received_hmac.lower())


async def create_invoice(
    *,
    api_key: str,
    amount: float,
    currency: str,
    order_id: str,
    callback_url: str,
    return_url: str,
    description: str | None = None,
) -> dict:
    """Создаёт инвойс. Возвращает dict с полями `track_id` и `payment_url`.

    `amount` — в единицах `currency` (RUB / USD). OxaPay автоматически
    конвертирует в крипту по текущему курсу.
    """
    body: dict = {
        "amount":       amount,
        "currency":     currency,
        "lifetime":     INVOICE_LIFETIME_MIN,
        "order_id":     order_id,
        "callback_url": callback_url,
        "return_url":   return_url,
        "fee_paid_by_payer": 0,
    }
    if description:
        body["description"] = description[:200]

    connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            f"{API_BASE}/payment/invoice",
            headers={
                "merchant_api_key": api_key,
                "Content-Type": "application/json",
            },
            data=json.dumps(body, ensure_ascii=False).encode(),
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            text = await r.text()
            if r.status >= 400:
                logger.warning("oxapay create_invoice %d: %s", r.status, text[:500])
                raise RuntimeError(f"oxapay create_invoice failed: {r.status}")
            try:
                data = json.loads(text)
            except Exception:
                logger.exception("oxapay create_invoice: bad JSON | %.200s", text)
                raise RuntimeError("oxapay create_invoice: bad JSON")

    # Response: { status: int, data: { track_id, payment_url, ... } }
    inner = data.get("data") or data
    if not inner.get("payment_url"):
        logger.error("oxapay create_invoice: no payment_url in %r", data)
        raise RuntimeError("oxapay create_invoice: no payment_url")
    return inner
