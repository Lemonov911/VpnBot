"""
VPN purchase flow + eSIM delivery.

Новые тарифы (задача 5):
  vpn_start   — 128★  1 AWG
  vpn_popular — 214★  2 AWG
  vpn_pro     — 342★  3 AWG + 1 VLESS (теоретический)
  vpn_family  — 513★  7 AWG + 1 VLESS (теоретический)

Старые тарифы оставлены для обратной совместимости существующих заказов.
"""

import logging
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    PreCheckoutQuery,
)

from services.database import (
    create_order,
    complete_order,
    create_subscription,
    get_subscription_by_payment_id,
    create_config_record,
    has_active_subscription,
    change_subscription_plan,
    add_referral_bonus,
    get_best_server,
    save_peer_to_config,
    update_server_peer_count,
    record_payment,
)
import aiosqlite as _aiosqlite
from pathlib import Path as _Path
_DB_PATH = _Path(__file__).parent.parent / "bot.db"
from services.payments import stars_invoice_kwargs
from services.vpnctl_client import provision_peer, VpnctlError
import services.esim_api as esim_api

logger = logging.getLogger(__name__)

router = Router()

# ── Тарифы ─────────────────────────────────────────────────────────────────────

VPN_PLANS: dict[str, dict] = {
    # ── v2 тарифы по скорости (Reality only) ──
    # speed_mbps — гарантированная скорость, soft_cap_gb — мягкий лимит трафика,
    # после которого скорость падает до throttle_mbps до конца месяца.
    "vpn_base": {
        "name":           "База",
        "stars":          145,            # ≈ 200 ₽
        "duration_days":  30,
        "awg_slots":      0,
        "vless_slots":    5,
        "speed_mbps":     60,
        "soft_cap_gb":    500,
        "throttle_mbps":  5,
        "description":    "2 человека в 4K + телефоны в фоне",
    },
    "vpn_max": {
        "name":           "Макс",
        "stars":          360,            # ≈ 500 ₽
        "duration_days":  30,
        "awg_slots":      0,
        "vless_slots":    10,
        "speed_mbps":     120,
        "soft_cap_gb":    1000,
        "throttle_mbps":  15,
        "description":    "Семья 3+ чел / стриминг + торренты",
    },

    # ── Legacy тарифы (для уже-купивших, в UI скрыты) ──
    "vpn_start":   {"name": "Старт",      "stars": 128,  "duration_days": 30, "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_popular": {"name": "Популярный", "stars": 214,  "duration_days": 30, "awg_slots": 2, "vless_slots": 0, "legacy": True},
    "vpn_pro":     {"name": "Про",        "stars": 342,  "duration_days": 30, "awg_slots": 3, "vless_slots": 1, "legacy": True},
    "vpn_family":  {"name": "Семейный",   "stars": 513,  "duration_days": 30, "awg_slots": 7, "vless_slots": 1, "legacy": True},
    "vpn_1m":      {"name": "1 месяц",    "stars": 299,  "duration_days": 30,  "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_3m":      {"name": "3 месяца",   "stars": 699,  "duration_days": 90,  "awg_slots": 1, "vless_slots": 0, "legacy": True},
    "vpn_1y":      {"name": "1 год",      "stars": 1990, "duration_days": 365, "awg_slots": 1, "vless_slots": 0, "legacy": True},
}

PLANS_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⭐ База — 145 ⭐️ · 60 Mbps · 5 устройств",     callback_data="vpn:buy:vpn_base")],
    [InlineKeyboardButton(text="🚀 Макс — 360 ⭐️ · 120 Mbps · 10 устройств",   callback_data="vpn:buy:vpn_max")],
    [InlineKeyboardButton(text="📖 Как настроить?",                              callback_data="vpn:howto")],
    [InlineKeyboardButton(text="◀️ Назад",                                       callback_data="menu:start")],
])

HOWTO_TEXT = (
    "📖 <b>Как настроить VPN</b>\n\n"
    "<b>1. Скачай приложение</b> (рекомендуем <b>Happ</b>):\n"
    "   • <a href=\"https://apps.apple.com/app/happ-proxy-utility/id6504287215\">iOS</a>\n"
    "   • <a href=\"https://play.google.com/store/apps/details?id=com.happproxy\">Android</a>\n"
    "   • Альтернативы: Streisand, V2Box, Shadowrocket\n\n"
    "<b>2. После оплаты</b> я пришлю vless://-ссылку\n\n"
    "<b>3. В Happ:</b>\n"
    "   • Нажми <b>«+»</b> → <b>«Из буфера обмена»</b>\n"
    "   • Или скопируй ссылку и вставь вручную\n\n"
    "<b>4. Включи переключатель</b> — готово! ✅\n\n"
    "💡 Если какие-то российские сайты не открываются (Сбер, Госуслуги) — "
    "напиши в поддержку, добавим в исключения"
)

def vless_service_for_plan(plan_key: str) -> str:
    """Resolves vpnctl service-name for a given plan_key.
    New v2 plans map to speed-tier services; legacy/unknown fallback to 'vless'."""
    if plan_key == "vpn_base":
        return "vless-base"
    if plan_key == "vpn_max":
        return "vless-max"
    return "vless"


def vless_slow_service_for_plan(plan_key: str) -> str | None:
    """Throttled service for a plan, used after soft-cap is exceeded.
    Returns None for legacy plans without a slow-tier."""
    if plan_key == "vpn_base":
        return "vless-base-slow"
    if plan_key == "vpn_max":
        return "vless-max-slow"
    return None


MOCK_CONFIG_TEMPLATE = """\
# ТЕСТОВЫЙ КОНФИГ — сервер ещё не подключён
# Рабочий файл придёт автоматически когда сервер будет готов

[Interface]
PrivateKey = PLACEHOLDER
Address = 10.8.0.X/32
DNS = 1.1.1.1, 1.0.0.1

[Peer]
PublicKey = PLACEHOLDER
PresharedKey = PLACEHOLDER
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = VPN_SERVER:51820
PersistentKeepalive = 25
"""


# ── Меню ───────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:vpn")
async def show_vpn_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🌐 <b>VPN — обход блокировок и приватность</b>\n\n"
        "Протокол: <b>VLESS + Reality</b> — маскируется под обычный сайт, "
        "не палится DPI и ТСПУ\n"
        "Локация: 🇩🇪 Frankfurt\n"
        "Soft-лимит трафика, после — медленнее, но не отключение\n\n"
        "<b>Тарифы:</b>\n"
        "• <b>База</b> 60 Mbps — 2 человека в 4K + телефоны в фоне\n"
        "• <b>Макс</b> 120 Mbps — семья / стриминг + торренты\n",
        reply_markup=PLANS_KEYBOARD,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "vpn:howto")
async def show_howto(callback: CallbackQuery):
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="menu:vpn")]
    ])
    await callback.message.edit_text(
        HOWTO_TEXT, reply_markup=back_kb, parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:start")
async def back_to_start(callback: CallbackQuery):
    from handlers.start import MAIN_MENU
    await callback.message.edit_text(
        "👋 Привет! Выбери, что тебя интересует:",
        reply_markup=MAIN_MENU,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:esim")
async def esim_coming_soon(callback: CallbackQuery):
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:start")]
    ])
    await callback.message.edit_text(
        "📱 <b>eSIM — скоро!</b>\n\nПродажа зарубежных eSIM карт появится совсем скоро.",
        reply_markup=back_kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ── Покупка через бота (inline keyboard) ───────────────────────────────────────

@router.callback_query(F.data.startswith("vpn:buy:"))
async def initiate_purchase(callback: CallbackQuery, bot: Bot):
    plan_key = callback.data.split(":")[-1]
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return

    if await has_active_subscription(callback.from_user.id):
        await callback.answer(
            "У тебя уже есть активная подписка.\nСмени тариф в мини-апп.",
            show_alert=True,
        )
        return

    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        **stars_invoice_kwargs(
            title=f"VPN {plan['name']}",
            description=(
                f"Доступ к VPN на {plan['duration_days']} дней. "
                "Протокол Amnezia WireGuard."
            ),
            payload=plan_key,
            stars=plan["stars"],
        ),
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


# ── Обработка успешного платежа ────────────────────────────────────────────────

@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot):
    payment = message.successful_payment
    payload = payment.invoice_payload

    # eSIM — отдельный обработчик
    if payload.startswith("esim:"):
        await _deliver_esim(message, bot, payment)
        return

    # Апгрейд тарифа
    if payload.startswith("plan_upgrade:"):
        await _apply_plan_upgrade(message, payment)
        return

    # VPN
    plan = VPN_PLANS.get(payload)
    if not plan:
        await message.answer("⚠️ Ошибка: неизвестный тариф. Напиши в поддержку.")
        return

    await _deliver_vpn(message, payment, plan, payload)


async def _deliver_vpn(message: Message, payment, plan: dict, plan_key: str):
    """Доставка VPN-конфигов после успешной оплаты."""
    user_id    = message.from_user.id
    payment_id = payment.telegram_payment_charge_id

    # Защита от повторной обработки одного платежа (задача 4)
    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("Дубль платежа %s для user %d — игнорируем", payment_id, user_id)
        return

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])

    # Создаём подписку в новой таблице
    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=payment.total_amount,
        expires_at=expires_at,
    )

    # Для обратной совместимости — дублируем в orders
    order_id = await create_order(
        user_id=user_id,
        product_type="vpn",
        plan=plan_key,
        stars_paid=payment.total_amount,
        expires_at=expires_at,
    )
    await complete_order(order_id, payment_id=payment_id)

    expiry_str  = expires_at.strftime("%d.%m.%Y")
    awg_slots   = plan.get("awg_slots", 1)
    vless_slots = plan.get("vless_slots", 0)

    # Записываем платёж в историю
    await record_payment(
        user_id=user_id,
        subscription_id=sub_id,
        method="stars",
        stars=payment.total_amount,
        tx_id=payment_id,
    )

    # Создаём реальные пиры через vpnctl (или пустые слоты если агент недоступен)
    created_wg    = 0
    created_vless = 0

    for i in range(awg_slots):
        config_id = await create_config_record(sub_id, user_id, protocol="awg")
        server = await get_best_server("awg")
        if server:
            try:
                label = f"user_{user_id}_wg_{i+1}"
                peer = await provision_peer(server, label, "awg")
                peer_ip = (peer.extra or {}).get("assigned_ip", "")
                await save_peer_to_config(
                    config_id, server["id"], peer.id,
                    peer_ip, peer.config, label,
                )
                await update_server_peer_count(server["id"], +1)
                created_wg += 1
                await message.answer_document(
                    BufferedInputFile(
                        peer.config.encode(),
                        filename=f"maxvpn_{i+1}.conf",
                    ),
                    caption=f"📁 <b>WireGuard конфиг #{i+1}</b>\nСервер: {server.get('flag','')} {server.get('name','')}\nIP: {peer_ip}",
                    parse_mode="HTML",
                )
            except VpnctlError as e:
                logger.warning("vpnctl WG peer error: %s", e)

    vless_service = vless_service_for_plan(plan_key)
    for i in range(vless_slots):
        config_id = await create_config_record(sub_id, user_id, protocol="vless")
        server = await get_best_server("vless")
        if server:
            try:
                label = f"user_{user_id}_vless_{i+1}"
                peer = await provision_peer(server, label, vless_service)
                await save_peer_to_config(
                    config_id, server["id"], peer.id,
                    "", peer.config, label,
                )
                await update_server_peer_count(server["id"], +1)
                created_vless += 1
            except VpnctlError as e:
                logger.warning("vpnctl VLess peer error: %s", e)

    slots_desc = f"{awg_slots} WireGuard"
    if vless_slots:
        slots_desc += f" + {vless_slots} VLess"

    delivered = created_wg + created_vless
    total     = awg_slots + vless_slots

    if delivered == total:
        note = "Конфиги отправлены выше 👆"
    elif delivered > 0:
        note = f"Часть конфигов ({delivered}/{total}) готова, остальные появятся в мини-апп позже."
    else:
        note = "Конфиги появятся в мини-апп → <b>Мои конфиги</b> как только серверы будут готовы."

    # Persistent subscription URL (Happ / Streisand auto-refresh при throttle)
    sub_url = ""
    if vless_slots > 0 and created_vless > 0:
        try:
            from services.database import get_or_create_sub_token
            tok = await get_or_create_sub_token(user_id)
            sub_url = f"https://maxvpnesim.com/sub/{tok}"
        except Exception as e:
            logger.warning("sub_token gen failed for user %d: %s", user_id, e)

    sub_block = (
        f"\n\n🔗 <b>Subscription URL</b> (импортируй в Happ один раз — обновляется автоматом):\n"
        f"<code>{sub_url}</code>"
        if sub_url else ""
    )

    await message.answer(
        f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
        f"📅 Действует до: <b>{expiry_str}</b>\n"
        f"🔌 Слотов: <b>{slots_desc}</b>\n\n"
        f"{note}"
        f"{sub_block}",
        parse_mode="HTML",
    )

    # Реферальный бонус: если это первая покупка и у юзера есть реферер
    try:
        async with _aiosqlite.connect(_DB_PATH) as _db:
            async with _db.execute(
                "SELECT referred_by FROM users WHERE id=?", (user_id,)
            ) as _cur:
                _row = await _cur.fetchone()
            referrer_id = _row[0] if _row and _row[0] else None

            if referrer_id:
                # Первая покупка = ровно одна подписка (только что созданная)
                async with _db.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE user_id=?", (user_id,)
                ) as _cur:
                    sub_count = (await _cur.fetchone())[0]

                if sub_count == 1:
                    from handlers.start import REFERRAL_BONUS_DAYS
                    await add_referral_bonus(referrer_id, REFERRAL_BONUS_DAYS)
                    try:
                        await message.bot.send_message(
                            referrer_id,
                            f"🎁 <b>+{REFERRAL_BONUS_DAYS} дней к подписке!</b>\n\n"
                            "Твой друг купил VPN по твоей реферальной ссылке.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
    except Exception as e:
        logger.warning("Ошибка реферального бонуса: %s", e)


async def _apply_plan_upgrade(message: Message, payment):
    """Применяет апгрейд тарифа после успешной оплаты."""
    parts = payment.invoice_payload.split(":")
    # plan_upgrade:{sub_id}:{plan_key}:{awg_delta}:{vless_delta}
    if len(parts) != 5:
        await message.answer("⚠️ Ошибка payload апгрейда. Напиши в поддержку.")
        return

    _, sub_id_str, plan_key, awg_delta_str, vless_delta_str = parts
    sub_id     = int(sub_id_str)
    awg_delta  = int(awg_delta_str)
    vless_delta = int(vless_delta_str)
    user_id    = message.from_user.id

    plan = VPN_PLANS.get(plan_key)
    if not plan:
        await message.answer("⚠️ Неизвестный тариф. Напиши в поддержку.")
        return

    await change_subscription_plan(sub_id, plan_key, user_id, awg_delta, vless_delta)

    slots_desc = f"{plan['awg_slots']} AWG"
    if plan["vless_slots"]:
        slots_desc += f" + {plan['vless_slots']} VLESS"

    await message.answer(
        f"✅ <b>Тариф изменён на «{plan['name']}»!</b>\n\n"
        f"🔌 Теперь у тебя: <b>{slots_desc}</b>\n\n"
        "Открой <b>Мои конфиги</b> — новые пустые слоты уже там.",
        parse_mode="HTML",
    )


# ── eSIM delivery ──────────────────────────────────────────────────────────────

async def _deliver_esim(message: Message, bot: Bot, payment):
    """Доставка eSIM после успешной оплаты Stars."""
    parts = payment.invoice_payload.split(":", 2)
    if len(parts) != 3:
        await message.answer("⚠️ Ошибка payload. Напиши в поддержку.")
        return

    _, pkg_code, price_str = parts
    wholesale_price = int(price_str)
    user_id   = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    order_id = await create_order(
        user_id=user_id,
        product_type="esim",
        plan=pkg_code,
        stars_paid=payment.total_amount,
    )
    await complete_order(order_id, payment_id=charge_id)

    await message.answer("⏳ Оформляем eSIM, секунду...")

    tx_id = f"tg_{user_id}_{order_id}_{uuid.uuid4().hex[:8]}"
    try:
        result = await esim_api.place_order(pkg_code, wholesale_price, tx_id)
    except Exception as e:
        logger.error("eSIM order failed: %s", e)
        await _esim_refund_and_notify(bot, user_id, charge_id, order_id)
        return

    if not result.get("success"):
        logger.error("eSIM API error: %s", result)
        await _esim_refund_and_notify(bot, user_id, charge_id, order_id)
        return

    obj       = result.get("obj") or {}
    esim_list = obj.get("esimList") or [obj]
    esim_data = esim_list[0] if esim_list else {}

    iccid   = esim_data.get("iccid", "")
    ac_code = (esim_data.get("ac") or esim_data.get("activationCode")
               or esim_data.get("lpa") or esim_data.get("qrCodeUrl") or "")

    logger.info("eSIM order #%d: iccid=%s", order_id, iccid)

    caption = (
        f"✅ <b>eSIM готов!</b>\n\n"
        f"🆔 Заказ: #{order_id}\n"
        f"📟 ICCID: <code>{iccid or '—'}</code>\n\n"
        "📖 <b>iPhone:</b> Настройки → Сотовая связь → Добавить план → Сканируй QR\n"
        "📖 <b>Android:</b> Настройки → SIM-карты → Добавить → Сканируй QR\n\n"
        "⚡️ Активируется при первом подключении к сети."
    )

    if ac_code and not ac_code.startswith("http"):
        try:
            import qrcode as qr_lib
            qr = qr_lib.QRCode(box_size=10, border=4)
            qr.add_data(ac_code)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            await message.answer_photo(
                BufferedInputFile(buf.read(), "esim_qr.png"),
                caption=caption, parse_mode="HTML",
            )
        except Exception as e:
            logger.error("QR generation failed: %s", e)
            await message.answer(caption + f"\n\n<code>{ac_code}</code>", parse_mode="HTML")
    elif ac_code.startswith("http"):
        await message.answer_photo(ac_code, caption=caption, parse_mode="HTML")
    else:
        await message.answer(
            caption + "\n\n⚠️ QR-код будет отправлен отдельно в течение нескольких минут.",
            parse_mode="HTML",
        )


async def _esim_refund_and_notify(bot: Bot, user_id: int, charge_id: str, order_id: int):
    """Возврат Stars и уведомление при ошибке eSIM."""
    try:
        await bot.refund_star_payment(user_id, charge_id)
        await bot.send_message(
            user_id,
            f"❌ <b>Не удалось оформить eSIM</b> (заказ #{order_id}).\n\n"
            "Звёзды возвращены. Попробуй ещё раз или напиши в поддержку.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Refund failed: %s", e)
