"""
Grace renewal helper — общая логика «юзер в grace платит → продлеваем
существующую sub вместо создания новой».

Зачем shared:  изначально жила в `handlers/vpn.py:_deliver_vpn` (Stars-only).
Юзер в grace платит через CryptoBot/Cryptomus/Lava → webhook-хендлеры
создавали новую sub, старая grace-sub продолжала throttle'ить → юзер
заплатил, VPN тормозит, тикет в саппорт.  Теперь все 4 платёжки идут
через эту функцию.

Что делает:
  1. Проверяет: у юзера есть active subscription со status='grace' и тем же
     plan_key как новая оплата.
  2. Если да — renew_subscription_from_grace (status→active, expires +30д,
     сбрасывает reminded_*) и шлёт unthrottle на агенте (AWG-tc снять,
     VLESS из vless-grace вернуть в normal inbound).
  3. Записывает платёж + шлёт TG-сообщение «Подписка продлена».
  4. Возвращает True — caller должен пропустить create_subscription.

Если grace не обнаружен — возвращает False, обычный create-flow.
"""
import logging
from typing import Any

from aiogram import Bot

from services.vpnctl_client import client_for_server, VpnctlError

logger = logging.getLogger(__name__)


async def try_renew_from_grace(
    bot: Bot,
    user_id: int,
    plan_key: str,
    plan: dict,
    payment_id: str,
    *,
    method: str,
    stars: int = 0,
    amount_rub: int = 0,
) -> bool:
    """Returns True если grace-продление выполнено (caller skip create), иначе False.

    `method` — "stars" / "crypto" / "cryptomus" / "lavatop"; пишется в payments-log
    для админ-аналитики.
    """
    from services.database import (
        get_active_subscription, renew_subscription_from_grace,
        get_configs_for_subscription, get_server_by_id, update_config_data,
        record_payment,
    )
    from services.plans import vless_service_for_plan

    existing = await get_active_subscription(user_id)
    if not existing:
        return False
    if existing.get("status") != "grace":
        return False
    if existing.get("plan") != plan_key:
        # Юзер в grace на ДРУГОМ плане. Раньше: создавалась 2-я sub, старая
        # grace sub висела до natural expire (14 дней throttle, дальше
        # auto-revoke).  Audit 17.05 #8: закрываем старую grace, чтобы
        # её AWG/VLESS пиры освободили слоты и не сбивали с толку в Mini App.
        await _close_dangling_grace(bot, existing["id"], existing.get("plan", ""))
        return False  # caller продолжает обычный create-flow

    sub_id = existing["id"]
    renewed = await renew_subscription_from_grace(
        sub_id, days=plan["duration_days"],
    )
    if not renewed:
        # race с scheduler'ом: пока мы читали grace, он перевёл в expired.
        # Безопасный fallback — caller создаст обычную sub.
        logger.warning(
            "renew_from_grace race: sub=%d уже не grace, fallback на create",
            sub_id,
        )
        return False

    logger.info(
        "renew_from_grace: user=%d sub=%d plan=%s method=%s",
        user_id, sub_id, plan_key, method,
    )

    # Unthrottle на агенте — AWG-tc снять, VLESS-grace вернуть в normal inbound.
    target_vless_svc = vless_service_for_plan(plan_key)
    try:
        configs = await get_configs_for_subscription(sub_id)
        for cfg in configs:
            server_id = cfg.get("server_id")
            if not server_id:
                continue
            server = await get_server_by_id(server_id)
            if not server or not server.get("agent_url"):
                continue
            client = client_for_server(server)
            proto = cfg.get("protocol", "")
            peer_id = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            try:
                if proto == "awg" and peer_id and assigned_ip:
                    await client.unthrottle_peer("awg", peer_id, assigned_ip)
                elif proto in ("vless", "vless-reality") and peer_id:
                    new_peer = await client.add_peer(
                        target_vless_svc, f"u{user_id}_c{cfg['id']}",
                        peer_id=peer_id,
                    )
                    await client.remove_peer("vless-grace", peer_id)
                    if new_peer.config:
                        await update_config_data(cfg["id"], new_peer.config)
            except VpnctlError as e:
                logger.warning(
                    "renew-from-grace unthrottle cfg #%d: %s",
                    cfg["id"], e, exc_info=True,
                )
    except Exception as e:
        # Outer-catch: даже если unthrottle упал — подписка УЖЕ продлена в БД,
        # scheduler следующим tick'ом разрулит (вернёт пиры на normal inbound).
        # Лучше ответить юзеру «продлено» чем тихо упасть.
        logger.error("renew-from-grace unthrottle outer: %s", e, exc_info=True)

    # Записываем платёж (для admin /payments + LTV-аналитики).
    try:
        await record_payment(
            user_id=user_id, subscription_id=sub_id,
            method=method, stars=stars, tx_id=payment_id,
        )
    except Exception as e:
        # Не блокируем UX из-за ошибки записи payment'а — это «не критично».
        logger.warning("record_payment after grace-renew failed: %s", e)

    # TG-сообщение юзеру.
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Подписка продлена!</b>\n\n"
            f"📅 Действует до: <b>{renewed['expires_at'][:10]}</b>\n"
            f"⚡ Полная скорость восстановлена — VPN снова работает без ограничений.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("send grace-renew confirmation to user %d: %s", user_id, e)

    return True


async def _close_dangling_grace(bot: Bot, sub_id: int, plan_key: str) -> None:
    """Закрывает grace-sub (status='grace' → 'expired') + revoke агент-side
    конфигов. Используется когда юзер в grace на одном плане покупает другой
    (cross-plan upgrade/downgrade), audit 17.05 #8.

    Best-effort: если agent down, DB всё равно помечается expired. Drift на
    агенте подберёт `_sync_vless_active_uuids` следующим тиком.
    """
    from services.database import (
        get_configs_for_subscription, get_server_by_id,
        update_server_peer_count, reset_config_slot,
        mark_subscription_expired_from_grace,
    )
    from services.plans import vless_service_for_plan as _vless_svc

    configs = await get_configs_for_subscription(sub_id)
    for cfg in configs:
        cfg_id = cfg["id"]
        server_id = cfg.get("server_id")
        protocol = cfg.get("protocol", "")
        peer_name = cfg.get("peer_name") or ""
        vless_uuid = cfg.get("vless_uuid") or ""
        if server_id:
            server = await get_server_by_id(server_id)
            if server and server.get("agent_url"):
                try:
                    cli = client_for_server(server)
                    if protocol == "awg" and peer_name:
                        try: await cli.remove_peer("awg", peer_name)
                        except VpnctlError: pass
                        await update_server_peer_count(server_id, -1)
                    elif protocol in ("vless", "vless-reality") and vless_uuid:
                        # peer мог быть в vless-grace или обычном inbound
                        config_data = cfg.get("config_data") or ""
                        svc = "vless-grace" if ":9453" in config_data else _vless_svc(plan_key)
                        try: await cli.remove_peer(svc, vless_uuid)
                        except VpnctlError: pass
                        await update_server_peer_count(server_id, -1)
                except Exception as e:
                    logger.warning("close_dangling_grace cfg #%d: %s", cfg_id, e)
        await reset_config_slot(cfg_id)

    # Финальный atomic mark: только grace → expired (защита от race с
    # параллельным renew, хотя в нашем callsite race нет).
    if await mark_subscription_expired_from_grace(sub_id):
        logger.info(
            "Cross-plan: dangling grace sub #%d (plan=%s) → expired, configs revoked",
            sub_id, plan_key,
        )
