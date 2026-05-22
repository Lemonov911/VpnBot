"""
Бесплатный пробный период: 3 дня. Даёт сразу 2 конфига — VLESS-base и AmneziaWG —
чтобы юзер мог попробовать главный продукт (AWG, обход МТС DPI) с первой минуты.

Один вызов — provision_trial(user_id) — создаёт подписку, оба пира и
возвращает subscription URL + AWG-конфиг. Зовётся из двух мест:
  - /trial команда бота (handlers/admin.py:cmd_trial)
  - POST /api/vpn/trial Mini App'а (services/webapp_api.py)
"""

import asyncio
import logging
from datetime import datetime, timedelta

import aiosqlite

from services.database import (
    DB_PATH,
    create_config_record,
    create_subscription,
    get_all_active_servers,
    get_best_server,
    get_or_create_sub_token,
    has_active_subscription,
    save_peer_to_config,
    update_server_peer_count,
)
from services.vpnctl_client import VpnctlError, provision_peer

logger = logging.getLogger(__name__)

# Per-user lock против race condition при concurrent /trial claim.
# Без него N параллельных POST /api/vpn/trial/claim видят пустой результат
# eligibility-check (оба до commit'а), и юзер получает N триал-конфигов
# вместо одного. У нас один процесс → asyncio.Lock достаточно.
# Не чистим dict при completion — на 10k юзеров это ~1 MB, не drama.
_TRIAL_LOCKS: dict[int, asyncio.Lock] = {}

# Длина пробного периода. 3 дня — стандарт RU TG-VPN-рынка (Матушка дают 3,
# Pink Panther 1). Достаточно чтобы юзер реально потестил скорость и обход
# DPI на своём операторе.
TRIAL_DAYS = 3
# Для тех, кто пришёл по реферальной ссылке — расширенный триал 7 дней
# (referrer-bonus задокументирован на странице «Друзья»: «другу — 7 дней
# вместо обычных 3»). Реферал получает реальное преимущество vs
# обычный новый юзер, что мотивирует кликать по реферальным ссылкам.
TRIAL_DAYS_REFERRED = 7


async def trial_days_for(user_id: int) -> int:
    """Сколько дней триала юзер получит — 3 или 7 (если есть referred_by)."""
    from services.database import get_referred_by
    referrer = await get_referred_by(user_id)
    return TRIAL_DAYS_REFERRED if referrer else TRIAL_DAYS

# Cooldown между триалами. Один раз в месяц на аккаунт — не «никогда».
# С одной стороны защищаем от халявы (один и тот же юзер не сидит на цикле
# триалов), с другой — даём вернуться людям, которые попробовали и забыли.
TRIAL_COOLDOWN_DAYS = 30

TRIAL_PLAN = "vpn_trial"


class TrialError(Exception):
    """Базовый класс ошибок trial-провижининга."""


class TrialAlreadyClaimed(TrialError):
    """Юзер уже использовал trial раньше."""


class TrialBlockedByActiveSub(TrialError):
    """У юзера активная платная подписка — trial не нужен."""


class TrialNoServer(TrialError):
    """Нет доступного VLESS-сервера прямо сейчас."""


async def can_claim_trial(user_id: int) -> bool:
    """True если у юзера нет активной/grace-подписки и нет недавнего trial'а.

    Логика (два независимых запроса):
    1) Активная или grace-подписка любого типа (включая trial) → False.
       Юзер не может взять новый trial пока старый ещё работает.
    2) Любой trial с expires_at в пределах cooldown → False.
       Юзер не может брать второй trial раньше чем через TRIAL_COOLDOWN_DAYS
       после реального истечения (не от created_at!).
    """
    if await has_active_subscription(user_id):
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        # Активная trial-sub в любом статусе active/grace — блокирует
        row = await (await db.execute(
            """SELECT 1 FROM subscriptions
               WHERE user_id=? AND plan=? AND status IN ('active','grace')
               LIMIT 1""",
            (user_id, TRIAL_PLAN),
        )).fetchone()
        if row is not None:
            return False
        # Trial был, но created_at в пределах cooldown — блокирует.
        # Используем created_at (как в _provision_trial_locked), не expires_at:
        # expires_at на 3 дня позже created_at, поэтому кнопка была бы скрыта
        # 3 лишних дня после окончания cooldown.
        #
        # COALESCE(trial_rolled_back, 0) = 0 вместо `status != 'expired'`:
        # после Fix #1 scheduler помечает истёкшие триалы expired, и старый
        # фильтр пропускал бы их → юзер мог взять второй триал сразу после
        # окончания первого. trial_rolled_back маркер ставится ТОЛЬКО при
        # rollback-пути provision_trial (provision упал) — в этом случае
        # cooldown не применяется, юзер может повторить попытку сразу.
        row = await (await db.execute(
            f"""SELECT 1 FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND COALESCE(trial_rolled_back, 0) = 0
                  AND created_at > datetime('now', '-{TRIAL_COOLDOWN_DAYS} days')
                LIMIT 1""",
            (user_id, TRIAL_PLAN),
        )).fetchone()
    return row is None


async def provision_trial(user_id: int) -> dict:
    """Создаёт пробную подписку + VLESS peer + AWG peer.

    Returns:
        {
            "sub_id":         int,
            "vless_config_id": int,
            "awg_config_id":   int | None,   # None если AWG сервер недоступен
            "sub_url":        "https://maxvpnesim.com/sub/<token>",
            "awg_config":     str | None,    # raw AWG конфиг (если есть)
            "expires_at":     datetime,
            "duration_days":  3,
        }

    Raises:
        TrialBlockedByActiveSub — у юзера активная платная подписка
        TrialAlreadyClaimed — trial уже был
        TrialNoServer — нет vless-сервера (AWG считается опциональным)
        VpnctlError — провижининг не удался
    """
    # Per-user lock: блокируем concurrent claim'ы того же юзера, иначе
    # eligibility-check + write выполняются не-атомарно и юзер может
    # намолотить N триалов параллельными POST /api/vpn/trial/claim.
    lock = _TRIAL_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        return await _provision_trial_locked(user_id)


async def _provision_trial_locked(user_id: int) -> dict:
    if await has_active_subscription(user_id):
        raise TrialBlockedByActiveSub()

    async with aiosqlite.connect(DB_PATH) as db:
        # Триал доступен раз в TRIAL_COOLDOWN_DAYS дней — не «никогда».
        # Старые истёкшие триалы > 30 дней назад не блокируют новый.
        # См. can_claim_trial для объяснения trial_rolled_back маркера —
        # rollback-случай не блокирует cooldown'ом, нормальное истечение
        # (status='expired' без флага) — блокирует.
        existing = await (await db.execute(
            f"""SELECT id FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND COALESCE(trial_rolled_back, 0) = 0
                  AND created_at > datetime('now', '-{TRIAL_COOLDOWN_DAYS} days')
                LIMIT 1""",
            (user_id, TRIAL_PLAN),
        )).fetchone()
    if existing:
        raise TrialAlreadyClaimed()

    days = await trial_days_for(user_id)
    expires = datetime.utcnow() + timedelta(days=days)
    # Уникальный payment_id с timestamp — `trial_{user_id}` не подходит,
    # т.к. через TRIAL_COOLDOWN_DAYS юзер может взять новый триал, и
    # UNIQUE-constraint на subscriptions.payment_id уронит вставку.
    trial_payment_id = f"trial_{user_id}_{int(datetime.utcnow().timestamp())}"
    sub_id = await create_subscription(user_id, TRIAL_PLAN, trial_payment_id, 0, expires)
    if sub_id is None:
        logger.error("trial create_subscription returned None for user %d (payment_id collision?)", user_id)
        raise TrialError("create_subscription failed")

    # ── compensating-rollback wrapper: если что-то упадёт между create_subscription
    # и реальной выдачей пира, sub'а остаётся orphan в БД → юзер не может ни
    # взять новый триал (cooldown 30 дней по `can_claim_trial`), ни купить
    # платный (`has_active_subscription` блокирует invoice). Лечим компенсацией.
    try:
        # ── 1) VLESS peers (обязательны — главный канал) ──────────────────────
        # Multi-location: один UUID на все активные VLESS-сервера.  Юзер
        # импортирует subscription-URL в Happ и видит дропдаун локаций.
        import uuid as _uuid
        from urllib.parse import quote as _q
        vless_servers = await get_all_active_servers("vless")
        if not vless_servers:
            raise TrialNoServer()

        slot_uuid = str(_uuid.uuid4())
        vless_provisioned = 0
        for server in vless_servers:
            cfg_id = await create_config_record(sub_id, user_id, protocol="vless",
                                                  server_id=server["id"])
            try:
                flag = (server.get("flag") or "").replace(" ", "")
                label = f"trial_{user_id}_{flag or server['id']}"
                peer = await provision_peer(server, label, "vless-base", peer_id=slot_uuid)
                loc = " ".join(filter(None, [
                    (server.get("flag") or "").strip(),
                    (server.get("city") or server.get("name") or "").strip(),
                ])).strip() or f"Server {server['id']}"
                cfg_data = peer.config or ""
                if cfg_data.startswith("vless://"):
                    base = cfg_data.split("#", 1)[0]
                    cfg_data = f"{base}#{_q(loc, safe='')}"
                await save_peer_to_config(
                    cfg_id, server["id"], peer.id, "",
                    cfg_data, label,
                    vless_uuid=slot_uuid,
                )
                await update_server_peer_count(server["id"], +1)
                vless_provisioned += 1
            except VpnctlError as e:
                logger.warning("trial vless server=%s slot=%d: %s",
                                server.get("id"), cfg_id, e, exc_info=True)
                # Удаляем orphan config-запись (status='empty') созданную
                # выше — peer не поднялся, слот не нужен.
                try:
                    from services.database import delete_config_record
                    await delete_config_record(cfg_id)
                except Exception as rb_err:
                    logger.warning("trial vless cleanup cfg #%d: %s", cfg_id, rb_err)
        if vless_provisioned == 0:
            # Ни одна локация не запровижилась → триал не выдан
            raise TrialNoServer()
        # Сохраним id первого активированного config'а как «vless_config_id»
        # для совместимости с downstream-кодом
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id FROM configs
                   WHERE subscription_id=? AND protocol='vless' AND status='active'
                   ORDER BY id LIMIT 1""",
                (sub_id,),
            ) as cur:
                row = await cur.fetchone()
                vless_cfg_id = row["id"] if row else 0
    except (TrialNoServer, VpnctlError, Exception):
        # Rollback: помечаем sub'у expired + ставим trial_rolled_back=1 чтобы
        # не блокировать юзера 30-дневным cooldown'ом — provisioning упал,
        # юзер должен иметь возможность сразу повторить попытку.
        # Используем mark_subscription_trial_rolled_back а не delete —
        # payment_id уникален, history-row нужна для audit/debug.
        try:
            from services.database import mark_subscription_trial_rolled_back
            await mark_subscription_trial_rolled_back(sub_id)
            logger.warning("trial rollback: marked sub #%d rolled-back (user %d)", sub_id, user_id)
        except Exception as rb_err:
            logger.error("trial rollback failed for sub #%d: %s", sub_id, rb_err, exc_info=True)
        raise

    # ── 2) AWG peer (опционален — best-effort, не валит триал) ──────────
    # Главный продукт = AmneziaWG (обход МТС DPI). Триал без AWG занижает
    # восприятие качества: юзер пришёл за AWG, получил VLESS — не то.
    awg_cfg_id: int | None = None
    awg_config: str | None = None
    awg_assigned_ip: str = ""
    try:
        awg_server = await get_best_server("awg")
        if awg_server:
            awg_cfg_id = await create_config_record(sub_id, user_id, protocol="awg")
            awg_peer = await provision_peer(
                awg_server, f"trial_{user_id}_{awg_cfg_id}", "awg"
            )
            awg_assigned_ip = (awg_peer.extra or {}).get("assigned_ip", "")
            await save_peer_to_config(
                awg_cfg_id, awg_server["id"], awg_peer.id, awg_assigned_ip,
                awg_peer.config, f"trial_{user_id}_awg",
            )
            await update_server_peer_count(awg_server["id"], +1)
            awg_config = awg_peer.config
        else:
            logger.warning("trial AWG skipped: no awg server available for user %d", user_id)
    except VpnctlError as e:
        logger.warning("trial AWG provisioning failed for user %d: %s — VLESS-only fallback", user_id, e, exc_info=True)
        # Cleanup: если record создан, но peer не сохранён — удалить orphan slot
        # (иначе trial sub имеет призрачный AWG-slot, который никогда не активируется).
        if awg_cfg_id is not None:
            try:
                from services.database import delete_config_record
                await delete_config_record(awg_cfg_id)
            except Exception as rb_err:
                logger.warning("trial AWG cleanup failed for cfg #%s: %s", awg_cfg_id, rb_err, exc_info=True)
        awg_cfg_id = None
        awg_config = None
    except Exception as e:
        logger.warning("trial AWG unexpected error for user %d: %s", user_id, e, exc_info=True)
        if awg_cfg_id is not None:
            try:
                from services.database import delete_config_record
                await delete_config_record(awg_cfg_id)
            except Exception as rb_err:
                logger.warning("trial AWG cleanup failed for cfg #%s: %s", awg_cfg_id, rb_err, exc_info=True)
        awg_cfg_id = None
        awg_config = None

    sub_token = await get_or_create_sub_token(user_id)

    logger.info(
        "trial provisioned: user_id=%d sub_id=%d vless_cfg=%d awg_cfg=%s expires=%s",
        user_id, sub_id, vless_cfg_id, awg_cfg_id, expires.isoformat(),
    )

    return {
        "sub_id":          sub_id,
        "vless_config_id": vless_cfg_id,
        "awg_config_id":   awg_cfg_id,
        "sub_url":         f"https://maxvpnesim.com/sub/{sub_token}",
        "awg_config":      awg_config,
        "expires_at":      expires,
        "duration_days":   days,
        # Backward-compat: старые callers ждут "config_id"
        "config_id":       vless_cfg_id,
    }


async def bootstrap_vless_for_sub(sub_id: int, user_id: int, plan_key: str) -> int:
    """Активирует VLESS на всех active VLESS-серверах для свежей grant-sub.

    Зачем: admin-grant создаёт только empty слоты (см. admin_grant_subscription
    в database.py). У платных flow'ов VLESS bootstrap'ится через successful_payment-
    handler'ы (cryptobot/oxapay/lava/stars), у trial — через provision_trial.
    У gift-sub этого шага не было — юзер получал sub_url=None в /api/vpn/subscription
    (т.к. _sub_url_for требует ≥1 active VLESS-конфиг), а UI per-slot VLESS не
    показывает (filter s.protocol !== 'vless'). Результат — VLESS физически
    купленный, но недоступный в Mini App.

    Re-uses первые N empty VLESS-слотов этой sub (по числу active VLESS-серверов).
    Best-effort: ошибка одного сервера не останавливает остальные. Если все
    серверы упали — sub остаётся без VLESS, log + админ-алёрт через caller.

    Returns: число успешно прованизированных пиров.
    """
    import uuid as _uuid
    from urllib.parse import quote as _q

    from services.database import get_configs_for_subscription
    from services.plans import vless_service_for_plan

    vless_servers = await get_all_active_servers("vless")
    if not vless_servers:
        logger.warning("bootstrap_vless: no active VLESS servers, sub=%d skipped", sub_id)
        return 0

    cfgs = await get_configs_for_subscription(sub_id)
    empty_vless = [c for c in cfgs if c["protocol"] == "vless" and c["status"] == "empty"]
    if not empty_vless:
        # Уже бутстраплено ранее (idempotency: повторный вызов = no-op)
        # ИЛИ план без VLESS-слотов вообще (vpn_start) — не наша проблема.
        logger.info("bootstrap_vless: sub=%d has no empty VLESS slots — skip", sub_id)
        return 0

    # Tier по плану: vpn_max → vless-max, vpn_base → vless-base, etc.
    # Без этого все grant'ы шли бы на base (медленный inbound), а юзер платил
    # за max — UX-несоответствие.
    tier_svc = vless_service_for_plan(plan_key)

    # Один UUID на все серверы — Happ subscription с multi-location.
    slot_uuid = str(_uuid.uuid4())
    provisioned = 0
    for i, server in enumerate(vless_servers):
        if i >= len(empty_vless):
            break  # серверов больше чем плановых слотов — оставляем хвост (rare)
        cfg = empty_vless[i]
        try:
            flag = (server.get("flag") or "").replace(" ", "")
            label = f"grant_{user_id}_{flag or server['id']}"
            peer = await provision_peer(server, label, tier_svc, peer_id=slot_uuid)
            loc = " ".join(filter(None, [
                (server.get("flag") or "").strip(),
                (server.get("city") or server.get("name") or "").strip(),
            ])).strip() or f"Server {server['id']}"
            cfg_data = peer.config or ""
            if cfg_data.startswith("vless://"):
                base = cfg_data.split("#", 1)[0]
                cfg_data = f"{base}#{_q(loc, safe='')}"
            await save_peer_to_config(
                cfg["id"], server["id"], peer.id, "",
                cfg_data, label,
                vless_uuid=slot_uuid,
            )
            await update_server_peer_count(server["id"], +1)
            provisioned += 1
        except VpnctlError as e:
            logger.warning("bootstrap_vless cfg=%d server=%s: %s",
                            cfg["id"], server.get("id"), e, exc_info=True)
        except Exception as e:
            logger.error("bootstrap_vless unexpected cfg=%d: %s",
                          cfg["id"], e, exc_info=True)

    logger.info("bootstrap_vless: sub=%d user=%d provisioned %d/%d VLESS peers (tier=%s)",
                sub_id, user_id, provisioned, len(vless_servers), tier_svc)
    return provisioned
