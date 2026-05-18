"""
Фоновый планировщик подписок.

Жизненный цикл подписки:
  active → grace (при истечении expires_at)
    AWG : tc-throttle 256 кбит/с через агент (per-peer по dst IP на awg0)
    VLESS: пир перемещается в inbound vless-grace (порт 9453, tc 256 кбит/с)
    Конфиги остаются активными — пользователь может продлить без потери ключей.

  grace → expired (при истечении grace_until = expires_at + 14 дней)
    AWG : снимается throttle, пир удаляется, слот освобождается.
    VLESS: пир удаляется из vless-grace, слот освобождается.

Также обрабатывает старые заказы из таблицы orders (backward compat).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from services.database import (
    get_expired_subscriptions,
    get_grace_expired_subscriptions,
    get_configs_for_subscription,
    mark_subscription_expired,
    mark_subscription_grace,
    ensure_grace_column,
    revoke_config,
    reset_config_slot,
    get_subscriptions_expiring_soon,
    get_subscriptions_grace_ending_soon,
    mark_reminded,
    mark_grace_reminded,
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
    get_esim_profiles_for_usage_sync,
    update_esim_usage,
    get_winback_candidates,
    mark_winback_sent,
)
import services.esim_api as esim_api
from services.vpnctl_client import client_for_server, VpnctlError
from services.plans import (
    VPN_PLANS,
    vless_service_for_plan,
    vless_slow_service_for_plan,
)

logger = logging.getLogger(__name__)

GRACE_DAYS = 14

GRACE_NOTICE = (
    "🐢 <b>Подписка истекла</b>\n\n"
    "VPN работает ещё <b>14 дней</b> на скорости 256 кбит/с — "
    "специально чтобы Telegram оставался доступным и ты мог продлить.\n\n"
    "Видео и тяжёлые сайты тормозят. Продли сейчас — полная скорость вернётся сразу."
)

EXPIRY_NOTICE = (
    "⚠️ <b>VPN полностью отключён</b>\n\n"
    "Льготный период (14 дней) истёк. Для возобновления доступа "
    "оформи новую подписку.\n\n"
    "/start — открыть меню"
)

CHECK_INTERVAL = 3600  # секунд (1 час)


def _bot_version() -> str:
    """Lazy lookup BOT_VERSION чтобы избежать circular import."""
    try:
        from bot import BOT_VERSION
        return BOT_VERSION
    except Exception:
        return "dev"


async def _weekly_vacuum():
    """SQLite incremental_vacuum — освобождает пустые страницы порциями.
    Без этого bot.db растёт на 5-10% в месяц (fragmentation).
    incremental_vacuum(N) освобождает не более N страниц за вызов — не
    требует exclusive lock на всю БД в отличие от полного VACUUM.
    """
    import sqlite3 as _sqlite
    from services.database import DB_PATH
    def _vacuum_sync():
        conn = _sqlite.connect(str(DB_PATH))
        try:
            conn.execute("PRAGMA incremental_vacuum(1000)")
            conn.commit()
        finally:
            conn.close()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _vacuum_sync)
    logger.info("weekly incremental_vacuum completed")

# Inline-кнопка «Продлить» во всех retention-уведомлениях. Открывает Plans
# внутри Mini App одним кликом — это разница между «продлил из дивана» и
# «забыл и ушёл к конкуренту».
_WEBAPP_URL = os.getenv("WEBAPP_URL", "")


def _renew_kb() -> InlineKeyboardMarkup | None:
    """Inline-клавиатура с deep-link на /vpn/plans в Mini App. None если WEBAPP_URL пустой."""
    if not _WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💎 Продлить подписку",
            web_app=WebAppInfo(url=f"{_WEBAPP_URL}/vpn/plans"),
        )
    ]])


# Batch-friendly send helper. Telegram global rate-limit ~30 msg/sec; если бот
# поднимается после downtime и `_process_grace_expired_subscriptions` находит
# 80 истёкших подписок, без задержки попадёт в flood-control 429 и заблокируется
# временно. ~25/sec = безопасный потолок.
_TG_SEND_DELAY = 0.04  # 25 msg/sec


async def _send_throttled(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    """Шлёт сообщение с защитой от flood-control. Возвращает True если успешно."""
    from aiogram.exceptions import TelegramRetryAfter
    try:
        await bot.send_message(user_id, text, **kwargs)
        await asyncio.sleep(_TG_SEND_DELAY)
        return True
    except TelegramRetryAfter as e:
        logger.warning("TG flood control: sleep %ds then retry user=%d", e.retry_after, user_id, exc_info=True)
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(user_id, text, **kwargs)
            return True
        except Exception as retry_err:
            logger.warning("TG retry failed user=%d: %s", user_id, retry_err, exc_info=True)
            return False
    except Exception as e:
        logger.warning("send_message failed user=%d: %s", user_id, e, exc_info=True)
        return False


def _current_vless_service(config_data: str, plan_key: str) -> str:
    """Определяет текущий vpnctl-сервис VLESS-конфига по порту в config_data."""
    if ":9448" in config_data:
        return "vless-max-slow"
    if ":9443" in config_data:
        return "vless-base-slow"
    if ":9453" in config_data:
        return "vless-grace"
    return vless_service_for_plan(plan_key)


async def _process_expired_subscriptions(bot: Bot):
    """Переводит истёкшие подписки в grace-период (14 дней при 256 кбит/с).

    AWG  — применяет tc-throttle через агент (per-peer по assigned_ip на awg0).
    VLESS — перемещает пир в inbound vless-grace (порт 9453, tc 256 кбит/с).
    Конфиги остаются активными; пользователь может продлить без потери ключей.
    """
    expired_subs = await get_expired_subscriptions()
    if not expired_subs:
        return

    logger.info("Найдено истёкших подписок: %d", len(expired_subs))
    grace_until = (datetime.utcnow() + timedelta(days=GRACE_DAYS)).isoformat()
    # Если бот лежал >GRACE_DAYS, sub'а expired дольше grace_period →
    # переход в expired сразу, без grace 256 кбит/с. Иначе юзер получит
    # одно за другим уведомления «grace» и «expired» за час, а реально
    # сервис всё это время был недоступен.
    cutoff_expired_long_ago = (datetime.utcnow() - timedelta(days=GRACE_DAYS)).isoformat()

    for sub in expired_subs:
        sub_id   = sub["id"]
        user_id  = sub["user_id"]
        plan_key = sub.get("plan", "")

        # Bot-offline guard: если sub.expires_at < (now - GRACE_DAYS), значит
        # grace window уже истёк → пропускаем grace transition, сразу к expired.
        sub_expires = sub.get("expires_at") or ""
        if sub_expires and sub_expires < cutoff_expired_long_ago:
            logger.info(
                "Подписка #%d: expires_at=%s давно истекло (>%d дней), "
                "пропускаем grace → expired",
                sub_id, sub_expires[:10], GRACE_DAYS,
            )
            try:
                await mark_subscription_expired(sub_id)
                # Уведомление юзеру
                await _send_throttled(bot, user_id, EXPIRY_NOTICE, parse_mode="HTML",
                                       reply_markup=_renew_kb())
            except Exception as e:
                logger.warning("late-expire sub #%d: %s", sub_id, e, exc_info=True)
            continue

        configs = await get_configs_for_subscription(sub_id)
        logger.info("Подписка #%d: переводим %d конфиг(ов) в grace", sub_id, len(configs))

        for cfg in configs:
            server_id  = cfg.get("server_id")
            protocol   = cfg.get("protocol", "")
            cfg_id     = cfg["id"]
            peer_name  = cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            vless_uuid = cfg.get("vless_uuid") or ""

            if not server_id:
                continue
            server = await get_server_by_id(server_id)
            if not server or not server.get("agent_url"):
                continue

            try:
                client = client_for_server(server)

                if protocol == "awg":
                    # tc-throttle: ограничиваем пир на awg0 по dst IP
                    if assigned_ip and peer_name:
                        await client.throttle_peer("awg", peer_name, assigned_ip, kbps=256)
                        logger.info("AWG конфиг #%d throttled 256kbps (sub=%d)", cfg_id, sub_id)
                    else:
                        # Data drift: assigned_ip пустой → throttle невозможен
                        # без него (tc нужен dst IP для фильтра).  Без alert'а
                        # юзер получит full speed бесплатно 14 дней grace.
                        # Audit 17.05 поймал — раньше silently skipped.
                        logger.error(
                            "AWG cfg #%d (sub=%d) cannot throttle: "
                            "assigned_ip=%r peer_name=%r — admin must check data drift",
                            cfg_id, sub_id, assigned_ip, peer_name,
                        )
                        try:
                            from config import ADMIN_ID
                            if ADMIN_ID and bot is not None:
                                await bot.send_message(
                                    ADMIN_ID,
                                    f"⚠️ <b>AWG grace throttle SKIPPED</b>\n\n"
                                    f"cfg #{cfg_id} sub #{sub_id} — assigned_ip="
                                    f"<code>{assigned_ip or 'NULL'}</code>, "
                                    f"peer_name=<code>{peer_name or 'NULL'}</code>\n\n"
                                    f"Юзер сейчас на full speed в grace. "
                                    f"Найди и пофикси data drift вручную.",
                                    parse_mode="HTML",
                                )
                        except Exception:
                            pass  # admin alert — best effort

                elif protocol in ("vless", "vless-reality"):
                    # Перемещаем в grace inbound (порт 9453, tc 256 кбит/с).
                    # Атомарность: если add_peer succeeded но remove_peer упал,
                    # юзер окажется в ДВУХ inbound одновременно (двойной трафик
                    # на одном UUID — Xray молча примет первый коннект). Делаем
                    # compensating remove на vless-grace чтобы откатить add.
                    if vless_uuid:
                        config_data = cfg.get("config_data") or ""
                        current_svc = _current_vless_service(config_data, plan_key)
                        if current_svc != "vless-grace":
                            label = f"grace_{sub_id}_{cfg_id}"
                            grace_added = False
                            try:
                                grace_peer = await client.add_peer(
                                    "vless-grace", label, peer_id=vless_uuid
                                )
                                grace_added = True
                                await client.remove_peer(current_svc, vless_uuid)
                                if grace_peer.config:
                                    await update_config_data(cfg_id, grace_peer.config)
                                logger.info(
                                    "VLESS конфиг #%d → vless-grace (был: %s, sub=%d)",
                                    cfg_id, current_svc, sub_id,
                                )
                            except VpnctlError as e:
                                logger.warning(
                                    "VLESS grace move failed cfg #%d (added=%s): %s",
                                    cfg_id, grace_added, e, exc_info=True,
                                )
                                # Compensating remove: если grace_added=True но
                                # remove_peer на старом inbound упал, чистим
                                # vless-grace чтобы не было двух пиров на одном UUID.
                                if grace_added:
                                    try:
                                        await client.remove_peer("vless-grace", vless_uuid)
                                        logger.info(
                                            "VLESS cfg #%d: compensating remove из vless-grace выполнен",
                                            cfg_id,
                                        )
                                    except Exception as cleanup_err:
                                        logger.error(
                                            "VLESS cfg #%d: compensating remove FAILED — пир в двух inbound, нужен ручной фикс: %s",
                                            cfg_id, cleanup_err, exc_info=True,
                                        )
                                        try:
                                            from config import ADMIN_ID
                                            await bot.send_message(
                                                ADMIN_ID,
                                                f"⚠️ <b>VLESS split-brain</b>\n\n"
                                                f"Cfg #{cfg_id}: пир одновременно в двух inbound.\n"
                                                f"Нужен ручной фикс на сервере.\n\n"
                                                f"<code>{cleanup_err}</code>",
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            pass

            except Exception as e:
                logger.warning("grace throttle error cfg #%d: %s", cfg_id, e, exc_info=True)

        # Применяем pending downgrade (если был запланирован) на момент истечения.
        # Семантика: юзер на vpn_max нажал «downgrade до vpn_base» → expires_at
        # настаёт → переключаем sub.plan и сбрасываем pending. Без этого юзер
        # после продления продолжит платить за старый план.
        pending = sub.get("pending_plan")
        if pending and pending != plan_key:
            try:
                from services.database import apply_pending_plan_change
                await apply_pending_plan_change(sub_id, pending)
                logger.info(
                    "Подписка #%d: применён pending downgrade %s → %s",
                    sub_id, plan_key, pending,
                )
                plan_key = pending  # для следующих итераций (grace transition)
            except Exception as e:
                logger.warning(
                    "Подписка #%d: pending downgrade failed (%s → %s): %s",
                    sub_id, plan_key, pending, e, exc_info=True,
                )

        await mark_subscription_grace(sub_id, grace_until)
        logger.info("Подписка #%d → grace (до %s)", sub_id, grace_until[:10])

        await _send_throttled(
            bot, user_id, GRACE_NOTICE, parse_mode="HTML",
            reply_markup=_renew_kb(),
        )


async def _process_grace_expired_subscriptions(bot: Bot):
    """Окончательно отзывает конфиги, у которых истёк grace-период.

    AWG  — снимает tc-throttle, удаляет пир, освобождает слот.
    VLESS — удаляет пир из vless-grace inbound, освобождает слот.

    Race protection: между snapshot'ом `grace_subs` и началом revoke юзер
    может заплатить и `try_renew_from_grace` атомарно перевёл sub в active.
    Atomic UPDATE в БД сам по себе НЕ останавливает наш скедулер — поэтому
    делаем явный re-check `subscriptions.status` ДО revoke каждого config'а.
    Audit 17.05 #2: без re-check'a юзер платил → DB renew OK → 10 сек спустя
    скедулер revoke'ал все его VLESS peers, юзер видел «отвал» сразу после
    оплаты.
    """
    from services.database import get_subscription_by_id
    grace_subs = await get_grace_expired_subscriptions()
    if not grace_subs:
        return

    logger.info("Grace-период истёк: %d подписок", len(grace_subs))

    for sub in grace_subs:
        sub_id   = sub["id"]
        user_id  = sub["user_id"]
        plan_key = sub.get("plan", "")

        # Re-check status перед началом revoke'а — sub могла быть renew'нута.
        cur = await get_subscription_by_id(sub_id)
        if not cur or cur.get("status") != "grace":
            logger.info(
                "sub #%d skipped — status уже %s (race с renew-from-grace)",
                sub_id, cur.get("status") if cur else "deleted",
            )
            continue

        configs = await get_configs_for_subscription(sub_id)

        aborted = False
        for cfg in configs:
            # Per-config re-check: даже внутри одной sub revoke'ом всех конфигов
            # может занять несколько секунд (агент-RPC). Renew-from-grace мог
            # прилететь в этом окне.
            cur = await get_subscription_by_id(sub_id)
            if not cur or cur.get("status") != "grace":
                logger.info(
                    "sub #%d revoke aborted mid-loop — status стал %s",
                    sub_id, cur.get("status") if cur else "deleted",
                )
                aborted = True
                break

            server_id   = cfg.get("server_id")
            protocol    = cfg.get("protocol", "")
            cfg_id      = cfg["id"]
            peer_name   = cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            vless_uuid  = cfg.get("vless_uuid") or ""

            if server_id:
                server = await get_server_by_id(server_id)
                if server and server.get("agent_url"):
                    try:
                        client = client_for_server(server)

                        if protocol == "awg":
                            if assigned_ip and peer_name:
                                await client.unthrottle_peer("awg", peer_name, assigned_ip)
                            await client.remove_peer("awg", peer_name)
                            await update_server_peer_count(server_id, -1)

                        elif protocol in ("vless", "vless-reality"):
                            if vless_uuid:
                                config_data = cfg.get("config_data") or ""
                                svc = _current_vless_service(config_data, plan_key)
                                await client.remove_peer(svc, vless_uuid)
                                await update_server_peer_count(server_id, -1)

                    except Exception as e:
                        logger.warning("revoke grace cfg #%d: %s", cfg_id, e, exc_info=True)

            await reset_config_slot(cfg_id)
            logger.info("Конфиг #%d отозван (grace истёк, sub=%d)", cfg_id, sub_id)

        if aborted:
            continue

        # Final atomic check: переводим в expired ТОЛЬКО если ещё grace.
        # Если renew-from-grace успел между нашими per-config re-check'ами
        # и этим финальным UPDATE — no-op, юзер получает renew'нутый sub
        # без destructive change.
        from services.database import mark_subscription_expired_from_grace
        if await mark_subscription_expired_from_grace(sub_id):
            logger.info("Подписка #%d → expired (post-grace)", sub_id)
        else:
            logger.info(
                "sub #%d: mark_expired no-op — статус уже не grace (renew race)",
                sub_id,
            )

        await _send_throttled(
            bot, user_id, EXPIRY_NOTICE, parse_mode="HTML",
            reply_markup=_renew_kb(),
        )


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
            logger.warning("Не удалось уведомить user %d: %s", user_id, e, exc_info=True)


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
            logger.warning("vless stats sync skipped server=%s: %s", server.get("name"), e, exc_info=True)
            continue
        except Exception as e:
            logger.warning("vless stats sync error server=%s: %s", server.get("name"), e, exc_info=True)
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
                    logger.warning("notify throttle user %d: %s", cfg["user_id"], e, exc_info=True)
            elif is_throttled and not should_throttle:
                # Restore: re-add to normal, remove from slow
                normal_peer = await client.add_peer(normal_svc, label, peer_id=uuid)
                await client.remove_peer(slow_svc, uuid)
                await update_config_data(cfg["config_id"], normal_peer.config)
                logger.info("throttle restored on config #%d", cfg["config_id"])
        except VpnctlError as e:
            logger.warning("throttle change failed for config #%d: %s", cfg["config_id"], e, exc_info=True)
        except Exception as e:
            logger.warning("throttle change error for config #%d: %s", cfg["config_id"], e, exc_info=True)


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
            logger.warning("vless uuid sync skipped server=%s: %s", server.get("name"), e, exc_info=True)
        except Exception as e:
            logger.warning("vless uuid sync error server=%s: %s", server.get("name"), e, exc_info=True)


async def _daily_backup(bot: Bot):
    """Раз в сутки шлёт сжатый дамп bot.db админу в Telegram.

    Безопасность:
      - SQLite-aware snapshot через `sqlite3.Connection.backup()` —
        корректно для WAL-режима (учитывает WAL/SHM, в отличие от
        `shutil.copy2` который мог терять свежие транзакции).
      - sub_token и payment_id затираются NULL'ами в snapshot — это
        самые чувствительные колонки. Утечка backup'а в TG = потеря
        самого backup'а, но не прямой доступ к VPN-конфигам юзеров.

    Health-alert:
      - state-файл `/opt/vpnbot/.last_backup_date` (persistent, не /tmp!)
        хранит дату последнего успешного backup'а.
      - Если последний backup был > 2 дней назад — шлём alert админу.
        Это покрывает молчаливые failures: bot.send_document отклонён,
        диск переполнен, sqlite повреждён, etc.
    """
    import gzip
    import os
    import sqlite3
    from datetime import datetime, timedelta
    from aiogram.types import BufferedInputFile
    from config import ADMIN_ID
    from services.database import DB_PATH

    # Persistent state — раньше был в /tmp и терялся при рестарте VPS
    # (что давало дубль backup'ов и ломало health-check «3 дня не было»).
    state_file = "/opt/vpnbot/.last_backup_date"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    last_backup_date: str | None = None
    try:
        with open(state_file) as f:
            last_backup_date = f.read().strip()
            if last_backup_date == today:
                return  # уже отправили сегодня
    except FileNotFoundError:
        pass

    # Health-alert: если последний успешный backup был > 2 дней назад,
    # шлём alert. Делаем это ДО самого backup'а — если он опять упадёт,
    # хотя бы alert уйдёт.
    if last_backup_date:
        try:
            last_dt = datetime.strptime(last_backup_date, "%Y-%m-%d")
            days_since = (datetime.utcnow() - last_dt).days
            if days_since >= 2:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Backup health alert</b>\n\n"
                        f"Последний успешный backup: <b>{last_backup_date}</b> "
                        f"({days_since} дн. назад).\n"
                        f"Проверь /opt/vpnbot logs — что-то ломает daily backup "
                        f"(disk full / Telegram API / sqlite corruption).",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("backup health alert failed: %s", e, exc_info=True)
        except ValueError:
            pass  # malformed state file — игнорируем

    # Snapshot в persistent /opt/vpnbot/.snapshots/ — не /tmp:
    # /tmp может быть tmpfs (теряется при reboot) или tmpwatch-cleaned.
    # На небольших VPS / partition может переполниться при `gzip` (~10 MB → 5 MB).
    snap_dir = "/opt/vpnbot/.snapshots"
    os.makedirs(snap_dir, exist_ok=True)
    snap = f"{snap_dir}/bot.db.snapshot"
    if os.path.exists(snap):
        os.unlink(snap)
    if os.path.exists(snap + ".gz"):
        os.unlink(snap + ".gz")

    # Backup + redact + gzip — все blocking-операции. На больших БД (50+ MB)
    # это 5-10 секунд блокировки event loop'а. polling замораживается, юзеры
    # видят "бот не отвечает". Переносим в default executor (thread pool).
    def _backup_blocking() -> bytes:
        # SQLite-aware backup (учитывает WAL+SHM, в отличие от shutil.copy)
        src = sqlite3.connect(str(DB_PATH))
        try:
            dst = sqlite3.connect(snap)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        # Redact sensitive columns на копии — sub_token даёт постоянный
        # доступ к VLESS-конфигам через /sub/{token}.
        conn = sqlite3.connect(snap)
        try:
            conn.execute("UPDATE users SET sub_token=NULL")
            conn.commit()
        finally:
            conn.close()

        with open(snap, "rb") as src_f, gzip.open(snap + ".gz", "wb", compresslevel=9) as dst_f:
            for chunk in iter(lambda: src_f.read(64 * 1024), b""):
                dst_f.write(chunk)
        with open(snap + ".gz", "rb") as f:
            return f.read()

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _backup_blocking)

    try:
        await bot.send_document(
            ADMIN_ID,
            BufferedInputFile(
                data,
                filename=f"bot-db-{today}-{_bot_version()[:7]}.gz",
            ),
            caption=f"📦 Daily backup · {today} · {len(data)//1024} KB · sub_tokens redacted",
        )
        with open(state_file, "w") as f:
            f.write(today)
        logger.info("daily backup отправлен (%d KB)", len(data) // 1024)
    except Exception as e:
        logger.warning("daily backup не отправлен: %s", e, exc_info=True)
    finally:
        # cleanup
        for p in (snap, snap + ".gz"):
            try:
                os.unlink(p)
            except OSError:
                pass


async def _send_expiry_reminders(bot: Bot):
    """Напоминания за 3д и за 1д до истечения. Триалы используют свой текст —
    «Успей купить подписку», а не «продли», т.к. триал ещё не платный.
    Для триала 3-дневный reminder пропускается (попал бы сразу после активации
    т.к. триал = 3 дня)."""
    for days in (3, 1):
        subs = await get_subscriptions_expiring_soon(days)
        for sub in subs:
            user_id = sub["user_id"]
            is_trial = sub.get("plan") == "vpn_trial"

            # Триал = 3 дня. Reminder за 3 дня попадает сразу после активации —
            # бесполезный спам. Шлём только 1-day reminder для триалов.
            if is_trial and days == 3:
                await mark_reminded(sub["id"], days)
                continue

            if is_trial:
                # 1 день до конца триала — главный конверсионный момент.
                text = (
                    "⏳ <b>Пробный период заканчивается через 24 часа</b>\n\n"
                    "Понравилось? Выбери постоянный тариф — "
                    "от 200 ₽/мес, та же скорость, без перерыва.\n\n"
                    "Продли сейчас — VPN продолжит работать без остановки."
                )
            elif days == 3:
                text = (
                    "⏰ <b>Подписка истекает через 3 дня</b>\n\n"
                    "Успей продлить, чтобы VPN не отключился."
                )
            else:
                text = (
                    "🚨 <b>Подписка истекает завтра!</b>\n\n"
                    "Последний шанс продлить без перерыва в работе VPN."
                )
            await _send_throttled(
                bot, user_id, text, parse_mode="HTML",
                reply_markup=_renew_kb(),
            )
            await mark_reminded(sub["id"], days)

    # ── Grace reminder: 3 дня до полного закрытия доступа ─────────────────
    # Юзер в grace ловит throttle 256 кбит/с и через 14 дней теряет доступ
    # полностью.  За 3 дня до этого момента — последний конверсионный шанс
    # вернуть его в active, иначе уходит.  Без этого reminder'а retention
    # loss потому что юзер чаще всего забывает что VPN на throttle.
    grace_subs = await get_subscriptions_grace_ending_soon(3)
    for sub in grace_subs:
        text = (
            "⏰ <b>Через 3 дня VPN отключится</b>\n\n"
            "Подписка в режиме 256 кбит/с — а через 3 дня закроется совсем. "
            "Продли сейчас, чтобы вернуть полную скорость и не остаться без VPN."
        )
        await _send_throttled(
            bot, sub["user_id"], text, parse_mode="HTML",
            reply_markup=_renew_kb(),
        )
        await mark_grace_reminded(sub["id"])


async def _send_renewal_reminders(bot: Bot):
    """За 3 дня до auto-charge на recurring subs (Lava + Stars) шлём
    уведомление: «через 3 дня спишется N₽/⭐ — отменить можно тут».

    Снижает chargeback risk + строит trust («предупредил, не сюрприз»).
    """
    from services.database import get_recurring_renewal_due_soon, mark_renewal_reminded
    subs = await get_recurring_renewal_due_soon(days_before=3)
    if not subs:
        return
    logger.info("renewal reminders: %d sub'ов готовы напомнить", len(subs))

    for sub in subs:
        user_id = sub["user_id"]
        plan_key = sub.get("plan") or ""
        provider = sub.get("payment_provider") or ""
        plan = VPN_PLANS.get(plan_key, {})
        plan_name = plan.get("name", plan_key)
        amount_rub = sub.get("amount_rub") or int(float(plan.get("rub", 0)))
        stars = plan.get("stars", 0)

        try:
            cur_expires = datetime.fromisoformat(sub.get("expires_at") or datetime.utcnow().isoformat())
            days_left = max(0, (cur_expires - datetime.utcnow()).days)
        except Exception:
            days_left = 3

        if provider == "lavatop":
            text = (
                f"🔁 <b>Через {days_left} {'день' if days_left == 1 else 'дня' if days_left < 5 else 'дней'} "
                f"спишется {amount_rub} ₽ с твоей карты</b>\n\n"
                f"Тариф: <b>{plan_name}</b>\n"
                f"Дата списания: <b>{cur_expires.strftime('%d.%m.%Y')}</b>\n\n"
                f"Если не хочешь продлевать — отмени в Mini App "
                f"(VPN → кнопка «Отменить автопродление»). "
                f"VPN продолжит работать до конца оплаченного периода."
            )
        else:  # stars
            text = (
                f"🔁 <b>Через {days_left} {'день' if days_left == 1 else 'дня' if days_left < 5 else 'дней'} "
                f"Telegram спишет {stars} ⭐ за продление</b>\n\n"
                f"Тариф: <b>{plan_name}</b>\n"
                f"Дата списания: <b>{cur_expires.strftime('%d.%m.%Y')}</b>\n\n"
                f"Если не хочешь продлевать — отмени в Telegram: "
                f"Настройки → Звёзды → Подписки → выбери MAX VPN → Cancel."
            )

        sent = await _send_throttled(bot, user_id, text, parse_mode="HTML")
        if sent:
            await mark_renewal_reminded(sub["id"])


async def _winback_campaign(bot: Bot):
    """Win-back: через 7-14 дней после истечения шлём реактивационное письмо.

    Цель — вернуть ушедших пользователей пока они ещё помнят про VPN.
    7 дней: успели почувствовать что VPN нет, но ещё не забыли про нас.
    14 дней: крайний срок, дальше CTR резко падает.

    Один раз на подписку (winback_sent=1) — не спамим.
    Пользователь с active/grace подпиской исключается (уже вернулся).
    """
    candidates = await get_winback_candidates(days_min=7, days_max=14)
    if not candidates:
        return
    logger.info("win-back: %d кандидатов", len(candidates))
    for sub in candidates:
        user_id = sub["user_id"]
        sub_id  = sub["id"]
        text = (
            "👋 <b>Скучаем без тебя!</b>\n\n"
            "Прошла неделя, а VPN всё ещё выключен.\n\n"
            "Возможно, что-то не устроило — напиши нам в поддержку, "
            "разберёмся. Или просто продли — тарифы с 200 ₽/мес, "
            "без контракта, первый раз 3 дня в подарок при оплате на месяц.\n\n"
            "Будем рады видеть тебя снова 🙂"
        )
        sent = await _send_throttled(
            bot, user_id, text, parse_mode="HTML",
            reply_markup=_renew_kb(),
        )
        if sent:
            await mark_winback_sent(sub_id)


async def _sync_esim_usage():
    """Раз в 3 часа батчем тянет /esim/usage/query для активных eSIM-профилей.
    Лимит API: 10 esimTranNo за один запрос; rate limit 8 req/sec.
    Юзедж у esimaccess обновляется раз в 2-3 ч, чаще опрашивать смысла нет."""
    profiles = await get_esim_profiles_for_usage_sync(limit=200)
    if not profiles:
        return

    tran_nos = [p["esim_tran_no"] for p in profiles if p["esim_tran_no"]]
    BATCH = 10
    updated = 0
    for i in range(0, len(tran_nos), BATCH):
        batch = tran_nos[i:i + BATCH]
        try:
            resp = await esim_api.usage_query(batch)
        except Exception as e:
            logger.warning("eSIM usage_query batch failed: %s", e, exc_info=True)
            continue
        for u in (resp.get("obj") or {}).get("esimUsageList") or []:
            tn = u.get("esimTranNo")
            used = u.get("dataUsage", 0)
            if tn:
                await update_esim_usage(tn, used)
                updated += 1
        # Лёгкий throttle между батчами (rate limit 8 req/s)
        await asyncio.sleep(0.2)

    if updated:
        logger.info("eSIM usage sync: обновлено %d профилей", updated)


# Счётчик тиков шедулера для запуска редких задач (eSIM usage — раз в 3ч)
_TICK = 0
_ESIM_SYNC_EVERY_N_TICKS = 3  # CHECK_INTERVAL=1ч → раз в 3ч

# Health-probe — отдельный таск, бьёт чаще основного шедулера.
HEALTH_PROBE_INTERVAL_SEC = 60
HEALTH_CLEANUP_INTERVAL_SEC = 24 * 3600  # раз в сутки чистим логи старше 31 дня

# Фоновые таски удерживаются здесь — без этого `asyncio.create_task()` может
# быть собран GC, и task незаметно умрёт (классический asyncio-footgun).
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro, name: str | None = None) -> asyncio.Task:
    """Запускает фоновую корутину и удерживает ссылку. Снимает её
    после завершения, чтобы set не рос бесконечно.

    `name` помогает в debug-выводе asyncio.all_tasks() — без него видны
    только generic "Task pending coro=<...>" что усложняет диагностику
    зависших или утечённых тасков.
    """
    task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def _run_health_loop(bot: Bot | None = None):
    """Independent loop: probe servers every 60s, write to server_health_log.
    Передаём bot чтобы health.py мог слать alert админу при auto-(de)activate.
    """
    from services.health import probe_all_servers, cleanup_old_logs
    cleanup_counter = 0
    logger.info("Health-probe запущен (интервал: %d сек)", HEALTH_PROBE_INTERVAL_SEC)
    while True:
        try:
            await probe_all_servers(bot)
        except Exception as e:
            logger.warning("health probe error: %s", e, exc_info=True)
        cleanup_counter += HEALTH_PROBE_INTERVAL_SEC
        if cleanup_counter >= HEALTH_CLEANUP_INTERVAL_SEC:
            cleanup_counter = 0
            try:
                await cleanup_old_logs(keep_days=31)
                logger.info("health: log cleanup done")
            except Exception as e:
                logger.warning("health cleanup error: %s", e, exc_info=True)
        await asyncio.sleep(HEALTH_PROBE_INTERVAL_SEC)


async def run_scheduler(bot: Bot):
    """Бесконечный цикл — запускать как asyncio background task из bot.py."""
    global _TICK
    logger.info("Планировщик подписок запущен (интервал: %d сек)", CHECK_INTERVAL)

    # Миграция: добавляет grace_until в subscriptions если её ещё нет.
    await ensure_grace_column()

    # Запускаем health-probe отдельным таском — он бьёт каждые 60с независимо.
    # `_spawn_bg` удерживает ссылку, чтобы GC не убил task.
    _spawn_bg(_run_health_loop(bot))

    # Первый прогон через 60с после старта — чтобы:
    #   1) не зависнуть на медленных задачах в момент cold start (juggling polling)
    #   2) не ждать целый час до первой проверки истёкших подписок после рестарта
    # 60с — компромисс: достаточно для прогрева, но не сутки.
    FIRST_TICK_DELAY = 60

    # Per-task timeout. Без него один залипший vless-sync (медленный агент) на
    # 10 мин блокировал бы reminders на этот час → юзер не получает
    # «осталось 1 день» → не продлевает (sec audit edge-case C2).
    # Критичные retention-таски (expiry/grace/reminders) идут с короткими
    # timeout'ами, медленные sync'и — с longer.
    async def _safe(name: str, coro, timeout: int = 180):
        try:
            await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.CancelledError:
            # При shutdown event-loop'а CancelledError должен прокинуться
            # дальше, иначе scheduler становится «не убиваемым» и graceful
            # shutdown зависает на await.
            raise
        except asyncio.TimeoutError:
            logger.error("scheduler task '%s' timed out after %ds", name, timeout)
        except Exception as e:
            logger.exception("scheduler task '%s' failed: %s", name, e)

    while True:
        await asyncio.sleep(FIRST_TICK_DELAY if _TICK == 0 else CHECK_INTERVAL)
        _TICK += 1
        # Критичные retention первыми и с коротким timeout — чтобы даже если
        # медленный agent залип, юзеры получили уведомления вовремя.
        await _safe("expiry_reminders", _send_expiry_reminders(bot),     timeout=120)
        await _safe("renewal_reminders", _send_renewal_reminders(bot),   timeout=60)
        await _safe("expired_subs",     _process_expired_subscriptions(bot),       timeout=180)
        await _safe("grace_expired",    _process_grace_expired_subscriptions(bot), timeout=180)
        await _safe("expired_orders",   _process_expired_orders(bot),    timeout=60)
        # Менее критичные / медленные — отдельно с large timeout'ом.
        await _safe("vless_stats",      _sync_vless_stats(),             timeout=300)
        await _safe("quota_throttle",   _apply_quota_throttle(bot),      timeout=300)
        await _safe("vless_uuid_sync",  _sync_vless_active_uuids(),      timeout=300)
        await _safe("daily_backup",     _daily_backup(bot),              timeout=240)
        if _TICK % _ESIM_SYNC_EVERY_N_TICKS == 0:
            await _safe("esim_usage",   _sync_esim_usage(),              timeout=180)
        # VACUUM раз в неделю (168 тиков). Без него БД растёт после
        # delete/update — SQLite не освобождает страницы автоматически.
        # incremental_vacuum дешевле full VACUUM (не блокирует БД целиком).
        if _TICK % (24 * 7) == 0:
            await _safe("db_vacuum",    _weekly_vacuum(),                timeout=300)
        # Stuck activating slots — каждые 4 часа. Слоты зависают в
        # 'activating' если provision упал (агент недоступен, таймаут).
        # Без этого юзер видит "слот занят" бесконечно до рестарта бота.
        if _TICK % 4 == 0:
            from services.database import cleanup_stuck_activating_slots
            n = await cleanup_stuck_activating_slots()
            if n:
                logger.info("cleanup_stuck_activating: сброшено %d слотов", n)
        # Win-back кампания — раз в сутки. Шлём реактивационное сообщение
        # пользователям у которых sub истёк 7-14 дней назад и они не вернулись.
        if _TICK % 24 == 0:
            await _safe("winback",      _winback_campaign(bot),          timeout=120)
