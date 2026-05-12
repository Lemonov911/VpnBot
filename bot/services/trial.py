"""
Бесплатный пробный период: 3 дня VLESS-base.

Один вызов — provision_trial(user_id) — создаёт подписку, peer и
возвращает subscription URL. Зовётся из двух мест:
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
    """True если у юзера нет активной подписки и последний триал был
    больше TRIAL_COOLDOWN_DAYS назад (или вообще не было).
    Используется для показа/скрытия trial-CTA в UI."""
    if await has_active_subscription(user_id):
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            f"""SELECT 1 FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND created_at > datetime('now', '-{TRIAL_COOLDOWN_DAYS} days')
                LIMIT 1""",
            (user_id, TRIAL_PLAN),
        )).fetchone()
    return row is None


async def provision_trial(user_id: int) -> dict:
    """Создаёт пробную подписку + VLESS peer.

    Returns:
        {
            "sub_id":     int,
            "config_id":  int,
            "sub_url":    "https://maxvpnesim.com/sub/<token>",
            "expires_at": datetime,
            "duration_days": 3,
        }

    Raises:
        TrialBlockedByActiveSub — у юзера активная платная подписка
        TrialAlreadyClaimed — trial уже был
        TrialNoServer — нет vless-сервера
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
        existing = await (await db.execute(
            f"""SELECT id FROM subscriptions
                WHERE user_id=? AND plan=?
                  AND created_at > datetime('now', '-{TRIAL_COOLDOWN_DAYS} days')
                LIMIT 1""",
            (user_id, TRIAL_PLAN),
        )).fetchone()
    if existing:
        raise TrialAlreadyClaimed()

    expires = datetime.utcnow() + timedelta(days=TRIAL_DAYS)
    sub_id = await create_subscription(user_id, TRIAL_PLAN, f"trial_{user_id}", 0, expires)
    config_id = await create_config_record(sub_id, user_id, protocol="vless")

    server = await get_best_server("vless")
    if not server:
        raise TrialNoServer()

    peer = await provision_peer(server, f"trial_{user_id}_{config_id}", "vless-base")

    await save_peer_to_config(
        config_id, server["id"], peer.id, "", peer.config, f"trial_{user_id}"
    )
    await update_server_peer_count(server["id"], +1)
    sub_token = await get_or_create_sub_token(user_id)

    logger.info("trial provisioned: user_id=%d sub_id=%d expires=%s",
                user_id, sub_id, expires.isoformat())

    return {
        "sub_id":        sub_id,
        "config_id":     config_id,
        "sub_url":       f"https://maxvpnesim.com/sub/{sub_token}",
        "expires_at":    expires,
        "duration_days": TRIAL_DAYS,
    }
