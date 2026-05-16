"""
Crypto Pay (CryptoBot) API client.

Docs: https://help.crypt.bot/crypto-pay-api
"""

import hashlib
import hmac
import logging

import ssl
import certifi
import aiohttp

logger = logging.getLogger(__name__)

CRYPTOPAY_URL = "https://pay.crypt.bot/api"


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    return ctx


async def create_invoice(
    token: str,
    *,
    fiat: str,
    amount: str,
    payload: str,
    description: str,
    bot_username: str,
) -> dict:
    connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
    async with aiohttp.ClientSession(connector=connector) as session:
        resp = await session.post(
            f"{CRYPTOPAY_URL}/createInvoice",
            headers={"Crypto-Pay-API-Token": token},
            json={
                "currency_type":    "fiat",
                "fiat":             fiat,
                "amount":           amount,
                "accepted_assets":  "USDT,TON,BTC,ETH,SOL",
                "payload":          payload,
                "description":      description,
                "expires_in":       3600,
                "hidden_message":   "Спасибо! Конфиг доступен в боте.",
                "paid_btn_name":    "openBot",
                "paid_btn_url":     f"https://t.me/{bot_username}",
            },
        )
        data = await resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"CryptoBot createInvoice error: {data}")

    return data["result"]


async def set_webhook(token: str, url: str) -> bool:
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
        async with aiohttp.ClientSession(connector=connector) as session:
            resp = await session.post(
                f"{CRYPTOPAY_URL}/setWebhook",
                headers={"Crypto-Pay-API-Token": token},
                json={"url": url},
            )
            data = await resp.json()
        ok = data.get("ok", False)
        if ok:
            logger.info("CryptoBot webhook set: %s", url)
        else:
            logger.warning("CryptoBot setWebhook failed: %s", data)
        return ok
    except Exception as e:
        logger.warning("CryptoBot setWebhook error (non-critical): %s", e, exc_info=True)
        return False


def verify_signature(body: bytes, signature: str, token: str) -> bool:
    """Verify incoming webhook signature from CryptoBot."""
    secret = hashlib.sha256(token.encode()).digest()
    computed = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature.lower())
