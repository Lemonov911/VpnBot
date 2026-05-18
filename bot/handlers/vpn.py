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
import os
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    PreCheckoutQuery,
    WebAppInfo,
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
    create_esim_profile,
    set_esim_order_no,
    fulfill_esim_profile,
    mark_esim_failed,
    get_esim_profile,
)
from services.payments import stars_invoice_kwargs
from services.plans import VPN_PLANS, vless_service_for_plan, vless_slow_service_for_plan  # noqa: F401
from services.vpnctl_client import provision_peer, VpnctlError
import services.esim_api as esim_api

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
    "<b>1. Скачай приложение</b>:\n"
    "   • iOS / Android / Mac — <a href=\"https://apps.apple.com/app/happ-proxy-utility/id6504287215\">Happ</a> "
    "(или <a href=\"https://play.google.com/store/apps/details?id=com.happproxy\">Google Play</a>)\n"
    "   • <b>Windows</b> — <a href=\"https://amnezia.org/downloads\">Amnezia VPN</a> "
    "(не WireGuard.exe — тот даёт 1-2 Мбит/с вместо 120)\n\n"
    "<b>2. После оплаты</b> я пришлю <b>Subscription URL</b> — это твоя постоянная "
    "ссылка. Импортируешь её в Happ <b>один раз</b> — дальше Happ сам подтягивает "
    "обновления и переключает между серверами.\n\n"
    "<b>3. В Happ</b>: «+» → <b>«Подписка»</b> → вставь URL → жми переключатель.\n"
    "   <b>В Amnezia VPN (Windows)</b>: Файл → Импортировать → вставь URL.\n\n"
    "💡 Если получишь ещё и AWG-конфиг — импортируй его в "
    "<a href=\"https://apps.apple.com/app/amneziawg/id6478942365\">AmneziaWG</a> "
    "для усиленного шифрования трафика.\n\n"
    "💡 Если какой-то российский сайт не открывается (Сбер, Госуслуги) — "
    "напиши в поддержку, добавим в исключения."
)

# vless_service_for_plan / vless_slow_service_for_plan вынесены в services.plans.
# Старый MOCK_CONFIG_TEMPLATE удалён (был мёртвым кодом — нигде не использовался).


# ── Меню ───────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:vpn")
async def show_vpn_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🌐 <b>VPN — приватность и защищённое соединение</b>\n\n"
        "Протокол: <b>VLESS + Reality</b> — шифрует трафик, работает в любых сетях\n"
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


@router.message(Command("howto"))
async def cmd_howto(message: Message):
    await message.answer(HOWTO_TEXT, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "menu:start")
async def back_to_start(callback: CallbackQuery):
    # start.py экспортирует функцию _main_menu(), не объект MAIN_MENU.
    # Без trial_eligible проверки кнопка trial может пропасть, но это OK —
    # юзер всегда может вернуться в /start чтобы получить актуальное меню.
    from handlers.start import _main_menu
    from services.trial import can_claim_trial
    trial_eligible = await can_claim_trial(callback.from_user.id)
    await callback.message.edit_text(
        "👋 Привет! Выбери, что тебя интересует:",
        reply_markup=_main_menu(trial_eligible=trial_eligible),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:esim")
async def esim_menu(callback: CallbackQuery):
    import os
    from aiogram.types import WebAppInfo
    webapp_url = os.getenv("WEBAPP_URL", "")
    rows = []
    if webapp_url:
        rows.append([InlineKeyboardButton(
            text="📱 Открыть каталог eSIM",
            web_app=WebAppInfo(url=f"{webapp_url}/esim"),
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:start")])
    await callback.message.edit_text(
        "📱 <b>eSIM — мобильный интернет за рубежом</b>\n\n"
        "Покупаешь, сканируешь QR — и через 30 сек у тебя интернет в Турции, "
        "Грузии, ОАЭ, Таиланде, Вьетнаме или по всей Европе.\n\n"
        "🇷🇺 Есть отдельный тариф для России — с зарубежным IP "
        "(работает как VPN: открывает заблокированные сайты).\n\n"
        "Оплата ⭐ или картой через Telegram.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
                "Протокол VLESS-Reality. "
                "Оплачивая, принимаете условия: maxvpnesim.com/oferta"
            ),
            payload=plan_key,
            stars=plan["stars"],
        ),
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    """Подтверждаем pre_checkout если payload валидный.
    Если план был удалён между invoice creation и оплатой — отбиваем
    pre_checkout с ok=False, Telegram отменит платёж до charge'а."""
    # Ban-гейт: blocked users не должны проходить оплату.  Если бан был
    # выставлен между invoice creation и pre_checkout — отбиваем здесь.
    from services.database import is_user_banned
    if await is_user_banned(query.from_user.id):
        await query.answer(ok=False, error_message="Доступ ограничен. Напиши в поддержку.")
        return

    payload = query.invoice_payload or ""
    if not payload:
        await query.answer(ok=False, error_message="Некорректный payload — попробуй создать платёж заново.")
        return
    # Валидация по payload-type. Stars-invoice payload:
    #   "vpn_base" / "vpn_max"            — обычная покупка
    #   "esim:pkg_code:price"             — eSIM
    #   "plan_upgrade:sub_id:plan_key:.." — апгрейд
    #   "free_*"                          — admin gift (не должно прийти через pre_checkout)
    if payload.startswith("esim:") or payload.startswith("plan_upgrade:"):
        await query.answer(ok=True)
        return
    if payload in VPN_PLANS:
        # Defensive: invoice создаётся серверно (сумма не достижима из клиента),
        # но если когда-нибудь добавим клиентский путь — занижение суммы ниже
        # plan.stars сразу даёт юзеру тариф «за дёшево». Дешёвая проверка.
        expected = VPN_PLANS[payload]["stars"]
        if query.total_amount < expected:
            logger.error(
                "pre_checkout SECURITY: payload=%s total_amount=%d < expected=%d user=%d",
                payload, query.total_amount, expected, query.from_user.id,
            )
            await query.answer(ok=False, error_message="Неверная сумма платежа.")
            return

        # Audit 17.05 #11: re-check active sub.  Между invoice gen и
        # pre_checkout юзер мог parallel'но купить другой тариф (открыл
        # 2 окна Mini App / тапнул на разные планы). Без проверки получим
        # 2 active sub.  Разрешаем grace (renew-from-grace flow legitimate).
        from services.database import get_active_subscription
        existing = await get_active_subscription(query.from_user.id)
        if existing and existing.get("status") == "active":
            # У юзера УЖЕ active sub. На Stars нет auto-renew через
            # pre_checkout (recurring обходит pre_checkout) → значит это
            # либо double-purchase, либо upgrade через wrong flow.
            existing_plan = existing.get("plan", "")
            if existing_plan == payload:
                msg = f"У тебя уже активная подписка «{VPN_PLANS[payload]['name']}». Открой Mini App."
            else:
                msg = "У тебя уже есть активная подписка. Для смены тарифа используй кнопку «Улучшить» в Mini App."
            logger.warning(
                "pre_checkout REJECT user=%d: existing active sub plan=%s tried=%s",
                query.from_user.id, existing_plan, payload,
            )
            await query.answer(ok=False, error_message=msg)
            return

        await query.answer(ok=True)
        return
    # Unknown plan_key (мог быть удалён из VPN_PLANS пока юзер тормозил)
    logger.warning("pre_checkout: unknown payload=%r user=%d", payload, query.from_user.id)
    await query.answer(ok=False, error_message="Тариф больше недоступен. Открой меню заново.")


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

    # Telegram Stars subscription auto-renewal:
    #   is_first_recurring=True → первая оплата подписки (создаём sub с auto_renew=True)
    #   is_recurring=True (без first) → ежемесячное продление (extend существующего sub)
    # Если оба флага отсутствуют — обычный one-time платёж (старый flow).
    if getattr(payment, "is_recurring", False) and not getattr(payment, "is_first_recurring", False):
        await _handle_stars_renewal(message, bot, payment, plan, payload)
        return

    is_first_recurring = bool(getattr(payment, "is_first_recurring", False))
    await _deliver_vpn(message, payment, plan, payload, auto_renew=is_first_recurring)


async def _handle_stars_renewal(message: Message, bot: Bot, payment, plan: dict, plan_key: str):
    """Stars recurring renewal — продлевает existing sub user'а на duration_days.

    Каждое продление приходит с новым telegram_payment_charge_id, поэтому
    UNIQUE на payment_id не сработает (это хорошо — каждое списание = отдельная
    запись по дизайну). Но нам надо найти ПЕРВУЮ recurring-подписку юзера
    (auto_renew=True + payment_provider='stars') и extend её expires_at.
    """
    user_id = message.from_user.id
    payment_id = payment.telegram_payment_charge_id
    logger.info("Stars renewal: user=%d plan=%s charge_id=%s", user_id, plan_key, payment_id)

    # Дубль-guard: если этот renewal charge_id мы уже видели — игнорируем.
    # Audit 17.05 #1: проверяем `payments.tx_id` (recurring create's own row),
    # не `subscriptions.payment_id` (тот для первоначальной sub).
    from services.database import (
        is_payment_recorded, get_recurring_sub_for_renewal,
        extend_subscription_expires_at, record_payment,
    )
    if await is_payment_recorded(payment_id):
        logger.warning("Stars renewal: duplicate charge_id %s, ignored", payment_id)
        return

    sub = await get_recurring_sub_for_renewal(user_id, plan_key)
    if not sub:
        logger.warning(
            "Stars renewal: no parent sub for user=%d plan=%s (создаём как первый платёж)",
            user_id, plan_key,
        )
        # Fallback — обработаем как первый платёж, если по какой-то причине
        # is_first_recurring не пришёл (баг Telegram или потеря в логах).
        await _deliver_vpn(message, payment, plan, plan_key, auto_renew=True)
        return

    # record_payment FIRST — atomic UNIQUE на tx_id будет gate'ом перед
    # extend. Если retry прилетит после первой записи но до commit'а DB
    # extend'а — второй record_payment вернёт False, extend будет skipped.
    inserted = await record_payment(
        user_id=user_id, subscription_id=sub["id"],
        method="stars",
        stars=payment.total_amount,
        tx_id=payment_id,
    )
    if not inserted:
        logger.warning("Stars renewal: tx_id %s already recorded (race), ignored", payment_id)
        return

    # Extend expires_at от max(now, current) + plan duration. Если sub был в grace
    # или просрочен — extend от now (не теряем неоплаченное время).
    try:
        cur_expires = datetime.fromisoformat(sub.get("expires_at") or datetime.utcnow().isoformat())
    except Exception:
        cur_expires = datetime.utcnow()
    base = max(cur_expires, datetime.utcnow())
    new_expires = base + timedelta(days=plan["duration_days"])
    await extend_subscription_expires_at(sub["id"], new_expires.isoformat())

    try:
        await bot.send_message(
            user_id,
            f"🔁 <b>Подписка продлена автоматически</b>\n\n"
            f"VPN {plan['name']} активен до <b>{new_expires.strftime('%d.%m.%Y')}</b>.\n"
            f"Списано: <b>{payment.total_amount} ⭐</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Stars renewal notify failed user=%d: %s", user_id, e, exc_info=True)


async def _deliver_vpn(message: Message, payment, plan: dict, plan_key: str,
                       *, auto_renew: bool = False):
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

    # Renew-from-grace: shared helper.  Если grace найден и renew выполнен —
    # внутри отправит сообщение юзеру и запишет платёж; мы выходим.
    from services.grace import try_renew_from_grace
    bot_obj = message.bot
    if await try_renew_from_grace(
        bot_obj, user_id, plan_key, plan, payment_id,
        method="stars" if not payment_id.startswith("crypto_") else "crypto",
        stars=payment.total_amount,
    ):
        return

    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])

    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=payment_id,
        stars_paid=payment.total_amount,
        expires_at=expires_at,
        auto_renew=auto_renew,
        payment_provider="stars",
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
        # Закрываем И active И grace trial'ы — иначе grace-trial sub висит,
        # peers продолжают балансироваться в Happ subscription URL юзера
        # (audit 17.05 #C1).
        trials = await get_user_subscriptions_by_plan(
            user_id, "vpn_trial", status=("active", "grace"),
        )
        for trial_sub in trials:
            await _close_trial_on_paid_purchase(trial_sub["id"], user_id)
    except Exception as e:
        logger.warning("close-trial-on-paid failed user %d: %s (продолжаем)", user_id, e, exc_info=True)

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
                logger.warning("vpnctl WG peer error: %s", e, exc_info=True)

    vless_service = vless_service_for_plan(plan_key)
    # Multi-location subscription: каждый VLESS-слот = один UUID, реплицированный
    # на ВСЕ активные VLESS-сервера.  Юзер импортирует sub-URL в Happ/V2Box и
    # видит дропдаун со всеми локациями.  Слоты = независимые UUID (для семьи /
    # нескольких устройств без шеринга трафик-лимита).
    import uuid as _uuid
    from urllib.parse import quote as _q
    from services.database import get_all_active_servers
    vless_servers = await get_all_active_servers("vless")
    for i in range(vless_slots):
        slot_uuid = str(_uuid.uuid4())
        slot_created = False
        for server in vless_servers:
            try:
                # Label включает локацию: на каждом сервере уникален + читаемо в xray.log
                flag = (server.get("flag") or "").replace(" ", "")
                label = f"u{user_id}_v{i+1}_{flag or server['id']}"
                peer = await provision_peer(server, label, vless_service, peer_id=slot_uuid)
                # Friendly fragment в vless:// URL → Happ/V2Box покажет это
                # пользователю в дропдауне локаций. Берём из server.flag+city/name.
                loc = " ".join(filter(None, [
                    (server.get("flag") or "").strip(),
                    (server.get("city") or server.get("name") or "").strip(),
                ])).strip() or f"Server {server['id']}"
                cfg_data = peer.config or ""
                if cfg_data.startswith("vless://"):
                    base = cfg_data.split("#", 1)[0]
                    cfg_data = f"{base}#{_q(loc, safe='')}"
                # Per-location config row.  Все строки одного слота имеют одинаковый
                # vless_uuid + subscription_id, отличаются только server_id и config_data.
                config_id = await create_config_record(sub_id, user_id, protocol="vless",
                                                         server_id=server["id"])
                await save_peer_to_config(
                    config_id, server["id"], peer.id,
                    "", cfg_data, label,
                    vless_uuid=slot_uuid,
                )
                await update_server_peer_count(server["id"], +1)
                slot_created = True
            except VpnctlError as e:
                logger.warning("vpnctl VLess peer error server=%s slot=%d: %s",
                                server.get("id"), i + 1, e, exc_info=True)
        if slot_created:
            created_vless += 1

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
            logger.warning("vpnctl plain-WG peer error: %s", e, exc_info=True)

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

    # ─── Safety net: 0/N доставлено → catastrophic provision failure ────────
    # Юзер заплатил, но НИ ОДИН пир не создался (агент лежит / нет серверов).
    # Возвращаем Stars + помечаем подписку expired, чтобы юзер мог купить заново.
    # Если хоть один пир создался — оставляем как есть (юзер видит хотя бы один).
    if total > 0 and delivered == 0:
        logger.error(
            "VPN provision FAILED 0/%d for user=%d sub=%d charge=%s — refunding",
            total, user_id, sub_id, payment_id,
        )
        # Idempotency: проверяем не было ли уже refund'а для этого charge_id.
        # Без этого retry от Telegram может вызвать второй refund → 400 от API
        # + flood control + ложное "звёзды возвращены" юзеру.
        from services.database import (
            mark_subscription_expired, mark_subscription_refunded,
            is_payment_refunded, mark_payment_refunded,
        )
        refund_ok = False
        if await is_payment_refunded(payment_id):
            logger.warning("Stars refund: already refunded charge=%s, skipping", payment_id)
            refund_ok = True
        else:
            try:
                await message.bot.refund_star_payment(user_id, payment_id)
                await mark_payment_refunded(payment_id)
                refund_ok = True
            except Exception as e:
                logger.error("Refund failed user=%d charge=%s: %s — admin alert", user_id, payment_id, e, exc_info=True)
                # Алерт админу — refund провалился, юзер думает что вернули
                try:
                    from config import ADMIN_ID
                    if ADMIN_ID:
                        await message.bot.send_message(
                            ADMIN_ID,
                            f"🚨 <b>Stars refund FAILED</b>\n\n"
                            f"User: <code>{user_id}</code>\n"
                            f"Charge: <code>{payment_id}</code>\n"
                            f"Stars: {payment.total_amount}\n"
                            f"Error: <code>{e}</code>\n\n"
                            f"Нужно вернуть вручную через @BotSupport.",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass
        try:
            # Если refund прошёл — помечаем как refunded (для MRR/referral).
            # Если refund провалился — помечаем expired (нужен ручной refund админом).
            if refund_ok:
                await mark_subscription_refunded(sub_id)
            else:
                await mark_subscription_expired(sub_id)
        except Exception as e:
            logger.error("Mark sub failed sub=%d: %s", sub_id, e, exc_info=True)
        msg = (
            "❌ <b>Не удалось создать VPN-конфиги</b>\n\n"
            "Сервера временно недоступны. Звёзды возвращены — попробуй "
            "через несколько минут или напиши в поддержку."
            if refund_ok else
            "❌ <b>Не удалось создать VPN-конфиги</b>\n\n"
            "Сервера временно недоступны. Я уведомил поддержку — вернут "
            "звёзды в течение нескольких часов."
        )
        await message.answer(msg, parse_mode="HTML")
        return

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
            logger.warning("sub_token gen failed for user %d: %s", user_id, e, exc_info=True)

    sub_block = (
        f"\n\n🔗 <b>Subscription URL</b> (импортируй в Happ один раз — обновляется автоматом):\n"
        f"<code>{sub_url}</code>"
        if sub_url else ""
    )

    # Inline-кнопки после оплаты: «Мои конфиги» + «Инструкция».
    # Снижает friction первого подключения — новый юзер не ищет команды.
    _webapp_url = os.getenv("WEBAPP_URL", "")
    kb_rows = []
    if _webapp_url:
        kb_rows.append([
            InlineKeyboardButton(
                text="📁 Мои конфиги",
                web_app=WebAppInfo(url=f"{_webapp_url}/configs"),
            ),
            InlineKeyboardButton(
                text="📖 Инструкция",
                web_app=WebAppInfo(url=f"{_webapp_url}/instructions"),
            ),
        ])
    reply_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None

    await message.answer(
        f"✅ <b>VPN {plan['name']} оплачен!</b>\n\n"
        f"📅 Действует до: <b>{expiry_str}</b>\n"
        f"🔌 Слотов: <b>{slots_desc}</b>\n\n"
        f"{note}"
        f"{sub_block}",
        parse_mode="HTML",
        reply_markup=reply_kb,
    )

    # Реферальный бонус (Stars-flow). Общий helper используется и в Cryptobot/
    # Cryptomus/Lava webhook'ах — единый код для всех payment-методов.
    await maybe_award_referral_bonus(message.bot, user_id, sub_id)


# Per-user lock — защита от race между _close_trial_on_paid_purchase и
# scheduler'ом который параллельно может перевести триал в grace.
# Без лока: trial-active → юзер платит → запускается close → одновременно
# scheduler видит expires_at < now → запускает grace transition → пиры
# удаляются из vless-base но добавляются в vless-grace в гонке.
import asyncio as _asyncio
_trial_close_locks: dict[int, _asyncio.Lock] = {}
# Per-sub lock — защита от race upgrade-from-grace со scheduler'ом который
# параллельно может grace_until истечь и удалить пиры пока идёт upgrade.
_sub_lifecycle_locks: dict[int, _asyncio.Lock] = {}


def _trial_close_lock(user_id: int) -> _asyncio.Lock:
    if user_id not in _trial_close_locks:
        _trial_close_locks[user_id] = _asyncio.Lock()
    return _trial_close_locks[user_id]


def _sub_lifecycle_lock(sub_id: int) -> _asyncio.Lock:
    if sub_id not in _sub_lifecycle_locks:
        _sub_lifecycle_locks[sub_id] = _asyncio.Lock()
    return _sub_lifecycle_locks[sub_id]


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
        reset_config_slot, update_server_peer_count, get_active_subscription_by_id,
    )
    from services.vpnctl_client import client_for_server

    async with _trial_close_lock(user_id):
        # Re-check внутри лока: scheduler мог уже отметить sub expired
        sub_now = await get_active_subscription_by_id(trial_sub_id)
        if sub_now and sub_now.get("status") == "expired":
            logger.info("trial close skip: sub=%d уже expired", trial_sub_id)
            return

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
                        config_data = cfg.get("config_data") or ""
                        if peer_id:
                            if proto == "awg":
                                await client.remove_peer("awg", peer_id)
                            elif proto in ("vless", "vless-reality"):
                                # Определяем inbound по порту в config_data —
                                # если уже grace (:9453), удаляем из vless-grace.
                                inbound = "vless-grace" if ":9453" in config_data else "vless-base"
                                await client.remove_peer(inbound, peer_id)
                            await update_server_peer_count(server_id, -1)
                    except Exception as e:
                        logger.warning("trial close: revoke cfg #%d failed: %s", cfg["id"], e, exc_info=True)
            await reset_config_slot(cfg["id"])
        await mark_subscription_expired(trial_sub_id)
        logger.info("trial закрыт после платной покупки: sub=%d user=%d", trial_sub_id, user_id)


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
    try:
        sub_id      = int(sub_id_str)
        awg_delta   = int(awg_delta_str)
        vless_delta = int(vless_delta_str)
        wg_delta    = int(wg_delta_str)
    except ValueError:
        logger.error("upgrade payload parse failed: %r (payment=%s)",
                     payment.invoice_payload, payment.telegram_payment_charge_id, exc_info=True)
        await message.answer("⚠️ Ошибка payload апгрейда. Напиши в поддержку.")
        return
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

    was_grace = sub.get("status") == "grace"
    # Lock per sub_id — защита от race с scheduler'ом который может в это
    # же время grace-expire-нуть подписку и удалить пиры пока идёт unthrottle.
    async with _sub_lifecycle_lock(sub_id):
        await change_subscription_plan(sub_id, plan_key, user_id, awg_delta, vless_delta, wg_delta)

        # Если апгрейд из grace — снять throttle на агенте. Без этого юзер платит,
        # видит «Plan: Max» в UI, а реально пакет всё ещё 256 кбит/с (потому что
        # change_subscription_plan только меняет БД, не трогает агента).
        if was_grace:
            try:
                from services.database import (
                    get_configs_for_subscription, get_server_by_id, update_config_data,
                )
                from services.vpnctl_client import client_for_server
                configs = await get_configs_for_subscription(sub_id)
                for cfg in configs:
                    server_id = cfg.get("server_id")
                    if not server_id:
                        continue
                    server = await get_server_by_id(server_id)
                    if not server or not server.get("agent_url"):
                        continue
                    try:
                        client = client_for_server(server)
                        proto = cfg.get("protocol", "")
                        peer_id = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
                        assigned_ip = cfg.get("assigned_ip") or ""
                        if proto == "awg" and peer_id and assigned_ip:
                            # Снять tc-throttle на awg0 для этого пира
                            await client.unthrottle_peer("awg", peer_id, assigned_ip)
                        elif proto in ("vless", "vless-reality") and peer_id:
                            # Вернуть из vless-grace в нормальный inbound по тарифу
                            from services.plans import vless_service_for_plan
                            target_svc = vless_service_for_plan(plan_key)
                            new_peer = await client.add_peer(target_svc, f"u{user_id}_c{cfg['id']}", peer_id=peer_id)
                            await client.remove_peer("vless-grace", peer_id)
                            if new_peer.config:
                                await update_config_data(cfg["id"], new_peer.config)
                    except Exception as e:
                        logger.warning("upgrade-from-grace unthrottle cfg #%d: %s", cfg["id"], e, exc_info=True)
            except Exception as e:
                logger.error("upgrade-from-grace unthrottle outer: %s", e, exc_info=True)

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

async def _deliver_esim(message: Message, bot: Bot, payment):
    """Доставка eSIM после успешной оплаты Stars."""
    import asyncio
    parts = payment.invoice_payload.split(":", 2)
    if len(parts) != 3:
        await message.answer("⚠️ Ошибка payload. Напиши в поддержку.")
        return

    _, pkg_code, price_str = parts
    wholesale_price = int(price_str)
    user_id   = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    pkg = await esim_api.find_package(pkg_code)
    if not pkg:
        logger.error("eSIM unknown package: %s", pkg_code)
        await _esim_refund_and_notify(bot, user_id, charge_id, 0)
        return

    order_id = await create_order(
        user_id=user_id,
        product_type="esim",
        plan=pkg_code,
        stars_paid=payment.total_amount,
    )
    await complete_order(order_id, payment_id=charge_id)

    # tx_id detrministic by order_id — без uuid suffix'а. esimaccess использует
    # transactionId для idempotency: если timeout → retry → тот же tx_id →
    # esimaccess вернёт тот же orderNo вместо создания второго заказа.
    tx_id = f"tg_{user_id}_{order_id}"
    profile_id = await create_esim_profile(
        user_id=user_id, order_id=order_id, tx_id=tx_id,
        package_code=pkg_code, package_name=pkg["name"],
        location_code=pkg["location"], wholesale_price=wholesale_price,
    )

    await message.answer(
        "🛠️ <b>Заказываем eSIM...</b>\n"
        "Обычно занимает 10–30 сек, QR придёт сюда автоматически.",
        parse_mode="HTML",
    )

    try:
        result = await esim_api.place_order(pkg_code, wholesale_price, tx_id)
    except Exception as e:
        logger.error("eSIM place_order failed: %s", e, exc_info=True)
        await mark_esim_failed(profile_id)
        await _esim_refund_and_notify(bot, user_id, charge_id, order_id)
        return

    if not result.get("success"):
        logger.error("eSIM API error: %s", result)
        await mark_esim_failed(profile_id)
        await _esim_refund_and_notify(bot, user_id, charge_id, order_id)
        return

    order_no = (result.get("obj") or {}).get("orderNo", "")
    if not order_no:
        logger.error("eSIM no orderNo in response: %s", result)
        await mark_esim_failed(profile_id)
        await _esim_refund_and_notify(bot, user_id, charge_id, order_id)
        return

    await set_esim_order_no(profile_id, order_no)
    logger.info("eSIM order placed: profile=%d order=%s pkg=%s", profile_id, order_no, pkg_code)

    # Фоновый poll-fallback на случай если webhook не настроен/упал.
    asyncio.create_task(
        _finalize_esim_via_polling(profile_id, order_no, bot, user_id)
    )


async def _finalize_esim_via_polling(profile_id: int, order_no: str, bot: Bot, user_id: int):
    """Опрашивает /esim/query до 60 сек и отдаёт пользователю.
    Если webhook опередил — fulfill_esim_profile вернёт False, тихо выйдем."""
    esim_data = await esim_api.poll_order_until_ready(order_no, max_wait_sec=60)
    if not esim_data:
        try:
            await bot.send_message(
                user_id,
                "⏳ <b>Заказ оформляется</b> — иногда SM-DP+ занимает пару минут. "
                "QR придёт сюда автоматически как только будет готов.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return
    fulfilled = await fulfill_esim_profile(profile_id, esim_data)
    if not fulfilled:
        return  # webhook опередил
    await deliver_esim_to_user(bot, profile_id)


async def deliver_esim_to_user(bot: Bot, profile_id: int):
    """Отправляет готовый eSIM в чат пользователю.
    Идемпотентно: вызывается и из polling-fallback, и из webhook handler."""
    profile = await get_esim_profile(profile_id)
    if not profile or profile["status"] != "ready":
        return

    user_id   = profile["user_id"]
    ac        = profile["ac"]
    qr_url    = profile["qr_url"]
    short_url = profile["short_url"]
    smdp      = profile["smdp_address"] or ""
    matching  = profile["matching_id"] or ""
    pkg_name  = profile["package_name"] or "eSIM"

    caption_lines = [
        "✅ <b>eSIM готова!</b>",
        f"📦 <b>{pkg_name}</b>",
        "",
        "📲 <b>iPhone — самый простой путь:</b>",
    ]
    if short_url:
        caption_lines.append(
            f"1. <a href=\"{short_url}\">Открой эту ссылку с iPhone 17.4+</a> — "
            "появится нативный диалог установки"
        )
        caption_lines.append("2. Или: Настройки → Сотовая связь → Добавить eSIM → Сканируй QR ниже")
    else:
        caption_lines.append("Настройки → Сотовая связь → Добавить eSIM → Сканируй QR ниже")

    caption_lines += [
        "",
        "🤖 <b>Android:</b> Настройки → SIM-карты → Добавить eSIM → Сканируй QR",
        "",
        "📝 <b>Вручную</b> (если QR/ссылка не работают):",
        f"   SM-DP+ адрес: <code>{smdp}</code>",
        f"   Код активации: <code>{matching}</code>",
        "",
        "⚠️ <b>Установить можно только 1 раз</b> — сохрани QR до активации.",
        "⚡ eSIM активируется при первом подключении к сети.",
    ]
    caption = "\n".join(caption_lines)

    try:
        if qr_url:
            await bot.send_photo(user_id, qr_url, caption=caption, parse_mode="HTML")
        elif ac:
            import qrcode as qr_lib
            qr = qr_lib.QRCode(box_size=10, border=4)
            qr.add_data(ac)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            await bot.send_photo(
                user_id,
                BufferedInputFile(buf.read(), "esim_qr.png"),
                caption=caption, parse_mode="HTML",
            )
        else:
            await bot.send_message(user_id, caption, parse_mode="HTML")
    except Exception as e:
        logger.error("eSIM delivery failed for user=%d profile=%d: %s", user_id, profile_id, e, exc_info=True)
        try:
            text = caption + (f"\n\nQR: {qr_url}" if qr_url else "")
            await bot.send_message(user_id, text, parse_mode="HTML")
        except Exception:
            pass


async def _esim_refund_and_notify(bot: Bot, user_id: int, charge_id: str, order_id: int):
    """Возврат Stars и уведомление при ошибке eSIM."""
    try:
        await bot.refund_star_payment(user_id, charge_id)
        # Помечаем eSIM-профиль как refunded — для аналитики и чтобы
        # /api/esim/my не показывал юзеру отменённый профиль как pending.
        if order_id:
            try:
                from services.database import mark_esim_refunded_by_order, mark_order_expired
                await mark_esim_refunded_by_order(order_id)
                await mark_order_expired(order_id)
            except Exception as e:
                logger.warning("eSIM refund mark failed order=%s: %s", order_id, e, exc_info=True)
        await bot.send_message(
            user_id,
            f"❌ <b>Не удалось оформить eSIM</b>"
            + (f" (заказ #{order_id})" if order_id else "")
            + ".\n\nЗвёзды возвращены. Попробуй ещё раз или напиши в поддержку.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Refund failed: %s", e, exc_info=True)


async def maybe_award_referral_bonus(bot: Bot, user_id: int, sub_id: int) -> None:
    """Шлёт реферрер-бонус в банк если у юзера есть referred_by + это первая платная.

    Идемпотентно — атомарный CLAIM через WHERE ref_bonus_awarded_to IS NULL
    в try_award_referral_bonus защищает от double-award race (если webhook
    прилетит дважды от одной покупки).

    Вызывается из всех 4 платёжных flows:
    - Stars (_deliver_vpn в этом же файле)
    - CryptoBot webhook (handle_cryptobot_webhook)
    - Cryptomus webhook (handle_cryptomus_webhook)
    - Lava webhook (handle_lavatop_webhook + recurring)

    Реферрер получает TG-сообщение «+N в банк, активируй в Mini App».
    Текст явно говорит «в банк» — не «к подписке», чтобы юзер не ожидал
    auto-extend (он удалён в пользу manual redeem).
    """
    try:
        from services.database import try_award_referral_bonus
        from handlers.start import REFERRAL_BONUS_DAYS
        referrer_id = await try_award_referral_bonus(
            user_id, REFERRAL_BONUS_DAYS, paid_sub_id=sub_id,
        )
        if referrer_id:
            try:
                await bot.send_message(
                    referrer_id,
                    f"🎁 <b>+{REFERRAL_BONUS_DAYS} дней в твой бонус-банк!</b>\n\n"
                    f"Твой друг купил VPN по реферальной ссылке. "
                    f"Активируй бонус в Mini App: <b>Друзья → 🎁 Мои бонусы</b>.\n\n"
                    f"Бонус применится к твоей активной подписке (продлит срок).",
                    parse_mode="HTML",
                )
            except Exception:
                pass  # юзер заблокировал бота — не страшно
    except Exception as e:
        logger.warning("maybe_award_referral_bonus failed user=%d sub=%d: %s",
                       user_id, sub_id, e, exc_info=True)


async def provision_vpn_slots_async(
    bot: Bot,
    user_id: int,
    sub_id: int,
    plan: dict,
    plan_key: str,
) -> tuple[int, int]:
    """Создаёт реальные пиры VPN на агенте для уже-созданной подписки.

    Используется CryptoBot / Cryptomus / Lava webhook'ами (там нет
    message-объекта). Не шлёт .conf-файлы документами — юзер увидит
    конфиги в Mini App «Мои конфиги».

    Возвращает (delivered, total). Если delivered == 0 — caller должен
    помечать sub expired и слать notification юзеру.
    """
    # Закрываем активный триал если есть. Без этого юзер с триалом+платным
    # получает две active sub'ы (Happ балансирует между пирами grace/normal).
    # _deliver_vpn делает то же для Stars-flow.
    try:
        from services.database import get_user_subscriptions_by_plan
        # И active И grace — см. audit #C1.
        trials = await get_user_subscriptions_by_plan(
            user_id, "vpn_trial", status=("active", "grace"),
        )
        for trial_sub in trials:
            if trial_sub["id"] != sub_id:  # не закрываем сами себя на всякий случай
                await _close_trial_on_paid_purchase(trial_sub["id"], user_id)
    except Exception as e:
        logger.warning("provision: close-trial failed user %d: %s (продолжаем)",
                       user_id, e, exc_info=True)

    awg_slots   = plan.get("awg_slots", 0)
    vless_slots = plan.get("vless_slots", 0)
    wg_slots    = plan.get("wg_slots", 0)
    total       = awg_slots + vless_slots + wg_slots
    delivered   = 0

    for i in range(awg_slots):
        config_id = await create_config_record(sub_id, user_id, protocol="awg")
        server = await get_best_server("awg")
        if not server:
            continue
        try:
            label = f"user_{user_id}_wg_{i+1}"
            peer = await provision_peer(server, label, "awg")
            peer_ip = (peer.extra or {}).get("assigned_ip", "")
            await save_peer_to_config(
                config_id, server["id"], peer.id, peer_ip, peer.config, label,
            )
            await update_server_peer_count(server["id"], +1)
            delivered += 1
        except VpnctlError as e:
            logger.warning("crypto-flow: WG peer error: %s", e, exc_info=True)

    vless_service = vless_service_for_plan(plan_key)
    for i in range(vless_slots):
        config_id = await create_config_record(sub_id, user_id, protocol="vless")
        server = await get_best_server("vless")
        if not server:
            continue
        try:
            label = f"user_{user_id}_vless_{i+1}"
            peer = await provision_peer(server, label, vless_service)
            await save_peer_to_config(
                config_id, server["id"], peer.id, "", peer.config, label,
            )
            await update_server_peer_count(server["id"], +1)
            delivered += 1
        except VpnctlError as e:
            logger.warning("crypto-flow: VLess peer error: %s", e, exc_info=True)

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
                config_id, server["id"], peer.id, peer_ip, peer.config, label,
            )
            await update_server_peer_count(server["id"], +1)
            delivered += 1
        except VpnctlError as e:
            logger.warning("crypto-flow: plain-WG peer error: %s", e, exc_info=True)

    return delivered, total
