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
    get_configs_for_subscription, get_server_by_id,
    reset_config_slot, update_server_peer_count,
)
from services.vpnctl_client import client_for_server
from services.plans import vless_service_for_plan

logger = logging.getLogger(__name__)


def current_vless_service(config_data: str, plan_key: str) -> str:
    """Определяет текущий vpnctl-сервис VLESS-конфига по порту в config_data.

    Слои tier: vless-base / vless-max (normal) + *-slow (throttled) + vless-grace.
    Если URL содержит маркер throttle-порта — возвращаем slow-вариант,
    иначе — base/max по plan_key. Используется в revoke чтобы попасть в правильный
    inbound (важно: после grace-tier-move пир сидит в vless-grace, revoke по
    vless-base его не достанет).
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
                        await client.remove_peer("awg", peer_name)
                        await update_server_peer_count(server_id, -1)
                        revoked += 1
                    elif protocol in ("vless", "vless-reality"):
                        if vless_uuid:
                            config_data = cfg.get("config_data") or ""
                            svc = current_vless_service(config_data, plan_key)
                            await client.remove_peer(svc, vless_uuid)
                            await update_server_peer_count(server_id, -1)
                            revoked += 1
                except Exception as e:
                    failed += 1
                    logger.warning("%s cfg #%d (%s on srv=%s): %s",
                                   log_prefix, cfg_id, protocol, server_id, e,
                                   exc_info=True)

        await reset_config_slot(cfg_id)

    return revoked, failed
