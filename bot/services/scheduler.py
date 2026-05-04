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
    get_servers_by_protocol,
    update_server_peer_count,
    update_config_traffic,
    get_config_id_by_vless_uuid,
    get_active_vless_uuids_by_server,
    get_active_vless_configs_with_plan,
    update_config_data,
)
from services.vpnctl_client import suspend_peer, client_for_server, VpnctlError
from handlers.vpn import (
    VPN_PLANS,
    vless_service_for_plan,
    vless_slow_service_for_plan,
)

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


async def _sync_vless_stats():
    """Pulls per-user traffic stats from each VLESS server's vpnctl agent
    and writes them to the configs table. Lets billing/quota logic work."""
    servers = await get_servers_by_protocol("vless")
    for server in servers:
        if not server.get("agent_url") or not server.get("agent_token"):
            continue
        try:
            client = client_for_server(server)
            peers = await client.list_peers("vless")
        except VpnctlError as e:
            logger.warning("vless stats sync skipped server=%s: %s", server.get("name"), e)
            continue
        except Exception as e:
            logger.warning("vless stats sync error server=%s: %s", server.get("name"), e)
            continue

        for peer in peers or []:
            uuid = peer.get("id")
            if not uuid:
                continue
            rx = int(peer.get("rx_bytes") or 0)
            tx = int(peer.get("tx_bytes") or 0)
            last_seen = peer.get("last_seen")
            if last_seen and last_seen.startswith("0001"):
                last_seen = None
            cfg_id = await get_config_id_by_vless_uuid(uuid)
            if cfg_id:
                await update_config_traffic(cfg_id, rx, tx, last_seen)


async def _apply_quota_throttle(bot: Bot):
    """For each VLESS config, check if soft-cap is exceeded and switch
    user between normal and throttled tiers via the agent."""
    configs = await get_active_vless_configs_with_plan()
    for cfg in configs:
        plan = VPN_PLANS.get(cfg["plan_key"])
        if not plan:
            continue
        cap_gb = plan.get("soft_cap_gb")
        if not cap_gb:
            continue  # legacy plan without speed-tier — пропускаем

        cap_bytes = cap_gb * (1024 ** 3)
        used = (cfg.get("rx_bytes") or 0) + (cfg.get("tx_bytes") or 0)
        cfg_data = cfg.get("config_data") or ""
        is_throttled = (":9443" in cfg_data) or (":9448" in cfg_data)
        should_throttle = used > cap_bytes

        if should_throttle == is_throttled:
            continue  # state already correct

        normal_svc = vless_service_for_plan(cfg["plan_key"])
        slow_svc = vless_slow_service_for_plan(cfg["plan_key"])
        if not slow_svc:
            continue

        server = await get_server_by_id(cfg["server_id"])
        if not server or not server.get("agent_url"):
            continue
        client = client_for_server(server)
        uuid = cfg["vless_uuid"]
        label = f"tg{cfg['user_id']}_{cfg['config_id']}"

        try:
            if should_throttle and not is_throttled:
                # Move into throttled tier: add to slow, remove from normal
                slow_peer = await client.add_peer(slow_svc, label, peer_id=uuid)
                await client.remove_peer(normal_svc, uuid)
                await update_config_data(cfg["config_id"], slow_peer.config)
                logger.info(
                    "throttled config #%d (used %.1f GB > %d GB cap)",
                    cfg["config_id"], used / 1024**3, cap_gb,
                )
                try:
                    await bot.send_message(
                        cfg["user_id"],
                        f"🐢 <b>Лимит трафика {cap_gb} GB исчерпан</b>\n\n"
                        f"Скорость снижена до {plan.get('throttle_mbps', '?')} Mbps до конца месяца.\n"
                        f"Если ты импортировал <b>Subscription URL</b> — конфиг обновится автоматически "
                        f"в течение нескольких минут.\n\n"
                        f"💎 Апгрейд тарифа в /start даёт больше квоты.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("notify throttle user %d: %s", cfg["user_id"], e)
            elif is_throttled and not should_throttle:
                # Restore: re-add to normal, remove from slow
                normal_peer = await client.add_peer(normal_svc, label, peer_id=uuid)
                await client.remove_peer(slow_svc, uuid)
                await update_config_data(cfg["config_id"], normal_peer.config)
                logger.info("throttle restored on config #%d", cfg["config_id"])
        except VpnctlError as e:
            logger.warning("throttle change failed for config #%d: %s", cfg["config_id"], e)
        except Exception as e:
            logger.warning("throttle change error for config #%d: %s", cfg["config_id"], e)


async def _sync_vless_active_uuids():
    """Sends the list of currently-active UUIDs to each VLESS server.
    Agent removes any UUID not in the list — stops users without a paid subscription."""
    servers = await get_servers_by_protocol("vless")
    for server in servers:
        if not server.get("agent_url") or not server.get("agent_token"):
            continue
        try:
            client = client_for_server(server)
            valid = await get_active_vless_uuids_by_server(server["id"])
            result = await client.sync_active_ids("vless", valid)
            logger.info(
                "vless sync: server=%s, valid=%d, kept=%d, removed=%d",
                server.get("name"),
                len(valid),
                result.get("kept", 0),
                len(result.get("removed", []) or []),
            )
        except VpnctlError as e:
            logger.warning("vless uuid sync skipped server=%s: %s", server.get("name"), e)
        except Exception as e:
            logger.warning("vless uuid sync error server=%s: %s", server.get("name"), e)


async def _daily_backup(bot: Bot):
    """Раз в сутки шлёт сжатый дамп bot.db админу в Telegram."""
    import gzip
    import shutil
    from datetime import datetime
    from aiogram.types import BufferedInputFile
    from config import ADMIN_ID
    from services.database import DB_PATH

    state_file = "/tmp/.last_backup_date"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with open(state_file) as f:
            if f.read().strip() == today:
                return  # уже отправили сегодня
    except FileNotFoundError:
        pass

    # snapshot — копируем перед сжатием, чтобы не блочить запись
    snap = "/tmp/bot.db.snapshot"
    shutil.copy2(DB_PATH, snap)
    with open(snap, "rb") as src, gzip.open(snap + ".gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    with open(snap + ".gz", "rb") as f:
        data = f.read()

    try:
        await bot.send_document(
            ADMIN_ID,
            BufferedInputFile(data, filename=f"bot-db-{today}.gz"),
            caption=f"📦 Daily backup · {today} · {len(data)//1024} KB",
        )
        with open(state_file, "w") as f:
            f.write(today)
        logger.info("daily backup отправлен (%d KB)", len(data) // 1024)
    except Exception as e:
        logger.warning("daily backup не отправлен: %s", e)


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
            await _sync_vless_stats()
            await _apply_quota_throttle(bot)
            await _sync_vless_active_uuids()
            await _daily_backup(bot)
        except Exception as e:
            logger.error("Ошибка планировщика: %s", e)
