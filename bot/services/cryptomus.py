"""
Cryptomus API client (https://doc.cryptomus.com/).

Алгоритм подписи (одинаковый для запросов и webhook'ов):
    sign = md5( base64( json_body ) + payment_key )

Заголовки запроса:
    merchant: <merchant_uuid>
    sign:     <md5_hex>
    Content-Type: application/json

Idempotency: order_id уникален. Cryptomus возвращает 422 с
`{"errors":{"order_id":["unique"]}}` при повторе — используется как
естественный идемпотент-guard.

Webhook: payment_status='paid'/'paid_over' — считаем оплаченным. Прочие
('check', 'process', 'confirm_check', 'fail', 'cancel', 'wrong_amount',
'system_fail', 'refund_*') — игнорируем или логируем.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import ssl

import aiohttp
import certifi

logger = logging.getLogger(__name__)

API_BASE = "https://api.cryptomus.com/v1"

# Лимит существования инвойса: 1 час — у юзера время оплатить и не успеет
# протухнуть курс крипты сильно. После TTL Cryptomus возвращает 'fail'.
INVOICE_LIFETIME_SEC = 3600


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _sign(body_bytes: bytes, payment_key: str) -> str:
    """sign = md5( base64(body) + payment_key ).

    body_bytes — точно те же байты, которые отправляем в HTTP body.
    Cryptomus считает sign на своей стороне из своего raw body — поэтому
    для верификации webhook'а тоже надо брать СЫРОЙ request body (не
    json.loads → json.dumps, иначе пробелы/слэши не совпадут).
    """
    b64 = base64.b64encode(body_bytes).decode()
    digest = hashlib.md5((b64 + payment_key).encode()).hexdigest()
    return digest


def verify_signature(raw_body: bytes, received_sign: str, payment_key: str) -> bool:
    """Webhook signature check.

    `received_sign` приходит ВНУТРИ JSON-payload в поле "sign". Но для
    вычисления подписи самой Cryptomus'ом этот ключ из payload'а удаляется —
    т.е. подпись делается от payload'а БЕЗ поля sign. Поэтому надо распарсить,
    выкинуть sign, обратно сериализовать тем же образом что и Cryptomus —
    и тогда base64+md5.

    На практике безопаснее не пытаться повторить их JSON-форматирование
    (PHP-стиль с экранированием слэшей), а сравнить с двумя вариантами:
      1) Python-стиль (separators=(',',':'), ensure_ascii=False)
      2) PHP-стиль (то же + escape '/')
    """
    if not received_sign or not payment_key:
        return False
    try:
        payload = json.loads(raw_body)
    except Exception:
        logger.warning("cryptomus webhook: not valid JSON")
        return False
    if not isinstance(payload, dict):
        return False
    payload_clean = {k: v for k, v in payload.items() if k != "sign"}

    candidates = [
        # Python default — компактный JSON, ensure_ascii=False
        json.dumps(payload_clean, ensure_ascii=False).encode(),
        # Минимальные separators
        json.dumps(payload_clean, ensure_ascii=False, separators=(",", ":")).encode(),
        # PHP-style: escape forward slashes (json_encode default)
        json.dumps(payload_clean, ensure_ascii=False).replace("/", "\\/").encode(),
    ]
    import hmac as _hmac_mod  # для compare_digest

    for cand in candidates:
        expected = _sign(cand, payment_key)
        if _hmac_mod.compare_digest(expected, received_sign):
            return True
    logger.warning(
        "cryptomus webhook: signature mismatch, tried %d variants",
        len(candidates),
    )
    return False


async def create_invoice(
    *,
    merchant_uuid: str,
    payment_key: str,
    amount: str,
    currency: str,
    order_id: str,
    callback_url: str,
    return_url: str,
    description: str | None = None,
) -> dict:
    """Создаёт инвойс. Возвращает {url, uuid, order_id, ...}.

    `amount` — строка чтобы избежать float-погрешностей (Cryptomus parsит
    как Decimal). `order_id` должен быть уникальным; повтор → 422.
    """
    body = {
        "amount":       amount,
        "currency":     currency,
        "order_id":     order_id,
        "url_callback": callback_url,
        "url_return":   return_url,
        "url_success":  return_url,
        "lifetime":     INVOICE_LIFETIME_SEC,
        "is_payment_multiple": False,
    }
    if description:
        body["additional_data"] = description[:200]
    body_bytes = json.dumps(body, ensure_ascii=False).encode()
    sign = _sign(body_bytes, payment_key)
    headers = {
        "merchant":     merchant_uuid,
        "sign":         sign,
        "Content-Type": "application/json",
    }
    connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            f"{API_BASE}/payment",
            data=body_bytes,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            text = await r.text()
            if r.status >= 400:
                logger.warning("cryptomus create_invoice %d: %s", r.status, text[:500])
                raise RuntimeError(f"cryptomus create_invoice failed: {r.status}")
            try:
                data = json.loads(text)
            except Exception:
                logger.exception("cryptomus create_invoice: bad JSON | %.200s", text)
                raise RuntimeError("cryptomus create_invoice: bad JSON")
    # API возвращает {state, result: {...}} либо {result: {...}}
    return data.get("result") or data
