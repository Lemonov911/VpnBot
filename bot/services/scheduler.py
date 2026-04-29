"""
Фоновая задача: проверяет истёкшие подписки каждый час.

Для каждой истёкшей подписки:
  1. Получаем все активные конфиги подписки
  2. Удаляем AWG-пир с сервера (best-effort)
  3. Помечаем конфиги как revoked
  4. Помечаем подписку как expired
  5. Отправляем уведомление пользователю

Также обрабатывает старые заказы из таблицы orders (backward compat).
"""

import asyncio
import logging

from aiogram import Bot

from services.database import (
    get_expired_subscriptions,
    get_configs_for_subscription,
    mark_subscription_expired,
    revoke_config,
    reset_config_slot,
    get_subscriptions_expiring_soon,
    mark_reminded,
    get_expired_orders,
    mark_order_expired,
    get_server_by_id,
    update_server_peer_count,
)
from services.vpnctl_client import suspend_peer

logger = logging.getLogger(__name__)

EXPIRY_NOTICE = (
    "⚠️ <b>Подписка на VPN истекла</b>\n\n"
    "Твой доступ к VPN был отключён. Чтобы продолжить — "
    "оформи новую подписку в боте.\n\n"
    "/start — открыть меню"
)

CHECK_INTERVAL = 3600  # секунд (1 час)


async def _process_expired_subscriptions(bot: Bot):
    """Обрабатывает истёкшие подписки из таблицы subscriptions."""
    expired_subs = await get_expired_subscriptions()
    if not expired_subs:
        return

    logger.info("Найдено истёкших подписок: %d", len(expired_subs))

    for sub in expired_subs:
        sub_id  = sub["id"]
        user_id = sub["user_id"]

        # Получаем все активные конфиги подписки
        configs = await get_configs_for_subscription(sub_id)
        logger.info("Подписка #%d: отзываем %d конфиг(ов)", sub_id, len(configs))

        for cfg in configs:
            # Приостанавливаем пир через vpnctl (suspend, не удаляем — можно возобновить)
            if cfg.get("server_id"):
                server = await get_server_by_id(cfg["server_id"])
                if server:
                    peer_id = cfg.get("vless_uuid") or cfg.get("wg_pubkey")
                    protocol = cfg["protocol"]
                    await suspend_peer(server, peer_id, protocol)
                    await update_server_peer_count(cfg["server_id"], -1)

            await reset_config_slot(cfg["id"])
            logger.info("Конфиг #%d suspended (sub=%d)", cfg["id"], sub_id)

        await mark_subscription_expired(sub_id)
        logger.info("Подписка #%d помечена как expired", sub_id)

        # Уведомляем пользователя
        try:
            await bot.send_message(user_id, EXPIRY_NOTICE, parse_mode="HTML")
        except Exception as e:
            logger.warning("Не удалось уведомить user %d: %s", user_id, e)


async def _process_expired_orders(bot: Bot):
    """
    Обрабатывает истёкшие заказы из старой таблицы orders.
    Оставлено для backward compatibility с заказами до рефакторинга.
    """
    expired = await get_expired_orders()
    if not expired:
        return

    logger.info("Найдено истёкших orders (legacy): %d", len(expired))

    for order in expired:
        order_id     = order["id"]
        user_id      = order["user_id"]
        vpn_username = order["vpn_username"]

        if vpn_username:
            pass  # legacy SSH — больше не используем

        await mark_order_expired(order_id)
        logger.info("Order #%d истёк, пир удалён: %s", order_id, vpn_username)

        try:
            await bot.send_message(user_id, EXPIRY_NOTICE, parse_mode="HTML")
        except Exception as e:
            logger.warning("Не удалось уведомить user %d: %s", user_id, e)


async def _send_expiry_reminders(bot: Bot):
    """Отправляет напоминания за 3 дня и за 1 день до истечения подписки."""
    for days in (3, 1):
        subs = await get_subscriptions_expiring_soon(days)
        for sub in subs:
            user_id = sub["user_id"]
            if days == 3:
                text = (
                    "⏰ <b>Подписка истекает через 3 дня</b>\n\n"
                    "Успей продлить, чтобы VPN не отключился.\n"
                    "/start — открыть меню"
                )
            else:
                text = (
                    "🚨 <b>Подписка истекает завтра!</b>\n\n"
                    "Последний шанс продлить без перерыва в работе VPN.\n"
                    "/start — открыть меню"
                )
            try:
                await bot.send_message(user_id, text, parse_mode="HTML")
            except Exception as e:
                logger.warning("Не удалось отправить напоминание user %d: %s", user_id, e)
            await mark_reminded(sub["id"], days)


async def run_scheduler(bot: Bot):
    """Бесконечный цикл — запускать как asyncio background task из bot.py."""
    logger.info("Планировщик подписок запущен (интервал: %d сек)", CHECK_INTERVAL)
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await _process_expired_subscriptions(bot)
            await _process_expired_orders(bot)
            await _send_expiry_reminders(bot)
        except Exception as e:
            logger.error("Ошибка планировщика: %s", e)
