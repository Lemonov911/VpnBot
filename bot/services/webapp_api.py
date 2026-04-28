"""
aiohttp HTTP API — backend для Telegram Mini App.

VPN:
  POST /api/vpn/invoice            { plan_key } → { invoice_url }
  GET  /api/vpn/configs            → [{ id, protocol, peer_name, plan, expires_at, has_config }]
  GET  /api/vpn/config/{id}/download → файл .conf (attachment)
  POST /api/vpn/config/{id}/revoke → { ok: true }

eSIM:
  GET  /api/esim/countries         → [{ code, name, count }]
  GET  /api/esim/packages          ?country=ES → [{ packageCode, ... stars }]
  POST /api/esim/invoice           { package_code, price, stars, name } → { invoice_url }

Авторизация:
  Приоритет — заголовок X-Telegram-Init-Data.
  Fallback  — поле init_data в теле запроса (обратная совместимость).
  В DEBUG-режиме проверка отключается.
"""

import logging
import os

from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from config import DEBUG, ADMIN_ID, BOT_TOKEN, CRYPTOBOT_TOKEN, WEBAPP_URL
from services.auth import verify_init_data
import services.esim_api as esim
from services.database import (
    get_user_configs, get_user_configs_full, get_config_by_id, activate_config_slot,
    reset_config_slot, get_servers_by_protocol, get_server_by_id,
    get_active_subscription, change_subscription_plan, schedule_plan_change,
    has_active_subscription, create_support_ticket, update_ticket_admin_msg,
    get_referral_stats as db_get_referral_stats,
)

logger = logging.getLogger(__name__)

# ── VPN тарифы ─────────────────────────────────────────────────────────────────

VPN_PLANS: dict[str, dict] = {
    "vpn_start":   {"name": "Старт",      "stars": 128,  "rub": "180",  "usd": "2.00",  "duration_days": 30, "awg_slots": 1, "vless_slots": 0},
    "vpn_popular": {"name": "Популярный", "stars": 214,  "rub": "270",  "usd": "3.00",  "duration_days": 30, "awg_slots": 2, "vless_slots": 0},
    "vpn_pro":     {"name": "Про",        "stars": 342,  "rub": "450",  "usd": "5.00",  "duration_days": 30, "awg_slots": 3, "vless_slots": 1},
    "vpn_family":  {"name": "Семейный",   "stars": 513,  "rub": "640",  "usd": "7.00",  "duration_days": 30, "awg_slots": 7, "vless_slots": 1},
    # Старые тарифы — обратная совместимость с существующими заказами
    "vpn_1m": {"name": "1 месяц",  "stars": 299,  "rub": "299",  "usd": "3.50",  "duration_days": 30,  "awg_slots": 1, "vless_slots": 0},
    "vpn_3m": {"name": "3 месяца", "stars": 699,  "rub": "699",  "usd": "8.00",  "duration_days": 90,  "awg_slots": 1, "vless_slots": 0},
    "vpn_1y": {"name": "1 год",    "stars": 1990, "rub": "1990", "usd": "22.00", "duration_days": 365, "awg_slots": 1, "vless_slots": 0},
}

# ── Авторизация ────────────────────────────────────────────────────────────────

def _resolve_user(request: web.Request, body: dict | None = None) -> dict | None:
    """
    Определяет пользователя из запроса.

    Порядок проверки:
      1. Заголовок X-Telegram-Init-Data
      2. Поле init_data в теле запроса (backward compat)
      3. Query-параметр init_data (для GET-запросов)
      4. В DEBUG-режиме — возвращаем admin-заглушку
    """
    # 1. Заголовок (новый способ)
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

    # 2. Тело запроса (старый способ — совместимость)
    if not init_data and body:
        init_data = body.get("init_data", "").strip()

    # 3. Query-параметр (GET-запросы)
    if not init_data:
        init_data = request.rel_url.query.get("init_data", "").strip()

    user = verify_init_data(init_data, BOT_TOKEN) if init_data else None

    if user is None and DEBUG:
        logger.warning("DEBUG: пропускаем проверку initData")
        user = {"id": ADMIN_ID or 0}

    return user


def _unauthorized() -> web.Response:
    return web.json_response({"error": "Unauthorized"}, status=401)


# ── VPN хендлеры ───────────────────────────────────────────────────────────────

async def handle_vpn_invoice(request: web.Request) -> web.Response:
    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan = VPN_PLANS.get(body.get("plan_key", ""))
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)

    # Блокируем покупку если уже есть активная подписка
    existing_sub = await get_active_subscription(user["id"])
    if existing_sub:
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    bot: Bot = request.app["bot"]
    url = await bot.create_invoice_link(
        title=f"VPN {plan['name']}",
        description=f"Доступ к VPN на 30 дней. Amnezia WireGuard.",
        payload=body["plan_key"],
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["stars"])],
        provider_token="",
    )
    logger.info("VPN invoice: user=%s plan=%s", user.get("id"), body["plan_key"])
    return web.json_response({"invoice_url": url})


async def handle_vpn_configs(request: web.Request) -> web.Response:
    """Возвращает список конфигов пользователя с данными сервера и трафиком."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    configs = await get_user_configs_full(user["id"])

    # Форматируем трафик и убираем чувствительные поля
    result = []
    for c in configs:
        result.append({
            "id":           c["id"],
            "protocol":     c["protocol"],
            "label":        c["label"] or c["peer_name"] or f"Устройство #{c['slot_num']}",
            "slot_num":     c["slot_num"],
            "status":       c["status"],
            "has_config":   bool(c["config_data"]),
            "assigned_ip":  c.get("assigned_ip", ""),
            "rx_bytes":     c.get("rx_bytes", 0),
            "tx_bytes":     c.get("tx_bytes", 0),
            "rx_human":     _fmt_bytes(c.get("rx_bytes", 0)),
            "tx_human":     _fmt_bytes(c.get("tx_bytes", 0)),
            "last_seen":    c.get("last_seen"),
            "plan":         c["plan"],
            "expires_at":   c["expires_at"],
            "sub_status":   c["sub_status"],
            "server_name":  c.get("server_name") or "",
            "server_flag":  c.get("flag") or "🌍",
            "server_city":  c.get("city") or "",
        })
    return web.json_response(result)


def _fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b/1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b/1024**2:.1f} MB"
    else:
        return f"{b/1024**3:.2f} GB"


async def handle_vpn_config_download(request: web.Request) -> web.Response:
    """Отдаёт .conf файл для скачивания."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = int(request.match_info["id"])
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if not config.get("config_data"):
        return web.json_response({"error": "Config not ready yet"}, status=404)

    filename = f"{config['peer_name'] or f'vpn_config_{config_id}'}.conf"
    return web.Response(
        body=config["config_data"].encode(),
        content_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def handle_vpn_config_qr(request: web.Request) -> web.Response:
    """Возвращает QR-код конфига как PNG."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = int(request.match_info["id"])
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if not config.get("config_data"):
        return web.json_response({"error": "Config not ready yet"}, status=404)

    import io
    import qrcode  # type: ignore
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=6, border=2)
    qr.add_data(config["config_data"])
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return web.Response(body=buf.getvalue(), content_type="image/png",
                        headers={"Cache-Control": "no-store"})


async def handle_vpn_servers(request: web.Request) -> web.Response:
    """Список активных серверов для протокола: GET /api/vpn/servers?protocol=awg"""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    protocol = request.rel_url.query.get("protocol", "awg")
    servers = await get_servers_by_protocol(protocol)
    # Не отдаём чувствительные поля (пароль, ключ)
    safe = [{"id": s["id"], "name": s["name"], "location": s["location"]} for s in servers]
    return web.json_response(safe)


async def handle_vpn_status(request: web.Request) -> web.Response:
    """
    Быстрая проверка доступности серверов.
    Возвращает список серверов с полем ok (True/False).
    Не требует авторизации — публичный эндпоинт.
    """
    import asyncio
    import socket

    async def _ping(server: dict) -> dict:
        host = server.get("host", "")
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyname, host),
                timeout=3.0,
            )
            ok = True
        except Exception:
            ok = False
        return {
            "id":       server["id"],
            "name":     server["name"],
            "location": server["location"],
            "ok":       ok,
        }

    all_servers = await get_servers_by_protocol("awg")
    results = await asyncio.gather(*[_ping(s) for s in all_servers])
    return web.json_response(list(results))


async def handle_vpn_config_activate(request: web.Request) -> web.Response:
    """
    Активирует пустой слот.
    Body: { server_id: number }  — сервер выбирает пользователь в UI.
    Если server_id не передан — берём первый активный сервер протокола.
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = int(request.match_info["id"])
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if config["status"] != "empty":
        return web.json_response({"error": "Слот уже активен"}, status=400)

    if config["protocol"] == "vless":
        return web.json_response({"error": "VLESS будет доступен в ближайшее время"}, status=400)

    body = await request.json()
    server_id = body.get("server_id")

    # Получаем сервер из БД
    if server_id:
        server = await get_server_by_id(server_id)
        if not server or not server["is_active"]:
            return web.json_response({"error": "Сервер недоступен"}, status=400)
    else:
        servers = await get_servers_by_protocol(config["protocol"])
        if not servers:
            return web.json_response({"error": "Нет доступных серверов"}, status=503)
        server = servers[0]
        server_id = server["id"]

    if not server.get("agent_url") or not server.get("agent_token"):
        logger.error("Server %s has no agent_url/agent_token", server.get("name", server["id"]))
        return web.json_response({"error": "Сервер не настроен (нет агента)"}, status=503)

    peer_name = f"tg{user['id']}_{config_id}"

    from services.vpnctl_client import provision_peer, VpnctlError

    try:
        result = await provision_peer(server, peer_name, config["protocol"])
    except VpnctlError as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e)
        return web.json_response({"error": "Ошибка создания конфига на сервере"}, status=503)
    except Exception as e:
        logger.error("Activate slot #%d on server %s: %s", config_id, server.get("name", server["id"]), e)
        return web.json_response({"error": "Сервер недоступен"}, status=503)

    config_data = result.wg_config if config["protocol"] == "awg" else result.vless_url
    if not config_data:
        return web.json_response({"error": "Ошибка создания конфига на сервере"}, status=503)

    wg_pubkey = result.public_key if config["protocol"] == "awg" else None
    vless_uuid = result.public_key if config["protocol"] == "vless" else None
    await activate_config_slot(
        config_id, peer_name, config_data, server_id,
        wg_pubkey=wg_pubkey, assigned_ip=result.assigned_ip or None, vless_uuid=vless_uuid,
    )
    logger.info("Слот #%d активирован на %s (%s)", config_id, server["name"], peer_name)
    return web.json_response({"ok": True})


async def handle_vpn_config_revoke(request: web.Request) -> web.Response:
    """Отзывает конфиг пользователя."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    config_id = int(request.match_info["id"])
    config = await get_config_by_id(config_id)

    if not config or config["user_id"] != user["id"]:
        return web.json_response({"error": "Not found"}, status=404)

    if config["status"] != "active":
        return web.json_response({"error": "Слот не активен"}, status=400)

    # Удаляем пир с сервера через vpnctl (best-effort)
    if config.get("peer_name") and config.get("server_id"):
        try:
            srv = await get_server_by_id(config["server_id"])
            if srv:
                from services.vpnctl_client import revoke_peer
                await revoke_peer(srv, config.get("wg_pubkey"), config.get("vless_uuid"), config["protocol"])
        except Exception as e:
            logger.warning("Не удалось удалить пир %s: %s", config["peer_name"], e)

    # Сбрасываем слот в empty — он остаётся доступным для повторной активации
    await reset_config_slot(config_id)
    logger.info("Слот #%d сброшен в empty пользователем %s", config_id, user["id"])
    return web.json_response({"ok": True})


# ── CryptoBot хендлеры ────────────────────────────────────────────────────────

async def handle_cryptobot_invoice(request: web.Request) -> web.Response:
    """
    POST /api/vpn/invoice/crypto  { plan_key, currency: "RUB"|"USD" }
    Создаёт инвойс через CryptoBot и возвращает { pay_url }.
    """
    if not CRYPTOBOT_TOKEN:
        return web.json_response({"error": "CryptoBot не настроен"}, status=503)

    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    plan = VPN_PLANS.get(body.get("plan_key", ""))
    if not plan:
        return web.json_response({"error": "Unknown plan"}, status=400)

    currency = body.get("currency", "RUB").upper()
    if currency not in ("RUB", "USD"):
        return web.json_response({"error": "currency must be RUB or USD"}, status=400)

    existing_sub = await get_active_subscription(user["id"])
    if existing_sub:
        return web.json_response(
            {"error": "У тебя уже есть активная подписка. Используй смену тарифа."},
            status=400,
        )

    amount  = plan["rub"] if currency == "RUB" else plan["usd"]
    payload = f"vpn:{user['id']}:{body['plan_key']}"

    from services.cryptobot import create_invoice
    from aiogram import Bot
    bot: Bot = request.app["bot"]
    bot_info = await bot.get_me()

    try:
        invoice = await create_invoice(
            CRYPTOBOT_TOKEN,
            fiat=currency,
            amount=amount,
            payload=payload,
            description=f"VPN {plan['name']} — 30 дней · Amnezia WireGuard",
            bot_username=bot_info.username,
        )
    except Exception as e:
        logger.error("CryptoBot invoice error: %s", e)
        return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

    pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url", "")
    logger.info("CryptoBot invoice: user=%s plan=%s cur=%s url=%s",
                user.get("id"), body["plan_key"], currency, pay_url)
    return web.json_response({"pay_url": pay_url})


async def handle_cryptobot_webhook(request: web.Request) -> web.Response:
    """
    POST /api/cryptobot/webhook
    CryptoBot уведомляет об оплате инвойса.
    """
    if not CRYPTOBOT_TOKEN:
        return web.Response(status=200)

    body_bytes = await request.read()
    signature  = request.headers.get("crypto-pay-api-signature", "")

    from services.cryptobot import verify_signature
    if not verify_signature(body_bytes, signature, CRYPTOBOT_TOKEN):
        logger.warning("CryptoBot webhook: invalid signature")
        return web.Response(status=401)

    import json
    data = json.loads(body_bytes)

    if data.get("update_type") != "invoice_paid":
        return web.Response(status=200)

    invoice = data.get("payload", {})
    raw_payload = invoice.get("payload", "")
    logger.info("CryptoBot payment: invoice_id=%s payload=%s",
                invoice.get("invoice_id"), raw_payload)

    # payload format: "vpn:USER_ID:PLAN_KEY"
    parts = raw_payload.split(":")
    if len(parts) != 3 or parts[0] != "vpn":
        logger.warning("CryptoBot webhook: unexpected payload %s", raw_payload)
        return web.Response(status=200)

    user_id  = int(parts[1])
    plan_key = parts[2]
    plan     = VPN_PLANS.get(plan_key)
    if not plan:
        logger.warning("CryptoBot webhook: unknown plan %s", plan_key)
        return web.Response(status=200)

    payment_id = f"crypto_{invoice.get('invoice_id')}"

    from services.database import (
        get_subscription_by_payment_id, create_subscription,
        create_order, complete_order, create_config_record,
    )
    from datetime import datetime, timedelta

    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("CryptoBot: duplicate payment %s", payment_id)
        return web.Response(status=200)

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])
    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=0,
        expires_at=expires_at,
    )

    order_id = await create_order(
        user_id=user_id,
        product_type="vpn",
        plan=plan_key,
        stars_paid=0,
        expires_at=expires_at,
    )
    await complete_order(order_id, payment_id=payment_id)

    for _ in range(plan["awg_slots"]):
        await create_config_record(subscription_id=sub_id, user_id=user_id, protocol="awg")
    for _ in range(plan["vless_slots"]):
        await create_config_record(subscription_id=sub_id, user_id=user_id, protocol="vless")

    # Уведомляем пользователя в Telegram
    try:
        bot: Bot = request.app["bot"]
        paid_amount = invoice.get("paid_amount", "")
        paid_asset  = invoice.get("paid_asset", "")
        await bot.send_message(
            user_id,
            f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
            f"💎 Оплата: {paid_amount} {paid_asset}\n"
            f"📅 Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
            "Открой мини-апп → <b>Мои конфиги</b> и добавь конфиг на устройство.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("CryptoBot: failed to notify user %d: %s", user_id, e)

    return web.Response(status=200)


# ── eSIM хендлеры ──────────────────────────────────────────────────────────────

async def handle_esim_countries(request: web.Request) -> web.Response:
    countries = await esim.get_countries()
    return web.json_response(countries)


async def handle_esim_packages(request: web.Request) -> web.Response:
    country = request.rel_url.query.get("country", "")
    if not country:
        return web.json_response({"error": "country required"}, status=400)
    packages = await esim.get_packages_for(country.upper())
    return web.json_response(packages)


async def handle_esim_invoice(request: web.Request) -> web.Response:
    body = await request.json()
    user = _resolve_user(request, body)
    if user is None:
        return _unauthorized()

    pkg_code = body.get("package_code", "")
    price    = body.get("price", 0)
    stars    = body.get("stars", 0)
    name     = body.get("name", "eSIM")

    if not pkg_code or not stars:
        return web.json_response({"error": "Invalid params"}, status=400)

    bot: Bot = request.app["bot"]
    payload = f"esim:{pkg_code}:{price}"
    url = await bot.create_invoice_link(
        title=name,
        description=f"eSIM: {name}. Активация при первом подключении.",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=name, amount=stars)],
        provider_token="",
    )
    logger.info("eSIM invoice: user=%s pkg=%s stars=%d", user.get("id"), pkg_code, stars)
    return web.json_response({"invoice_url": url})


async def handle_vpn_subscription(request: web.Request) -> web.Response:
    """GET /api/vpn/subscription — активная подписка пользователя."""
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    sub = await get_active_subscription(user["id"])
    if sub is None:
        return web.json_response(None)

    from datetime import datetime
    expires = datetime.fromisoformat(sub["expires_at"])
    remaining_days = max(0, (expires - datetime.utcnow()).days)

    return web.json_response({
        "id":            sub["id"],
        "plan":          sub["plan"],
        "stars_paid":    sub["stars_paid"],
        "expires_at":    sub["expires_at"],
        "pending_plan":  sub["pending_plan"],
        "days_remaining": remaining_days,
    })


async def handle_vpn_change_plan(request: web.Request) -> web.Response:
    """
    POST /api/vpn/subscription/change { plan_key }
    Апгрейд  → возвращает { invoice_url }
    Даунгрейд → возвращает { ok: true, scheduled: true }
    Отмена даунгрейда → возвращает { ok: true, cancelled: true }
    """
    user = _resolve_user(request)
    if user is None:
        return _unauthorized()

    body     = await request.json()
    plan_key = body.get("plan_key", "")
    new_plan = VPN_PLANS.get(plan_key)
    if not new_plan:
        return web.json_response({"error": "Неизвестный тариф"}, status=400)

    sub = await get_active_subscription(user["id"])
    if sub is None:
        return web.json_response({"error": "Нет активной подписки"}, status=400)

    cur_plan = VPN_PLANS.get(sub["plan"])
    if cur_plan is None:
        return web.json_response({"error": "Ошибка: текущий тариф не распознан"}, status=400)

    if plan_key == sub["plan"]:
        return web.json_response({"ok": True, "same": True})

    from datetime import datetime
    expires       = datetime.fromisoformat(sub["expires_at"])
    remaining_days = max(0, (expires - datetime.utcnow()).days)

    is_upgrade = new_plan["stars"] > cur_plan["stars"]

    if is_upgrade:
        # Доплата пропорционально оставшимся дням, в рублях
        cur_rub = int(cur_plan.get("rub", cur_plan["stars"]))
        new_rub = int(new_plan.get("rub", new_plan["stars"]))
        rub_price = max(1, round((new_rub - cur_rub) * remaining_days / 30))

        awg_delta   = new_plan["awg_slots"]   - cur_plan["awg_slots"]
        vless_delta = new_plan["vless_slots"] - cur_plan["vless_slots"]

        if not CRYPTOBOT_TOKEN:
            return web.json_response({"error": "Оплата апгрейда временно недоступна"}, status=503)

        from services.cryptobot import create_invoice
        bot: Bot = request.app["bot"]
        bot_info = await bot.get_me()
        payload  = f"plan_upgrade:{sub['id']}:{plan_key}:{awg_delta}:{vless_delta}"

        try:
            invoice = await create_invoice(
                CRYPTOBOT_TOKEN,
                fiat="RUB",
                amount=str(rub_price),
                payload=payload,
                description=f"Апгрейд до «{new_plan['name']}». Доплата за {remaining_days} дн.",
                bot_username=bot_info.username,
            )
        except Exception as e:
            logger.error("CryptoBot upgrade invoice error: %s", e)
            return web.json_response({"error": "Ошибка платёжного сервиса"}, status=503)

        pay_url = invoice.get("mini_app_invoice_url") or invoice.get("bot_invoice_url", "")
        return web.json_response({"invoice_url": pay_url})

    else:
        # Даунгрейд — планируем на следующий месяц
        # Если уже запланирован тот же — отменяем
        if sub.get("pending_plan") == plan_key:
            await schedule_plan_change(sub["id"], None)
            return web.json_response({"ok": True, "cancelled": True})

        await schedule_plan_change(sub["id"], plan_key)
        return web.json_response({"ok": True, "scheduled": True})


# ── Статистика пользователя ────────────────────────────────────────────────────

async def handle_user_stats(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    uid = user["id"]
    from services.database import DB_PATH
    import aiosqlite as _sq
    async with _sq.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(stars_paid),0) FROM subscriptions WHERE user_id=?", (uid,)
        ) as cur:
            stars_spent = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(ref_bonus_days,0) FROM users WHERE id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
            bonus_days = row[0] if row else 0
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)
        ) as cur:
            invited = (await cur.fetchone())[0]

    return web.json_response({
        "stars_spent": stars_spent,
        "bonus_days":  bonus_days,
        "invited":     invited,
    })


# ── Реферальная программа ─────────────────────────────────────────────────────

async def handle_referral_stats(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    bot: Bot = request.app["bot"]
    bot_info = await bot.get_me()
    stats = await db_get_referral_stats(user["id"])
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user['id']}"
    return web.json_response({
        "ref_link":   ref_link,
        "invited":    stats["invited"],
        "converted":  stats["converted"],
        "bonus_days": stats["bonus_days"],
    })


# ── Поддержка ──────────────────────────────────────────────────────────────────

CATEGORY_LABELS: dict[str, str] = {
    "vpn":     "Проблема с VPN",
    "esim":    "Проблема с eSIM",
    "payment": "Вопрос по оплате",
    "other":   "Другое",
}

async def handle_support_ticket(request: web.Request) -> web.Response:
    user = _resolve_user(request)
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Bad request"}, status=400)

    category = str(body.get("category", "other"))
    message  = str(body.get("message", "")).strip()
    if not message:
        return web.json_response({"error": "Пустое сообщение"}, status=400)
    if len(message) > 2000:
        return web.json_response({"error": "Сообщение слишком длинное"}, status=400)

    ticket_id = await create_support_ticket(user["id"], category, message)

    bot: Bot = request.app["bot"]
    cat_label = CATEGORY_LABELS.get(category, category)
    username  = f"@{user['username']}" if user.get("username") else f"id:{user['id']}"
    name      = user.get("first_name") or "—"
    text = (
        f"🎫 <b>Тикет #{ticket_id}</b>\n"
        f"👤 {name} ({username})\n"
        f"📂 {cat_label}\n\n"
        f"{message}"
    )
    try:
        sent = await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        await update_ticket_admin_msg(ticket_id, sent.message_id)
    except Exception as e:
        logger.warning("Не удалось отправить тикет #%d админу: %s", ticket_id, e)

    return web.json_response({"ok": True, "ticket_id": ticket_id})


# ── CORS middleware ────────────────────────────────────────────────────────────

ALLOWED_ORIGINS = {
    "https://lemonov911.github.io",
    "http://localhost:5173",
    "http://localhost:4173",
}

@web.middleware
async def cors_middleware(request: web.Request, handler):
    origin = request.headers.get("Origin", "")
    allow_origin = origin if origin in ALLOWED_ORIGINS else ""

    # Preflight
    if request.method == "OPTIONS":
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin":  allow_origin or "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
                "Access-Control-Max-Age":       "86400",
            },
        )

    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"]  = allow_origin or "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
    return response


# ── Фабрика приложения ─────────────────────────────────────────────────────────

def create_api_app(bot: Bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot

    # VPN
    app.router.add_post("/api/vpn/invoice",                handle_vpn_invoice)
    app.router.add_post("/api/vpn/invoice/crypto",         handle_cryptobot_invoice)
    app.router.add_get ("/api/vpn/configs",                handle_vpn_configs)
    app.router.add_get ("/api/vpn/servers",                handle_vpn_servers)
    app.router.add_get ("/api/vpn/status",                 handle_vpn_status)
    app.router.add_get ("/api/vpn/config/{id}/download",   handle_vpn_config_download)
    app.router.add_get ("/api/vpn/config/{id}/qr",        handle_vpn_config_qr)
    app.router.add_post("/api/vpn/config/{id}/activate",   handle_vpn_config_activate)
    app.router.add_post("/api/vpn/config/{id}/revoke",     handle_vpn_config_revoke)
    app.router.add_get ("/api/vpn/subscription",           handle_vpn_subscription)
    app.router.add_post("/api/vpn/subscription/change",    handle_vpn_change_plan)

    # CryptoBot webhook
    app.router.add_post("/api/cryptobot/webhook",          handle_cryptobot_webhook)

    # eSIM
    app.router.add_get ("/api/esim/countries",             handle_esim_countries)
    app.router.add_get ("/api/esim/packages",              handle_esim_packages)
    app.router.add_post("/api/esim/invoice",               handle_esim_invoice)

    # Поддержка
    app.router.add_post("/api/support/ticket",             handle_support_ticket)

    # Статистика пользователя
    app.router.add_get ("/api/user/stats",                 handle_user_stats)

    # Реферальная программа
    app.router.add_get ("/api/referral/stats",             handle_referral_stats)

    return app
