import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import ADMIN_ID, ADMIN_IDS, BOT_TOKEN, WEBAPP_URL
from services.database import (
    get_stats,
    create_order,
    complete_order,
    create_subscription,
    create_config_record,
    get_ticket_by_admin_msg,
    get_referral_stats,
    get_servers_by_protocol,
    get_active_vless_configs_with_plan,
)
from services.vpnctl_client import client_for_server, VpnctlError
from handlers.vpn import VPN_PLANS, vless_service_for_plan, vless_slow_service_for_plan

router = Router()

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _make_admin_token(user_id: int, username: str) -> str:
    """Генерирует одноразовый токен для входа в админку (живёт 5 минут)."""
    payload = base64.b64encode(
        json.dumps({"userId": user_id, "username": username, "exp": int(time.time()) + 300}).encode()
    ).decode()
    sig = hmac.new(BOT_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}.{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@router.message(F.reply_to_message)
async def relay_support_reply(message: Message):
    """Пересылает ответ админа на тикет обратно пользователю.

    Раньше фильтр был `F.from_user.id == ADMIN_ID` (только основной).
    Теперь любой из ADMIN_IDS может отвечать — проверка внутри.
    """
    if not _is_admin(message.from_user.id):
        return  # не админ — даже не reply на тикет
    replied_msg_id = message.reply_to_message.message_id
    ticket = await get_ticket_by_admin_msg(replied_msg_id)
    if not ticket:
        return  # не тикет — игнорируем

    user_id = ticket["user_id"]
    ticket_id = ticket["id"]
    try:
        await message.bot.send_message(
            user_id,
            f"💬 <b>Ответ от поддержки (тикет #{ticket_id}):</b>\n\n{message.text or message.caption or ''}",
            parse_mode="HTML",
        )
        await message.reply("✅ Ответ отправлен пользователю")
    except Exception as e:
        await message.reply(f"❌ Не удалось отправить: {e}")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not _is_admin(message.from_user.id):
        return
    import aiosqlite
    from services.database import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Подписки по плану
        rows_plan = await (await db.execute(
            "SELECT plan, COUNT(*) c FROM subscriptions WHERE status='active' GROUP BY plan"
        )).fetchall()
        # Активные конфиги по протоколу
        rows_proto = await (await db.execute(
            "SELECT protocol, COUNT(*) c FROM configs WHERE status='active' GROUP BY protocol"
        )).fetchall()
        # Серверы
        rows_srv = await (await db.execute(
            "SELECT name, flag, protocol, active_peers, capacity, is_active FROM servers ORDER BY id"
        )).fetchall()
        # Throttled (config_data на slow-port)
        thr_row = await (await db.execute(
            """SELECT COUNT(*) c FROM configs
               WHERE status='active' AND protocol='vless'
                 AND (config_data LIKE '%:9443%' OR config_data LIKE '%:9448%'
                      OR config_data LIKE '%:43200%' OR config_data LIKE '%:43300%')"""
        )).fetchone()
        # Сегодняшний доход (Stars + RUB)
        rev_stars_row = await (await db.execute(
            "SELECT COALESCE(SUM(stars),0) FROM payments WHERE date(created_at)=date('now')"
        )).fetchone()
        # Всего юзеров
        users_row = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        # Истекают за 3 дня
        exp_row = await (await db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE status='active' "
            "AND expires_at <= datetime('now','+3 days')"
        )).fetchone()

    plans = "\n".join(f"  • {r['plan']}: <b>{r['c']}</b>" for r in rows_plan) or "  (нет)"
    protos = "\n".join(f"  • {r['protocol']}: <b>{r['c']}</b>" for r in rows_proto) or "  (нет)"
    srvs = "\n".join(
        f"  {'🟢' if r['is_active'] else '⚫'} {r['flag'] or '🌍'} {r['name']:20s} "
        f"{r['protocol']:7s} {r['active_peers']:>3}/{r['capacity']}"
        for r in rows_srv
    )
    throttled = thr_row[0] if thr_row else 0
    today_stars = rev_stars_row[0] if rev_stars_row else 0
    total_users = users_row[0] if users_row else 0
    expiring = exp_row[0] if exp_row else 0

    text = (
        "📊 <b>Stats</b>\n\n"
        f"<b>Подписки активные</b>:\n{plans}\n\n"
        f"<b>Конфиги активные</b>:\n{protos}\n\n"
        f"<b>Серверы</b>:\n<code>{srvs}</code>\n\n"
        f"🐢 Throttled: <b>{throttled}</b>\n"
        f"⏳ Истекают за 3 дня: <b>{expiring}</b>\n"
        f"⭐ Доход сегодня: <b>{today_stars}</b>\n"
        f"👤 Всего юзеров: <b>{total_users}</b>"
    )
    await message.answer(text, parse_mode="HTML")


async def _trial_response(user_id: int) -> tuple[str, dict]:
    """Запускает provision_trial и возвращает (text, send_kwargs) для ответа юзеру.
    Единый код для /trial команды и для callback "trial:claim" из /start меню.
    """
    from services.trial import (
        provision_trial,
        TrialAlreadyClaimed,
        TrialBlockedByActiveSub,
        TrialNoServer,
    )
    from services.vpnctl_client import VpnctlError

    try:
        result = await provision_trial(user_id)
    except TrialBlockedByActiveSub:
        return ("У тебя уже активная подписка. Trial доступен только новым пользователям.", {})
    except TrialAlreadyClaimed:
        return ("🎁 Trial уже использован.\n\nДля продолжения — выбери тариф в /start", {})
    except TrialNoServer:
        return ("⚠️ Серверы пока недоступны, попробуй позже", {})
    except VpnctlError as e:
        return (f"⚠️ Ошибка провижининга: {e}", {})

    expires_str = result["expires_at"].strftime("%d.%m.%Y %H:%M")
    has_awg = bool(result.get("awg_config"))

    if has_awg:
        text = (
            f"🎁 <b>Trial на {result['duration_days']} дня активирован</b>\n\n"
            f"📅 До: <b>{expires_str}</b>\n"
            f"🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
            f"<b>1) AmneziaWG</b> — главный обфускатор, работает на МТС\n"
            f"   Открой Configs (/start → 📁 Конфиги) → скачай AWG-конфиг\n\n"
            f"<b>2) VLESS Subscription URL</b> (для Happ / V2Box):\n"
            f"<code>{result['sub_url']}</code>\n\n"
            f"📖 Инструкция: /howto\n"
            f"💎 После trial — выбери постоянный тариф в /start"
        )
    else:
        # AWG-сервер недоступен — fallback на VLESS-only (старое поведение)
        text = (
            f"🎁 <b>Trial на {result['duration_days']} дня активирован</b>\n\n"
            f"📅 До: <b>{expires_str}</b>\n"
            f"🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
            f"<b>Subscription URL</b> (импортируй в Happ один раз):\n"
            f"<code>{result['sub_url']}</code>\n\n"
            f"📖 Инструкция: /howto\n"
            f"💎 После trial — выбери постоянный тариф в /start"
        )
    return (text, {"parse_mode": "HTML"})


@router.message(Command("trial"))
async def cmd_trial(message: Message):
    """Бесплатный пробный период — 3 дня VLESS-base."""
    text, kwargs = await _trial_response(message.from_user.id)
    await message.answer(text, **kwargs)


@router.callback_query(F.data == "trial:claim")
async def cb_trial_claim(callback: CallbackQuery):
    """Callback с кнопки «🎁 Попробуй бесплатно» из /start меню."""
    await callback.answer()  # снять "thinking" индикатор
    text, kwargs = await _trial_response(callback.from_user.id)
    if callback.message:
        await callback.message.answer(text, **kwargs)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not _is_admin(message.from_user.id):
        return

    user = message.from_user
    token = _make_admin_token(user.id, user.username or user.first_name)
    admin_url = f"https://maxvpnesim.com/admin/api/auth/token?t={token}"

    await message.answer(
        "🔐 <b>Вход в админ-панель</b>\n\n"
        "Ссылка действует <b>5 минут</b>. Не передавай её никому.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть панель →", url=admin_url)
        ]])
    )


@router.message(Command("refund_ref"))
async def cmd_refund_referral(message: Message):
    """Откатывает реферальный бонус для подписки которая была возвращена.

    Используется когда поддержка делает manual refund (например через
    CryptoBot dashboard) — рефер получил +7 дней за пустой платёж,
    которые надо вычесть обратно.

    Usage: /refund_ref <subscription_id>
    """
    if not _is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(
            "Usage: <code>/refund_ref &lt;subscription_id&gt;</code>\n\n"
            "Откатит +N дней рефералу для подписки которая была возвращена.",
            parse_mode="HTML",
        )
        return
    sub_id = int(parts[1].strip())
    from services.database import (
        rollback_referral_bonus, mark_subscription_refunded, audit_log_record,
    )
    result = await rollback_referral_bonus(sub_id)
    # Помечаем подписку как refunded (если ещё не помечена) — для MRR /
    # paid_count логики. mark_subscription_refunded идемпотентно.
    try:
        await mark_subscription_refunded(sub_id)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(
            "/refund_ref: mark_refunded failed sub=%d: %s", sub_id, e, exc_info=True,
        )
    if result is None:
        await audit_log_record(
            message.from_user.id, "refund_ref_no_bonus",
            target=f"sub_id={sub_id}",
            details="Бонус не был начислен или уже откатан",
        )
        await message.answer(
            f"❌ Бонус не был начислен для sub #{sub_id} (или уже откатан).\n"
            f"Подписка помечена как refunded.",
        )
        return
    referrer_id, days = result
    await audit_log_record(
        message.from_user.id, "refund_ref",
        target=f"sub_id={sub_id} referrer_id={referrer_id}",
        details=f"days={days}",
    )
    await message.answer(
        f"✅ Откачено: <b>{days} дней</b> у юзера <code>{referrer_id}</code>.\n"
        f"ref_bonus_days уменьшен (clamp на 0), expires_at активной подписки сдвинут назад.\n"
        f"Подписка помечена refunded.",
        parse_mode="HTML",
    )


@router.message(Command("esim_test"))
async def cmd_esim_test(message: Message):
    """Админская команда: тестовый заказ eSIM напрямую через esimaccess API.
    Списывается с merchant-баланса (не Stars). Только для админов.

    Usage:
      /esim_test            — показать список slug + цены + текущий баланс
      /esim_test <slug>     — заказать eSIM (например /esim_test RU_0.1_7)
    """
    import logging as _log
    _log.getLogger(__name__).info(
        "/esim_test called by user_id=%s username=%s args=%r",
        message.from_user.id, message.from_user.username, message.text,
    )
    if not _is_admin(message.from_user.id):
        _log.getLogger(__name__).warning(
            "/esim_test rejected: user_id=%s NOT in ADMIN_IDS=%s",
            message.from_user.id, ADMIN_IDS,
        )
        return

    import uuid
    import services.esim_api as esim_api
    from services.database import (
        create_order, complete_order, create_esim_profile, set_esim_order_no,
        fulfill_esim_profile, mark_esim_failed,
    )
    from handlers.vpn import deliver_esim_to_user

    parts = message.text.split(maxsplit=1)

    # ── без аргумента: показать список ────────────────────────────────────────
    if len(parts) < 2:
        bal = await esim_api.get_balance()
        balance_usd = (bal.get('obj', {}) or {}).get('balance', 0) / 10000
        lines = [
            f"💳 <b>esimaccess balance:</b> ${balance_usd:.2f}",
            "",
            "📦 <b>Самые дешёвые тарифы по странам:</b>",
            "",
        ]
        for code in ['RU', 'TR', 'GE', 'AE', 'TH', 'VN', 'EU-42']:
            try:
                pkgs = await esim_api.get_packages_for(code)
            except Exception:
                continue
            if not pkgs:
                continue
            cheapest = min(pkgs, key=lambda p: p['price'])
            lines.append(
                f"<code>/esim_test {cheapest['slug']}</code>\n"
                f"   {cheapest['name']} · ${cheapest['priceUsd']:.2f} · "
                f"{cheapest['ipExport'] or '?'}-IP"
            )
        lines += [
            "",
            "⚠ Списание с merchant-баланса esimaccess (не Stars).",
            "QR придёт сюда через 10–60 сек.",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML",
                              disable_web_page_preview=True)
        return

    # ── со slug: заказать ─────────────────────────────────────────────────────
    slug = parts[1].strip()
    pkg = await esim_api.find_package(slug)
    if not pkg:
        await message.answer(
            f"❌ Пакет <code>{slug}</code> не найден.\n"
            "Без аргумента <code>/esim_test</code> покажет доступные.",
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id

    await message.answer(
        f"🛠️ <b>Заказываю тестовую eSIM...</b>\n"
        f"📦 {pkg['name']}\n"
        f"💸 wholesale ${pkg['priceUsd']:.2f}\n"
        f"🌐 IP-эксит: {pkg['ipExport'] or '?'}\n"
        f"⏳ 10–60 сек, QR придёт сюда автоматом",
        parse_mode="HTML",
    )

    # Order/profile записи для трассировки (но stars_paid=0)
    order_id = await create_order(
        user_id=user_id, product_type="esim",
        plan=pkg['packageCode'], stars_paid=0,
    )
    await complete_order(order_id, payment_id=f"admin_test_{int(time.time())}")

    tx_id = f"admin_{user_id}_{order_id}_{uuid.uuid4().hex[:8]}"
    profile_id = await create_esim_profile(
        user_id=user_id, order_id=order_id, tx_id=tx_id,
        package_code=pkg['packageCode'], package_name=pkg['name'],
        location_code=pkg['location'], wholesale_price=pkg['price'],
    )

    try:
        result = await esim_api.place_order(pkg['packageCode'], pkg['price'], tx_id)
    except Exception as e:
        await mark_esim_failed(profile_id)
        await message.answer(f"❌ <b>place_order error:</b>\n<code>{e}</code>",
                              parse_mode="HTML")
        return

    if not result.get('success'):
        await mark_esim_failed(profile_id)
        err = result.get('errorCode') or '?'
        msg = result.get('errorMsg') or '?'
        await message.answer(
            f"❌ <b>esimaccess error {err}:</b> {msg}\n"
            f"<code>{str(result)[:300]}</code>",
            parse_mode="HTML",
        )
        return

    order_no = (result.get('obj') or {}).get('orderNo', '')
    if not order_no:
        await mark_esim_failed(profile_id)
        await message.answer(
            f"❌ Нет orderNo в ответе:\n<code>{str(result)[:300]}</code>",
            parse_mode="HTML",
        )
        return

    await set_esim_order_no(profile_id, order_no)
    await message.answer(f"✅ Order <code>#{order_no}</code> создан, жду профиль...",
                          parse_mode="HTML")

    bot = message.bot
    esim_data = await esim_api.poll_order_until_ready(order_no, max_wait_sec=60)
    if not esim_data:
        await message.answer(
            "⏳ Профиль не готов через 60s. Жду webhook ORDER_STATUS — QR придёт автоматом.",
        )
        return

    fulfilled = await fulfill_esim_profile(profile_id, esim_data)
    if not fulfilled:
        await message.answer("ℹ️ Webhook опередил polling — eSIM уже отправлен.")
        return

    await deliver_esim_to_user(bot, profile_id)


@router.message(Command("gift"))
async def cmd_gift(message: Message):
    """Выдать себе бесплатный VPN: /gift vpn_pro"""
    if not _is_admin(message.from_user.id):
        return

    args = message.text.split()
    plan_key = args[1] if len(args) > 1 else "vpn_start"
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        await message.answer(f"Неизвестный план. Доступны: {', '.join(VPN_PLANS)}")
        return

    from services.database import audit_log_record
    await audit_log_record(
        message.from_user.id, "gift_self",
        target=f"user_id={message.from_user.id}",
        details=f"plan={plan_key}",
    )
    await _deliver_free_vpn(message, message.from_user.id, plan_key, plan)


@router.message(Command("send"))
async def cmd_send(message: Message):
    """Подарить VPN юзеру: /send 123456789 vpn_start"""
    if not _is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /send &lt;user_id&gt; &lt;план&gt;", parse_mode="HTML")
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("user_id должен быть числом")
        return

    plan_key = args[2]
    plan = VPN_PLANS.get(plan_key)
    if not plan:
        await message.answer(f"Неизвестный план. Доступны: {', '.join(VPN_PLANS)}")
        return

    from services.database import audit_log_record
    await audit_log_record(
        message.from_user.id, "gift_to_user",
        target=f"user_id={target_id}",
        details=f"plan={plan_key}",
    )
    await message.answer(f"⏳ Создаю слоты для {target_id}...")
    await _deliver_free_vpn(message, target_id, plan_key, plan, notify_admin=True)


async def _deliver_free_vpn(
    message: Message,
    user_id: int,
    plan_key: str,
    plan: dict,
    notify_admin: bool = False,
):
    """
    Создаёт бесплатную подписку с пустыми слотами.
    Пользователь активирует конфиги сам в мини-апп → Мои конфиги.
    """
    expires_at = datetime.utcnow() + timedelta(days=plan["duration_days"])

    # Уникальный payment_id для бесплатных выдач
    free_payment_id = f"free_{user_id}_{int(datetime.utcnow().timestamp())}"

    sub_id = await create_subscription(
        user_id=user_id,
        plan=plan_key,
        payment_id=free_payment_id,
        stars_paid=0,
        expires_at=expires_at,
    )

    # Backward compat — orders
    order_id = await create_order(
        user_id=user_id,
        product_type="vpn",
        plan=plan_key,
        stars_paid=0,
        expires_at=expires_at,
    )
    await complete_order(order_id, payment_id=free_payment_id)

    awg_slots   = plan.get("awg_slots", 1)
    vless_slots = plan.get("vless_slots", 0)

    # Создаём пустые слоты
    for _ in range(awg_slots):
        await create_config_record(subscription_id=sub_id, user_id=user_id, protocol="awg")
    for _ in range(vless_slots):
        await create_config_record(subscription_id=sub_id, user_id=user_id, protocol="vless")

    slots_desc = f"{awg_slots} AWG"
    if vless_slots:
        slots_desc += f" + {vless_slots} VLESS"

    expiry_str = expires_at.strftime("%d.%m.%Y")
    bot = message.bot

    await bot.send_message(
        user_id,
        f"🎁 <b>Бесплатный VPN · {plan['name']}</b>\n\n"
        f"📅 Действует до: <b>{expiry_str}</b>\n"
        f"🔌 Слотов: <b>{slots_desc}</b>\n\n"
        "Открой мини-апп → <b>Мои конфиги</b> и активируй нужные слоты.",
        parse_mode="HTML",
    )

    if notify_admin:
        await message.answer(
            f"✅ Подписка #{sub_id} создана → user {user_id}\n"
            f"Тариф: {plan['name']} · {slots_desc} · до {expiry_str}"
        )
