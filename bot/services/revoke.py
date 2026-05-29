"""Helper для revoke всех configs принадлежащих подписке.

Используется:
- handle_admin_sub_refund (webapp_api.py) — при admin-refund
- _process_grace_expired_subscriptions (scheduler.py) — grace → expired
- scripts/recover_leaked_subs.py — backfill для уже-протёкших подписок

Идемпотентность: если peer уже удалён на агенте — игнорируем ошибку,
БД всё равно сбрасывается в чистое состояние. Orphan на сервере
подчищается AWG/VLESS-sync'ом (TODO для AWG, для VLESS работает через
_sync_vless_active_uuids).

Audit-aware: ошибки на агенте логируем, но не аборт-ом — БД должна
дойти до consistent state даже при flaky-агенте.
"""
import logging
from services.database import (
    get_configs_for_subscription, get_configs_for_subscription_by_protocol,
    get_server_by_id, reset_config_slot, delete_config_record,
    update_server_peer_count,
)
from services.vpnctl_client import client_for_server
from services.plans import vless_service_for_plan, VPN_PLANS

logger = logging.getLogger(__name__)


def current_vless_service(config_data: str, plan_key: str) -> str:
    """Определяет текущий vpnctl-сервис VLESS-конфига по порту в config_data.

    Слои tier: vless-base / vless-max (normal) + *-slow (throttled) + vless-grace.
    Если URL содержит маркер throttle-порта — возвращаем slow-вариант,
    иначе — base/max по plan_key. Используется в revoke чтобы попасть в правильный
    inbound (важно: после grace-tier-move пир сидит в vless-grace, revoke по
    vless-base его не достанет).

    vpn_trial: после 443-консолидации (28.05) триал провижинится в тот же
    сервис что и обычные планы (trial.py → vless_service_for_plan → vless-max).
    Раньше тут был спец-кейс `return "vless-base"` под хардкод в trial.py — но
    trial.py тоже переведён на vless_service_for_plan, спец-кейс убран, чтобы
    provision и revoke не разъезжались (mismatch → 404 → orphan peer + counter
    drift). Port-маркеры (slow/grace) проверяются ПЕРЕД планом — throttled/grace
    триал-пиры всё равно найдутся в своём инбаунде.
    """
    if ":9448" in config_data:
        return "vless-max-slow"
    if ":9443" in config_data:
        return "vless-base-slow"
    if ":9453" in config_data:
        return "vless-grace"
    return vless_service_for_plan(plan_key)


async def revoke_subscription_configs(
    sub_id: int,
    plan_key: str,
    *,
    log_prefix: str = "revoke",
) -> tuple[int, int]:
    """Revoke peers всех active configs у sub_id.

    Возвращает (revoked, failed). Continue-on-error: даже если remove_peer
    на агенте упал — БД всё равно reset'ится. Иначе при flaky-агенте
    остаются split-brain row с status=active и юзер сохраняет доступ.

    Замечание для VLESS multi-server: после refactor `bf568ca` один slot
    может иметь только одну config-row (с server_id из неё, остальные
    локации динамически из servers), но в зависимости от истории есть
    pre-refactor sub'ы где config-row на каждый сервер.  Этот helper
    обрабатывает оба сценария: loop по configs, revoke каждой row.
    Multi-server VLESS coverage для **одной row, представляющей slot
    на N серверах** — отдельная задача (КРИТ #2 в audit).
    """
    configs = await get_configs_for_subscription(sub_id)
    revoked = 0
    failed = 0

    for cfg in configs:
        cfg_id = cfg["id"]
        server_id = cfg.get("server_id")
        protocol = cfg.get("protocol", "")
        peer_name = cfg.get("peer_name") or ""
        assigned_ip = cfg.get("assigned_ip") or ""
        vless_uuid = cfg.get("vless_uuid") or ""

        if server_id:
            server = await get_server_by_id(server_id)
            if server and server.get("agent_url"):
                try:
                    client = client_for_server(server)
                    if protocol == "awg":
                        # unthrottle перед remove — иначе при последующем
                        # add_peer с тем же IP остаётся старая tc-rule.
                        if assigned_ip and peer_name:
                            try:
                                await client.unthrottle_peer("awg", peer_name, assigned_ip)
                            except Exception as ue:
                                logger.debug("%s cfg #%d unthrottle skipped: %s",
                                             log_prefix, cfg_id, ue)
                        # Conditional decrement (audit 2026-05-23 round-3): если peer
                        # уже удалён (404 от агента) — counter уже декрементирован,
                        # повтор сломает счётчик. См. также remove_peer bool-return.
                        if await client.remove_peer("awg", peer_name):
                            await update_server_peer_count(server_id, -1)
                        revoked += 1
                    elif protocol in ("vless", "vless-reality"):
                        if vless_uuid:
                            config_data = cfg.get("config_data") or ""
                            svc = current_vless_service(config_data, plan_key)
                            if await client.remove_peer(svc, vless_uuid):
                                await update_server_peer_count(server_id, -1)
                            revoked += 1
                except Exception as e:
                    failed += 1
                    logger.warning("%s cfg #%d (%s on srv=%s): %s",
                                   log_prefix, cfg_id, protocol, server_id, e,
                                   exc_info=True)

        await reset_config_slot(cfg_id)

    return revoked, failed


async def revoke_excess_configs_on_downgrade(
    sub_id: int,
    old_plan_key: str,
    new_plan_key: str,
    *,
    log_prefix: str = "downgrade",
) -> tuple[int, int]:
    """Отзывает configs которые превышают slot-count нового плана.

    Вызывается из scheduler'а после `apply_pending_plan_change` (когда юзер
    запланировал downgrade `vpn_max → vpn_base` и подписка истекла). Без
    этого helper'а лишние слоты остаются status='active' навсегда — юзер
    платит за `vpn_base` (1 VLESS), но продолжает пользоваться 5-ю
    VLESS-пирами с прошлого `vpn_max`. Это до scheduler'а проще: вызвать
    ПЕРЕД grace transition (throttle сработает только на оставшиеся слоты).

    Стратегия для каждого протокола (awg/vless/wg):
      old_count = old_plan[f"{proto}_slots"]
      new_count = new_plan[f"{proto}_slots"]
      if new < old: revoke (old - new) configs, empty-first, oldest-first

    VLESS multi-location: после refactor bf568ca один slot = одна config-row
    (multi-server replication обрабатывается агентом). Если есть legacy
    pre-refactor rows где slot = N rows (one per server) — каждая row
    считается отдельным слотом; это overshoot revocation, но безопаснее
    leak'а.

    Returns: (revoked, failed). revoked включает и empty-slot deletions
    (без обращения к агенту), и active-peer revocations.
    """
    old_plan = VPN_PLANS.get(old_plan_key) or {}
    new_plan = VPN_PLANS.get(new_plan_key) or {}
    if not old_plan or not new_plan:
        logger.warning(
            "%s: unknown plan keys old=%r new=%r — skip excess-revoke",
            log_prefix, old_plan_key, new_plan_key,
        )
        return 0, 0

    revoked = 0
    failed = 0

    for proto in ("awg", "vless", "wg"):
        old_count = int(old_plan.get(f"{proto}_slots", 0) or 0)
        new_count = int(new_plan.get(f"{proto}_slots", 0) or 0)
        if new_count >= old_count:
            continue
        excess = old_count - new_count

        # configs упорядочены: empty первыми (cheap), потом oldest active.
        configs = await get_configs_for_subscription_by_protocol(sub_id, proto)
        if not configs:
            continue

        # Берём первые `excess` — это будет mix из empty (бесплатно убрать)
        # и active (нужно revoke peer на сервере).
        targets = configs[:excess]

        for cfg in targets:
            cfg_id = cfg["id"]
            status = cfg.get("status") or ""
            if status == "empty":
                # Пустой слот — просто удаляем row, агент трогать не нужно.
                try:
                    await delete_config_record(cfg_id)
                    revoked += 1
                    logger.info(
                        "%s: empty slot cfg #%d (proto=%s) deleted",
                        log_prefix, cfg_id, proto,
                    )
                except Exception as e:
                    failed += 1
                    logger.warning(
                        "%s: delete empty cfg #%d failed: %s",
                        log_prefix, cfg_id, e, exc_info=True,
                    )
                continue

            # Active slot — revoke peer + reset (re-use логика из
            # revoke_subscription_configs, но в конце не reset_config_slot,
            # а delete_config_record — слот целиком уходит, не сохраняем
            # пустую row для downgraded плана.
            server_id = cfg.get("server_id")
            protocol = cfg.get("protocol", "")
            peer_name = cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            vless_uuid = cfg.get("vless_uuid") or ""

            if server_id:
                server = await get_server_by_id(server_id)
                if server and server.get("agent_url"):
                    try:
                        client = client_for_server(server)
                        if protocol == "awg":
                            if assigned_ip and peer_name:
                                try:
                                    await client.unthrottle_peer(
                                        "awg", peer_name, assigned_ip,
                                    )
                                except Exception as ue:
                                    logger.debug(
                                        "%s cfg #%d unthrottle skipped: %s",
                                        log_prefix, cfg_id, ue,
                                    )
                            # Conditional decrement (audit 2026-05-23 round-3):
                            # remove_peer 404 → counter уже декрементирован,
                            # не повторяем.
                            if await client.remove_peer("awg", peer_name):
                                await update_server_peer_count(server_id, -1)
                            revoked += 1
                        elif protocol in ("vless", "vless-reality"):
                            if vless_uuid:
                                config_data = cfg.get("config_data") or ""
                                # old_plan_key — слот ещё «в старом тарифе»
                                # до downgrade'а; current_vless_service сама
                                # маппит throttle-порты в *-slow / -grace.
                                svc = current_vless_service(
                                    config_data, old_plan_key,
                                )
                                if await client.remove_peer(svc, vless_uuid):
                                    await update_server_peer_count(server_id, -1)
                                revoked += 1
                        elif protocol == "wg":
                            # plain wg ещё не используется, но не оставляем
                            # silent skip — лог + counter.
                            if await client.remove_peer("wg", peer_name):
                                await update_server_peer_count(server_id, -1)
                            revoked += 1
                    except Exception as e:
                        failed += 1
                        logger.warning(
                            "%s cfg #%d (%s on srv=%s): %s",
                            log_prefix, cfg_id, protocol, server_id, e,
                            exc_info=True,
                        )

            # Полностью убираем row — slot больше не часть тарифа.
            try:
                await delete_config_record(cfg_id)
            except Exception as e:
                logger.warning(
                    "%s: delete cfg #%d after revoke failed: %s",
                    log_prefix, cfg_id, e, exc_info=True,
                )

    return revoked, failed
