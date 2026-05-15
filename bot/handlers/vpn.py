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
from services.payments import stars_invoice_kwargs
from services.plans import VPN_PLANS, vless_service_for_plan, vless_slow_service_for_plan  # noqa: F401
from services.vpnctl_client import provision_peer, VpnctlError

logger = logging.getLogger(__name__)

router = Router()

# Тарифы импортируются из services.plans — единственный источник истины.

PLANS_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⭐ База — 145 ⭐ (≈200 ₽) · 60 Mbps · 5 устройств",   callback_data="vpn:buy:vpn_base")],
    [InlineKeyboardButton(text="🚀 Макс — 360 ⭐ (≈500 ₽) · 120 Mbps · 10 устройств", callback_data="vpn:buy:vpn_max")],
    [InlineKeyboardButton(text="📖 Как настроить?",                              callback_data="vpn:howto")],
    [InlineKeyboardButton(text="◀️ Назад",                                       callback_data="menu:start")],
])

HOWTO_TEXT = (
    "📖 <b>Как настроить VPN — 3 шага</b>\n\n"
    "<b>1. Скачай Happ</b>:\n"
    "   • <a href=\"https://apps.apple.com/app/happ-proxy-utility/id6504287215\">iOS</a>\n"
    "   • <a href=\"https://play.google.com/store/apps/details?id=com.happproxy\">Android</a>\n"
    "   • <a href=\"https://happ.su\">Mac / Windows</a>\n\n"
    "<b>2. После оплаты</b> я пришлю <b>Subscription URL</b> — это твоя постоянная "
    "ссылка. Импортируешь её в Happ <b>один раз</b> — дальше Happ сам подтягивает "
    "обновления и переключает между серверами.\n\n"
    "<b>3. В Happ</b>: «+» → <b>«Подписка»</b> → вставь URL → жми переключатель.\n\n"
    "💡 Если получишь ещё и одиночный <code>vless://</code>-конфиг — это запасной. "
    "Subscription URL надёжнее: обновляется сам.\n\n"
    "💡 Если какой-то российский сайт не открывается (Сбер, Госуслуги) — "
    "напиши в поддержку, добавим в исключения."
)

# vless_service_for_plan / vless_slow_service_for_plan вынесены в services.plans.
# Старый MOCK_CONFIG_TEMPLATE удалён (был мёртвым кодом — нигде не использовался).


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
                "Протокол VLESS-Reality. Маскируется под обычный сайт."
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

    # Защита от повторной обработки одного платежа (задача 4).
    # Двухуровневая: (1) get-by-payment_id фастпас для уже-обработанных дублей,
    # (2) UNIQUE-constraint на subscriptions.payment_id — закрывает TOCTOU гонку
    #     если два successful_payment события прилетят почти одновременно.
    existing = await get_subscription_by_payment_id(payment_id)
    if existing:
        logger.warning("Дубль платежа %s для user %d — игнорируем", payment_id, user_id)
        return

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])

    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=payment.total_amount,
        expires_at=expires_at,
    )
    # None = UNIQUE-constraint сработал → дубль проскочил TOCTOU. Идемпотентный exit.
    if sub_id is None:
        logger.warning("Дубль платежа %s проскочил TOCTOU (UNIQUE сработал), user %d", payment_id, user_id)
        return

    # Юзер платит за тариф пока триал ещё active → его trial-VLESS-пир пойдёт
    # в grace через 1-2 дня (scheduler не различает trial/paid sub'ы при expire).
    # Happ балансирует subscription-URL между нормальным и grace пиром → юзер
    # иногда попадает на 256 кбит/с и жалуется «купил, а скорость дрянь».
    # Закрываем триал сразу: revoke его пиры и mark_expired.
    try:
        from services.database import get_user_subscriptions_by_plan
        from services.scheduler import _process_expired_subscriptions  # not used directly, just for clarity
        active_trials = await get_user_subscriptions_by_plan(user_id, "vpn_trial", status="active")
        for trial_sub in active_trials:
            await _close_trial_on_paid_purchase(trial_sub["id"], user_id)
    except Exception as e:
        logger.warning("close-trial-on-paid failed user %d: %s (продолжаем)", user_id, e)

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
    wg_slots    = plan.get("wg_slots", 0)

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

    # Plain WireGuard слоты (без AmneziaWG-обфускации) — для роутеров и клиентов
    # которым DPI не страшен / не нужна обфускация. Серверная часть — отдельный
    # `wg`-интерфейс в agent/wg/, выбирается через get_best_server("wg").
    created_plain_wg = 0
    for i in range(wg_slots):
        config_id = await create_config_record(sub_id, user_id, protocol="wg")
        server = await get_best_server("wg")
        if not server:
            continue
        try:
            label = f"user_{user_id}_plainwg_{i+1}"
            peer = await provision_peer(server, label, "wg")
            peer_ip = (peer.extra or {}).get("assigned_ip", "")
            await save_peer_to_config(
                config_id, server["id"], peer.id,
                peer_ip, peer.config, label,
            )
            await update_server_peer_count(server["id"], +1)
            created_plain_wg += 1
        except VpnctlError as e:
            logger.warning("vpnctl plain-WG peer error: %s", e)

    parts_desc = []
    if awg_slots:
        parts_desc.append(f"{awg_slots} AmneziaWG")
    if vless_slots:
        parts_desc.append(f"{vless_slots} VLess")
    if wg_slots:
        parts_desc.append(f"{wg_slots} WireGuard")
    slots_desc = " + ".join(parts_desc) or "0 слотов"

    delivered = created_wg + created_vless + created_plain_wg
    total     = awg_slots + vless_slots + wg_slots

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
            from services.database import rotate_sub_token
            tok = await rotate_sub_token(user_id)
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

    # Реферальный бонус: try_award_referral_bonus сам проверяет (а) есть ли
    # реферер у юзера, (б) это ли первая ПЛАТНАЯ подписка (триалы не считаются).
    # Возвращает referrer_id если бонус начислен, иначе None.
    try:
        from services.database import try_award_referral_bonus
        from handlers.start import REFERRAL_BONUS_DAYS
        referrer_id = await try_award_referral_bonus(user_id, REFERRAL_BONUS_DAYS)
        if referrer_id:
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


async def _close_trial_on_paid_purchase(trial_sub_id: int, user_id: int):
    """Юзер купил тариф пока триал ещё active — закрываем триал сразу.

    Без этого scheduler через сутки увидит истекающий триал и переведёт его
    в grace (256 кбит/с). Subscription URL отдаёт пиры из active+grace подписок
    → Happ балансирует между нормальным платным пиром и медленным trial-grace.
    Юзер жалуется «оплатил, а скорость не та».

    Действие: revoke trial-пиры на агенте, reset config slots, mark trial expired.
    Не grace — он триальный, не надо возвращать после.
    """
    from services.database import (
        get_configs_for_subscription, get_server_by_id, mark_subscription_expired,
        reset_config_slot, update_server_peer_count,
    )
    from services.vpnctl_client import client_for_server

    configs = await get_configs_for_subscription(trial_sub_id)
    for cfg in configs:
        server_id = cfg.get("server_id")
        if server_id:
            server = await get_server_by_id(server_id)
            if server and server.get("agent_url"):
                try:
                    client = client_for_server(server)
                    proto = cfg.get("protocol", "")
                    peer_id = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
                    if peer_id:
                        if proto == "awg":
                            await client.remove_peer("awg", peer_id)
                        elif proto in ("vless", "vless-reality"):
                            await client.remove_peer("vless-base", peer_id)
                        await update_server_peer_count(server_id, -1)
                except Exception as e:
                    logger.warning("trial close: revoke cfg #%d failed: %s", cfg["id"], e)
        await reset_config_slot(cfg["id"])
    await mark_subscription_expired(trial_sub_id)
    logger.info("triale закрыт после платной покупки: sub=%d user=%d", trial_sub_id, user_id)


async def _apply_plan_upgrade(message: Message, payment):
    """Применяет апгрейд тарифа после успешной оплаты."""
    parts = payment.invoice_payload.split(":")
    # plan_upgrade:{sub_id}:{plan_key}:{awg_delta}:{vless_delta}[:{wg_delta}]
    # wg_delta — опциональный 6-й элемент (added after launch). Старые in-flight
    # invoice'ы без него парсятся с wg_delta=0.
    if len(parts) not in (5, 6):
        await message.answer("⚠️ Ошибка payload апгрейда. Напиши в поддержку.")
        return

    _, sub_id_str, plan_key, awg_delta_str, vless_delta_str = parts[:5]
    wg_delta_str = parts[5] if len(parts) == 6 else "0"
    sub_id      = int(sub_id_str)
    awg_delta   = int(awg_delta_str)
    vless_delta = int(vless_delta_str)
    wg_delta    = int(wg_delta_str)
    user_id     = message.from_user.id

    plan = VPN_PLANS.get(plan_key)
    if not plan:
        await message.answer("⚠️ Неизвестный тариф. Напиши в поддержку.")
        return

    # Sec audit H6: проверяем что юзер обновляет СВОЮ подписку. Без этого
    # утечка invoice URL → злоумышленник платит за чужой апгрейд (для жертвы
    # это бесплатный бонус, но это нарушение модели и нечестная игра).
    from services.database import get_subscription_by_id
    sub = await get_subscription_by_id(sub_id)
    if not sub:
        logger.error("upgrade: sub #%d не найдена (payment_id=%s, user=%d)",
                     sub_id, payment.telegram_payment_charge_id, user_id)
        await message.answer("⚠️ Подписка не найдена. Напиши в поддержку.")
        return
    if sub["user_id"] != user_id:
        logger.error("upgrade SECURITY: sub #%d принадлежит user %d, оплатил %d (payment=%s)",
                     sub_id, sub["user_id"], user_id, payment.telegram_payment_charge_id)
        await message.answer("⚠️ Подписка не твоя. Если это ошибка — напиши в поддержку.")
        return

    await change_subscription_plan(sub_id, plan_key, user_id, awg_delta, vless_delta, wg_delta)

    parts_desc = []
    if plan["awg_slots"]:
        parts_desc.append(f"{plan['awg_slots']} AWG")
    if plan["vless_slots"]:
        parts_desc.append(f"{plan['vless_slots']} VLESS")
    if plan.get("wg_slots"):
        parts_desc.append(f"{plan['wg_slots']} WireGuard")
    slots_desc = " + ".join(parts_desc) or "0"

    await message.answer(
        f"✅ <b>Тариф изменён на «{plan['name']}»!</b>\n\n"
        f"🔌 Теперь у тебя: <b>{slots_desc}</b>\n\n"
        "Открой <b>Мои конфиги</b> — новые пустые слоты уже там.",
        parse_mode="HTML",
    )


# ── eSIM delivery ──────────────────────────────────────────────────────────────
# Поток:
#   1. Юзер платит ⭐ → _deliver_esim() кладёт order + esim_profile (pending),
#      зовёт place_order, сохраняет orderNo, спавнит фоновый poll.
#   2. Фоновый poll опрашивает /esim/query до 60 сек.
#   3. Параллельно может прилететь webhook ORDER_STATUS (см. webapp_api.handle_esim_webhook).
#   4. Кто первый дошёл — fulfill_esim_profile() атомарно переводит статус в 'ready'
#      и отправляет deliver_esim_to_user. Второй путь обнаруживает rowcount=0 и тихо выходит.

