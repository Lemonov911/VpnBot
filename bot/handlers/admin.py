import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID, BOT_TOKEN, WEBAPP_URL
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

ADMIN_IDS = {ADMIN_ID, 594024866}

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


@router.message(F.reply_to_message, F.from_user.id == ADMIN_ID)
async def relay_support_reply(message: Message):
    """Пересылает ответ админа на тикет обратно пользователю."""
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


@router.message(Command("trial"))
async def cmd_trial(message: Message):
    """Бесплатный пробный период — 1 GB / 24 часа."""
    from datetime import datetime, timedelta
    from services.database import (
        DB_PATH, get_best_server, save_peer_to_config, create_subscription,
        create_config_record, update_server_peer_count, get_or_create_sub_token,
    )
    from services.vpnctl_client import provision_peer, VpnctlError
    import aiosqlite

    user_id = message.from_user.id

    # Проверка, что пользователь не использовал trial раньше
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (await db.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND plan='vpn_trial' LIMIT 1",
            (user_id,)
        )).fetchone()
    if existing:
        await message.answer(
            "🎁 Trial уже использован.\n\nДля продолжения — выбери тариф в /start"
        )
        return

    # Создаём пробную подписку на 24 ч
    expires = datetime.now() + timedelta(days=1)
    sub_id = await create_subscription(user_id, "vpn_trial", "trial", 0, expires)
    config_id = await create_config_record(sub_id, user_id, protocol="vless")

    server = await get_best_server("vless")
    if not server:
        await message.answer("⚠️ Серверы пока недоступны, попробуй позже")
        return

    try:
        peer = await provision_peer(server, f"trial_{user_id}_{config_id}", "vless-base")
    except VpnctlError as e:
        await message.answer(f"⚠️ Ошибка провижининга: {e}")
        return

    await save_peer_to_config(
        config_id, server["id"], peer.id, "", peer.config, f"trial_{user_id}"
    )
    await update_server_peer_count(server["id"], +1)
    sub_token = await get_or_create_sub_token(user_id)

    await message.answer(
        "🎁 <b>Trial 1 GB / 24 часа активирован</b>\n\n"
        f"📅 До: <b>{expires.strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
        f"<b>Subscription URL</b> (импортируй в Happ):\n"
        f"<code>https://maxvpnesim.com/sub/{sub_token}</code>\n\n"
        f"📖 Инструкция: /howto\n"
        f"💎 После trial — выбери постоянный тариф в /start",
        parse_mode="HTML",
    )


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
