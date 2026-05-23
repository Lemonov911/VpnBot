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
import asyncio
import logging

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
    auto_renew: bool = False,
) -> bool:
    """Returns True если grace-продление выполнено (caller skip create), иначе False.

    `method` — "stars" / "crypto" / "oxapay" / "lavatop"; пишется в payments-log
    для админ-аналитики.

    `auto_renew` — True для Stars recurring first-payment; проставляет
    auto_renew=1 и payment_provider='stars' чтобы следующий renewal charge
    нашёл sub через get_recurring_sub_for_renewal (без этого создалась бы
    вторая sub на следующий месяц).
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
        # EU-F7: НЕ закрываем dangling grace eagerly. Раньше делали:
        #   await _close_dangling_grace(...)
        # — закрытие шло ПЕРЕД создание новой sub + provision. Если
        # provision'ил с ошибкой, у юзера terminated старый VPN + не работал
        # новый = service interruption.
        # Теперь: оставляем grace-sub до natural expire (grace_until) —
        # scheduler-тик подберёт её через _reconcile_orphans / grace-reap.
        # Юзер получит overlap: новый full-speed VPN + старый throttled, пока
        # 14-day grace_until не истечёт. Это безопаснее даунтайма.
        logger.info(
            "try_renew_from_grace: user %d has grace on different plan (%s vs %s), "
            "leaving for scheduler to reap on natural grace_until expiry",
            user_id, existing.get("plan"), plan_key,
        )
        return False  # caller продолжает обычный create-flow

    sub_id = existing["id"]
    renewed = await renew_subscription_from_grace(
        sub_id, days=plan["duration_days"],
        auto_renew=auto_renew,
        payment_provider="stars" if method == "stars" and auto_renew else None,
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
    # Audit F5: cache invalidate — grace→active. Без этого Mini App до 2с
    # после оплаты всё ещё рисует «🐢 эконом-режим» баннер.
    from services.sub_cache import invalidate as _inv_sub_cache
    _inv_sub_cache(user_id)

    # Unthrottle на агенте — AWG-tc снять, VLESS-grace вернуть в normal inbound.
    await unthrottle_sub_configs(sub_id, user_id, plan_key)

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
        from datetime import datetime
        from services.database import get_user_lang
        from services.i18n_bot import t
        try:
            exp_dt = datetime.fromisoformat(str(renewed['expires_at']).replace(' ', 'T'))
            exp_str = exp_dt.strftime('%d.%m.%Y')
        except Exception:
            exp_str = str(renewed['expires_at'])[:10]
        lang = await get_user_lang(user_id) or "ru"
        await bot.send_message(
            user_id,
            "\n\n".join([
                t(lang, "bot_grace_renewed_title"),
                t(lang, "bot_grace_renewed_until", until=exp_str),
                t(lang, "bot_grace_renewed_speed"),
            ]),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("send grace-renew confirmation to user %d: %s", user_id, e)

    # Если у юзера ещё есть active/grace trial sub — закрываем её сейчас.
    # Без этого Happ загружает оба VLESS-пира (платный + trial), и trial
    # через сутки уйдёт в grace на 256 кбит/с — юзер "оплатил, но тормозит".
    # Те же грабли что _deliver_vpn fix'ил на новой sub, но grace-renew path
    # их обходил.
    try:
        from services.database import get_user_subscriptions_by_plan
        from handlers.vpn import _close_trial_on_paid_purchase
        trials = await get_user_subscriptions_by_plan(
            user_id, "vpn_trial", status=("active", "grace"),
        )
        for trial_sub in trials:
            if trial_sub["id"] != sub_id:
                await _close_trial_on_paid_purchase(trial_sub["id"], user_id)
    except Exception as e:
        logger.warning("trial close on grace-renew sub=%d: %s", sub_id, e, exc_info=True)

    return True


async def unthrottle_sub_configs(sub_id: int, user_id: int, plan_key: str) -> None:
    """Снимает throttle с конфигов подписки после выхода из grace.

    AWG  — unthrottle_peer (снимает tc-фильтр на awg0).
    VLESS — add_peer в normal inbound + remove_peer из vless-grace.
    Ошибки агента best-effort: не блокируем UX, scheduler sync подберёт.

    Используется в try_renew_from_grace, handle_admin_sub_extend и Lava
    recurring renewal — единственный источник правды для unthrottle-логики.
    """
    from services.database import (
        get_configs_for_subscription, get_server_by_id, update_config_data,
    )
    from services.plans import vless_service_for_plan

    target_vless_svc = vless_service_for_plan(plan_key)

    async def _one(cfg: dict) -> None:
        server_id = cfg.get("server_id")
        if not server_id:
            return
        server = await get_server_by_id(server_id)
        if not server or not server.get("agent_url"):
            return
        client = client_for_server(server)
        proto = cfg.get("protocol", "")
        peer_id = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
        assigned_ip = cfg.get("assigned_ip") or ""
        try:
            if proto == "awg" and peer_id and assigned_ip:
                await client.unthrottle_peer("awg", peer_id, assigned_ip)
            elif proto in ("vless", "vless-reality") and peer_id:
                normal_added = False
                try:
                    new_peer = await client.add_peer(
                        target_vless_svc, f"u{user_id}_c{cfg['id']}",
                        peer_id=peer_id,
                    )
                    normal_added = True
                    for _svc in ("vless-grace", "vless-base-slow", "vless-max-slow"):
                        await client.remove_peer(_svc, peer_id)
                except VpnctlError:
                    if normal_added:
                        try:
                            await client.remove_peer(target_vless_svc, peer_id)
                        except Exception:
                            pass
                    raise
                if new_peer.config:
                    await update_config_data(cfg["id"], new_peer.config)
        except VpnctlError as e:
            logger.warning(
                "unthrottle_sub_configs cfg #%d: %s", cfg["id"], e, exc_info=True,
            )

    try:
        configs = await get_configs_for_subscription(sub_id)
        await asyncio.gather(*[_one(cfg) for cfg in configs])
    except Exception as e:
        # Даже при ошибке — подписка уже активна в БД; scheduler sync разрулит.
        logger.error("unthrottle_sub_configs sub=%d outer: %s", sub_id, e, exc_info=True)


async def close_dangling_grace_subs_after_upgrade(
    bot: Bot, user_id: int, new_sub_id: int,
) -> int:
    """После успешного provisioning'а новой sub на cross-plan upgrade —
    закрывает все ОСТАЛЬНЫЕ grace-subs юзера. Раньше (EU-F7) их оставляли
    до natural grace_until expire (14 дней overlap), но это:
      - 14 дней Happ балансирует между старым throttle и новым full speed
      - peer_count на агенте раздут (старые пиры висят)
      - `get_active_subscription` логирует warning о 2 active rows

    Безопаснее закрывать сейчас, ПОСЛЕ provisioning'а нового — мы гарантируем
    что у юзера есть рабочий новый VPN до того как тушим старый. Best-effort:
    если revoke упадёт, _sync_vless_active_uuids подберёт ghost-пиры.

    Returns: число закрытых grace-sub'ов.
    """
    from services.database import get_dangling_grace_subs_for_user
    closed = 0
    try:
        dangling = await get_dangling_grace_subs_for_user(user_id, new_sub_id)
    except Exception as e:
        logger.warning("close_dangling_grace_subs: query failed user=%d: %s", user_id, e)
        return 0
    for old_sub in dangling:
        try:
            await _close_dangling_grace(bot, old_sub["id"], old_sub["plan"])
            closed += 1
        except Exception as e:
            logger.warning(
                "close_dangling_grace sub=%d (user=%d, plan=%s) failed: %s — "
                "scheduler reap подберёт по grace_until",
                old_sub["id"], user_id, old_sub.get("plan"), e,
            )
    if closed:
        logger.info(
            "Cross-plan upgrade: closed %d dangling grace sub(s) for user=%d after new sub=%d",
            closed, user_id, new_sub_id,
        )
    return closed


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
    from services.revoke import current_vless_service

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
                        # Conditional decrement (audit 2026-05-23 round-4):
                        #   - 200/204 (peer был и удалён) → counter -1
                        #   - 404 (peer уже не было) → counter не трогаем (был
                        #     декрементирован тем кто реально удалил, например
                        #     `_process_orphan_active_configs` или admin revoke)
                        #   - 5xx/timeout (raise VpnctlError) → counter не
                        #     трогаем, orphan-reaper retry'нет на след. тике
                        # Раньше комментарий обещал "AWG-аналог sync" — его не
                        # существует (только VLESS sync), counter drift'ил при
                        # каждой 404/5xx. Audit нашёл inconsistency с
                        # `revoke_subscription_configs` + orphan-reaper, теперь
                        # все callsites используют один и тот же bool-gate.
                        try:
                            if await cli.remove_peer("awg", peer_name):
                                await update_server_peer_count(server_id, -1)
                        except VpnctlError as e:
                            logger.warning(
                                "remove_peer awg failed in _close_dangling_grace "
                                "(orphan-reaper подберёт next tick): %s", e,
                            )
                    elif protocol in ("vless", "vless-reality") and vless_uuid:
                        # peer мог быть в vless-grace или обычном inbound
                        config_data = cfg.get("config_data") or ""
                        svc = current_vless_service(config_data, plan_key)
                        try:
                            if await cli.remove_peer(svc, vless_uuid):
                                await update_server_peer_count(server_id, -1)
                        except VpnctlError as e:
                            logger.warning(
                                "remove_peer vless failed in _close_dangling_grace "
                                "(_sync_vless_active_uuids подберёт next tick): %s", e,
                            )
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
