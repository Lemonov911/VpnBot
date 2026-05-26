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

# Per-sub lock для bootstrap_vless_for_sub (audit F1): без него два
# параллельных admin grant'а / retry на flaky network могли запустить
# bootstrap дважды для одной sub, оба читали тот же empty_vless список,
# оба генерировали свой slot_uuid → split-brain peers + active_peers drift.
_BOOTSTRAP_LOCKS: dict[int, asyncio.Lock] = {}

# Длина пробного периода. 7 дней (было 3 до бизнес-аудита 25.05.2026).
#
# Почему подняли с 3 до 7: 3 дня — стандарт RU TG-рынка (Матушка дают 3,
# Pink Panther 1), но на cold-аудитории это убивает конверсию. Типовой
# cold-юзер: день 1 ставит → проверяет что работает → выключает Mini App;
# день 2-3 не открывает; день 3 вспоминает «надо что-то делать» — а уже
# expired → «ну ладно, в другой раз» → не возвращается. 7 дней даёт окно
# сформировать привычку перед оплатой. Capybara/ZoogVPN дают 7-10д не зря.
# Trial→paid conversion ожидаемо растёт с 10-15% (3д) до 20-30% (7д).
TRIAL_DAYS = 7
# Для тех, кто пришёл по реферальной ссылке — расширенный триал 10 дней
# (referrer-bonus задокументирован на странице «Друзья»: «другу — больше
# дней чем обычным»). Реферал получает реальное преимущество vs обычный
# новый юзер, что мотивирует кликать по реферальным ссылкам.
TRIAL_DAYS_REFERRED = 10


async def trial_days_for(user_id: int) -> int:
    """Сколько дней триала юзер получит — 3 или 7 (если есть referred_by)."""
    from services.database import get_referred_by
    referrer = await get_referred_by(user_id)
    return TRIAL_DAYS_REFERRED if referrer else TRIAL_DAYS

# Cooldown между триалами. Защищаем от халявы (один и тот же юзер не сидит
# на цикле триалов), но даём вернуться тем, кто попробовал и забыл.
#
# ⚠️ Cooldown считается от created_at (см. can_claim_trial ниже), поэтому
# цикл abuse'а = ровно cooldown дней, НЕ cooldown+trial. При 7-дневном
# триале и cooldown=30 это было бы 12 циклов/год × 7 = 84 free дня на
# alt-аккаунт (ровно тот abuse, которым пугал комментарий referred). Подняли
# до 60 при апгрейде триала 3→7 (бизнес-аудит 25.05): 6 циклов × 7 = 42
# free дня/год — приемлемо.
TRIAL_COOLDOWN_DAYS = 60
# Для referred-юзеров (взяли расширенный 10-day trial) — длиннее окно.
# При cooldown=90: ~4 цикла/год × 10 = 40 free дней/alt — в том же бюджете.
TRIAL_COOLDOWN_DAYS_REFERRED = 90


async def trial_cooldown_days_for(user_id: int) -> int:
    """Сколько дней между триалами — 30 (regular) или 90 (referred)."""
    from services.database import get_referred_by
    referrer = await get_referred_by(user_id)
    return TRIAL_COOLDOWN_DAYS_REFERRED if referrer else TRIAL_COOLDOWN_DAYS

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
        # expires_at на TRIAL_DAYS позже created_at, поэтому при фильтре по
        # expires_at кнопка была бы скрыта лишние TRIAL_DAYS после окончания
        # cooldown.
        #
        # COALESCE(trial_rolled_back, 0) = 0 вместо `status != 'expired'`:
        # после Fix #1 scheduler помечает истёкшие триалы expired, и старый
        # фильтр пропускал бы их → юзер мог взять второй триал сразу после
        # окончания первого. trial_rolled_back маркер ставится ТОЛЬКО при
        # rollback-пути provision_trial (provision упал) — в этом случае
        # cooldown не применяется, юзер может повторить попытку сразу.
        cooldown = await trial_cooldown_days_for(user_id)
        row = await (await db.execute(
            f"""SELECT 1 FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND COALESCE(trial_rolled_back, 0) = 0
                  AND created_at > datetime('now', '-{cooldown} days')
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

    cooldown = await trial_cooldown_days_for(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        # Триал доступен раз в cooldown дней (30 regular / 90 referred).
        # Старые истёкшие триалы > cooldown дней назад не блокируют новый.
        # См. can_claim_trial для объяснения trial_rolled_back маркера —
        # rollback-случай не блокирует cooldown'ом, нормальное истечение
        # (status='expired' без флага) — блокирует.
        existing = await (await db.execute(
            f"""SELECT id FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND COALESCE(trial_rolled_back, 0) = 0
                  AND created_at > datetime('now', '-{cooldown} days')
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
        from urllib.parse import quote as _q
        vless_servers = await get_all_active_servers("vless")
        if not vless_servers:
            raise TrialNoServer()

        # CRITICAL: используем users.vless_uuid (persistent per-user), НЕ
        # генерим свежий uuid4. _resolve_vless_urls динамически строит
        # sub_url из users.vless_uuid; если провижим под другим UUID,
        # клиент шлёт users.vless_uuid → сервер не находит → Reality fail.
        # Это историческая засада: первая версия trial генерила фреш UUID,
        # а handle_vpn_config_activate (line 951-964) уже использует
        # ensure_user_vless_uuid — паритет нарушался, sub_url ломался.
        from services.database import ensure_user_vless_uuid
        slot_uuid = await ensure_user_vless_uuid(user_id)
        vless_provisioned = 0
        for server in vless_servers:
            cfg_id = await create_config_record(sub_id, user_id, protocol="vless",
                                                  server_id=server["id"])
            flag = (server.get("flag") or "").replace(" ", "")
            label = f"trial_{user_id}_{flag or server['id']}"
            peer = None
            try:
                peer = await provision_peer(server, label, "vless-base", peer_id=slot_uuid)
            except VpnctlError as e:
                logger.warning("trial vless provision server=%s slot=%d: %s",
                                server.get("id"), cfg_id, e, exc_info=True)
                # Peer не поднялся — чистим orphan config-запись (status='empty')
                try:
                    from services.database import delete_config_record
                    await delete_config_record(cfg_id)
                except Exception as rb_err:
                    logger.warning("trial vless cleanup cfg #%d: %s", cfg_id, rb_err)
                continue
            # F3: provision успешный — теперь save_peer_to_config + counter.
            # Если они упадут (DB hiccup), peer уже на агенте → ghost.
            # Compensating remove чтобы active_peers не дрейфовал и пир
            # не висел навсегда не привязанный к юзеру.
            try:
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
            except Exception as save_err:
                logger.error("trial vless save/counter cfg #%d (peer=%s) failed: %s",
                              cfg_id, peer.id, save_err, exc_info=True)
                # Compensating: убираем ghost peer + cleanup config row
                try:
                    from services.vpnctl_client import client_for_server
                    await client_for_server(server).remove_peer("vless-base", slot_uuid)
                    logger.info("trial vless: compensating remove ghost peer srv=%s OK",
                                server.get("id"))
                except Exception as cleanup_err:
                    logger.error("trial vless: compensating remove FAILED srv=%s: %s",
                                  server.get("id"), cleanup_err)
                try:
                    from services.database import delete_config_record
                    await delete_config_record(cfg_id)
                except Exception:
                    pass
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


async def bootstrap_vless_for_sub(
    sub_id: int, user_id: int, plan_key: str,
    bot: "object | None" = None,
) -> int:
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
    серверы упали — sub остаётся без VLESS, log + админ-алёрт.

    Idempotency: per-sub asyncio.Lock защищает от двойного запуска при
    race grant retry / parallel admin clicks (audit F1).

    Args:
        bot: опциональный aiogram Bot для админ-алерта о partial fail (F11).
             Не передан → только лог, без TG-уведомления.

    Returns: число успешно прованизированных пиров.
    """
    from urllib.parse import quote as _q

    # F1: per-sub lock — concurrent bootstrap'ы для одной sub серилизуются.
    # После выхода из lock дальнейшие вызовы найдут empty_vless=[] и no-op'нутся
    # (idempotent).
    lock = _BOOTSTRAP_LOCKS.setdefault(sub_id, asyncio.Lock())
    async with lock:
        return await _bootstrap_vless_locked(sub_id, user_id, plan_key, bot, _q)


async def _bootstrap_vless_locked(sub_id, user_id, plan_key, bot, _q) -> int:
    # get_configs_for_subscription() фильтрует status='active' — нам нужны empty.
    # Используем by_protocol-вариант: возвращает все статусы, empty в начале.
    from services.database import get_configs_for_subscription_by_protocol, ensure_user_vless_uuid
    from services.plans import vless_service_for_plan

    vless_servers = await get_all_active_servers("vless")
    if not vless_servers:
        logger.warning("bootstrap_vless: no active VLESS servers, sub=%d skipped", sub_id)
        await _alert_admin_bootstrap_failed(
            bot, sub_id, user_id, plan_key, 0, 0,
            reason="no active VLESS servers",
        )
        return 0

    cfgs = await get_configs_for_subscription_by_protocol(sub_id, "vless")
    empty_vless = [c for c in cfgs if c["status"] == "empty"]
    if not empty_vless:
        # Уже бутстраплено ранее (idempotency: повторный вызов = no-op)
        # ИЛИ план без VLESS-слотов вообще (vpn_start) — не наша проблема.
        logger.info("bootstrap_vless: sub=%d has no empty VLESS slots — skip", sub_id)
        return 0

    # Tier по плану: vpn_max → vless-max, vpn_base → vless-base, etc.
    # Без этого все grant'ы шли бы на base (медленный inbound), а юзер платил
    # за max — UX-несоответствие.
    tier_svc = vless_service_for_plan(plan_key)

    # CRITICAL (был баг 22.05): используем users.vless_uuid, не uuid4().
    # _resolve_vless_urls строит sub_url из users.vless_uuid; провижим
    # peer'ов под другим UUID = клиент шлёт users.vless_uuid, сервер не
    # знает этот UUID → Reality handshake fail → EOF в Happ.
    slot_uuid = await ensure_user_vless_uuid(user_id)
    provisioned = 0
    target_count = min(len(vless_servers), len(empty_vless))
    for i, server in enumerate(vless_servers):
        if i >= len(empty_vless):
            break  # серверов больше чем плановых слотов — оставляем хвост (rare)
        cfg = empty_vless[i]
        flag = (server.get("flag") or "").replace(" ", "")
        label = f"grant_{user_id}_{flag or server['id']}"
        peer = None
        try:
            peer = await provision_peer(server, label, tier_svc, peer_id=slot_uuid)
        except VpnctlError as e:
            logger.warning("bootstrap_vless provision cfg=%d server=%s: %s",
                            cfg["id"], server.get("id"), e, exc_info=True)
            continue
        # F3-mirror: provision успешный — теперь save+counter. Если они упадут,
        # peer на сервере останется ghost-ом → compensating remove.
        try:
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
        except Exception as e:
            logger.error("bootstrap_vless save/counter cfg=%d (provisioned peer=%s) failed: %s",
                          cfg["id"], peer.id, e, exc_info=True)
            # Compensating remove — иначе ghost peer на агенте без записи в БД,
            # юзер никогда не сможет его удалить через Mini App.
            try:
                from services.vpnctl_client import client_for_server
                cli = client_for_server(server)
                await cli.remove_peer(tier_svc, slot_uuid)
                logger.info("bootstrap_vless: compensating remove ghost peer on srv=%s OK",
                            server.get("id"))
            except Exception as cleanup_err:
                logger.error("bootstrap_vless: compensating remove FAILED srv=%s: %s — "
                              "ghost peer останется до scheduler-sync",
                              server.get("id"), cleanup_err)

    logger.info("bootstrap_vless: sub=%d user=%d provisioned %d/%d VLESS peers (tier=%s)",
                sub_id, user_id, provisioned, target_count, tier_svc)

    # Collapse excess empty VLESS-rows. Историческое legacy: admin_grant и
    # _deliver_vpn создавали `plan.vless_slots` пустых rows (например 5 для
    # vpn_max), но _resolve_vless_urls строит sub_url из ОДНОГО `users.vless_uuid`
    # × N active VLESS-серверов. Если slots > servers, лишние пустые rows
    # никогда не активируются (юзеру в Mini App «VLESS slot 3/5» без сервера),
    # а sub-URL и так раздаёт все локации. Удаляем шум.
    if len(empty_vless) > len(vless_servers):
        from services.database import delete_config_record
        # Перечитываем актуальный список — provisioned-rows теперь status='active',
        # фильтр empty оставит только то что мы НЕ заполнили.
        remaining = await get_configs_for_subscription_by_protocol(sub_id, "vless")
        for cfg in (c for c in remaining if c["status"] == "empty"):
            try:
                await delete_config_record(cfg["id"])
                logger.info("bootstrap_vless: deleted excess empty VLESS row cfg=#%d "
                             "(plan slots > N active servers)", cfg["id"])
            except Exception as e:
                logger.warning("bootstrap_vless: delete excess cfg=#%d failed: %s",
                                cfg["id"], e)

    # F11: админ-алёрт если provisioned < target. Любой partial fail для
    # paid grant'а — потеря локации в Happ subscription, юзер этого не увидит,
    # надо чтобы админ сам нашёл и руками починил.
    if provisioned < target_count:
        await _alert_admin_bootstrap_failed(
            bot, sub_id, user_id, plan_key, provisioned, target_count,
            reason="provision_peer / save failures на части серверов",
        )
    return provisioned


async def _alert_admin_bootstrap_failed(
    bot, sub_id: int, user_id: int, plan_key: str,
    provisioned: int, target: int, reason: str,
) -> None:
    """TG-алёрт админу: bootstrap_vless_for_sub отработал с partial/full fail.

    Без алёрта админ не узнаёт что юзер получил grant'ed sub без VLESS —
    в Mini App у юзера VLESS просто отсутствует (фильтр s.protocol !== 'vless'
    скрывает empty slots), он не репортит «нет VLESS» а просто молча сидит.
    """
    if bot is None:
        return
    try:
        from config import ADMIN_ID
        from html import escape as _esc
        if not ADMIN_ID:
            return
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ <b>VLESS bootstrap partial fail</b>\n\n"
            f"sub <code>#{sub_id}</code> (user <code>{user_id}</code>, plan {_esc(plan_key)})\n"
            f"Provisioned: <b>{provisioned}/{target}</b>\n"
            f"Reason: {_esc(reason)}\n\n"
            f"Юзер видит sub_url с меньшим числом локаций чем должен. "
            f"Проверь логи и при нужде повтори grant или manual provision.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("bootstrap_vless admin alert failed: %s", e)
