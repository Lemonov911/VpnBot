"""
Periodic health probe для VPN-серверов.

Раз в минуту scheduler зовёт `probe_all_servers()`:
  1. пингует /health у каждого `is_active=1` сервера
  2. пишет результат в `server_health_log`
  3. отслеживает переходы up↔down и пишет в `incidents`

Aggregate-функции `uptime_*` возвращают % uptime за окно для UI.
"""

import asyncio
import logging
import time
from typing import Literal

import aiosqlite

from services.database import DB_PATH
from services.vpnctl_client import VpnctlError, client_for_server

logger = logging.getLogger(__name__)

# Сколько ждать /health перед тем как пометить down.
PROBE_TIMEOUT_S = 4.0

# Авто-деактивация: сколько подряд проб down должно быть чтобы выключить
# сервер. При интервале пробит 60с → 10 минут consecutive downtime.
AUTO_DEACTIVATE_AFTER_DOWN = 10

# Авто-реактивация: сколько подряд проб up чтобы вернуть deactivated-сервер
# назад в is_active=1. Анти-flapping: 5 минут стабильности.
AUTO_REACTIVATE_AFTER_UP = 5


Status = Literal["up", "down", "unknown"]


async def _probe_one(server: dict) -> tuple[Status, int | None, str | None]:
    """Возвращает (status, latency_ms, error)."""
    if not server.get("agent_url") or not server.get("agent_token"):
        return "unknown", None, "no agent_url/agent_token"
    client = client_for_server(server)
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(client.health(), timeout=PROBE_TIMEOUT_S)
        return "up", int((time.perf_counter() - t0) * 1000), None
    except asyncio.TimeoutError:
        return "down", None, "timeout"
    except VpnctlError as e:
        return "down", None, str(e)[:200]
    except Exception as e:
        return "down", None, f"{type(e).__name__}: {e}"[:200]


async def _record_probe(db: aiosqlite.Connection, server_id: int,
                         status: Status, latency_ms: int | None, error: str | None):
    """Пишет результат пробы. Если статус сменился — обновляет incident."""
    await db.execute(
        "INSERT INTO server_health_log (server_id, status, latency_ms, error) VALUES (?, ?, ?, ?)",
        (server_id, status, latency_ms, error),
    )

    # Determine previous status by looking at last log row before this one.
    async with db.execute(
        """SELECT status FROM server_health_log
           WHERE server_id=? AND id < (SELECT MAX(id) FROM server_health_log WHERE server_id=?)
           ORDER BY id DESC LIMIT 1""",
        (server_id, server_id),
    ) as cur:
        row = await cur.fetchone()
        prev_status = row[0] if row else None

    # Track 'down' incidents (treat 'unknown' as a soft state — не открывает incident)
    if status == "down" and prev_status != "down":
        # Открываем новый incident, если ещё нет открытого
        async with db.execute(
            "SELECT id FROM incidents WHERE server_id=? AND resolved_at IS NULL LIMIT 1",
            (server_id,),
        ) as cur:
            existing = await cur.fetchone()
        if not existing:
            await db.execute(
                "INSERT INTO incidents (server_id) VALUES (?)",
                (server_id,),
            )
            logger.warning("health: server %d DOWN (incident opened)", server_id)
    elif status == "up" and prev_status == "down":
        # Закрываем последний открытый incident
        async with db.execute(
            "SELECT id, started_at FROM incidents WHERE server_id=? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1",
            (server_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            inc_id = row[0]
            await db.execute(
                """UPDATE incidents
                   SET resolved_at = CURRENT_TIMESTAMP,
                       duration_sec = CAST((julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400 AS INTEGER)
                   WHERE id = ?""",
                (inc_id,),
            )
            logger.info("health: server %d UP (incident #%d closed)", server_id, inc_id)


async def _consecutive_status_count(db: aiosqlite.Connection,
                                     server_id: int, status: Status,
                                     limit: int) -> int:
    """Сколько последних подряд проб имели данный статус (max `limit`).
    Используется для auto-deactivate/reactivate.
    """
    async with db.execute(
        """SELECT status FROM server_health_log
           WHERE server_id=? ORDER BY id DESC LIMIT ?""",
        (server_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    count = 0
    for row in rows:
        if row[0] == status:
            count += 1
        else:
            break
    return count


async def _maybe_deactivate(db: aiosqlite.Connection, server: dict, bot) -> bool:
    """Если сервер `is_active=1` и последние AUTO_DEACTIVATE_AFTER_DOWN проб
    подряд = down → деактивирует и шлёт alert админу. Возвращает True если
    действительно деактивировали."""
    if not server.get("is_active"):
        return False
    consec_down = await _consecutive_status_count(
        db, server["id"], "down", AUTO_DEACTIVATE_AFTER_DOWN,
    )
    if consec_down < AUTO_DEACTIVATE_AFTER_DOWN:
        return False
    await db.execute(
        "UPDATE servers SET is_active=0 WHERE id=?", (server["id"],),
    )
    logger.warning(
        "health: AUTO-DEACTIVATED server #%d %s (%d consecutive down probes)",
        server["id"], server.get("name", ""), consec_down,
    )
    if bot:
        try:
            from config import ADMIN_ID
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ <b>Server auto-deactivated</b>\n\n"
                    f"{server.get('flag', '🌍')} <b>{server.get('name', '?')}</b> "
                    f"({server.get('host', '?')}) — {consec_down} проб подряд down "
                    f"(~{consec_down} мин).\n\n"
                    f"Новые пиры на этот сервер не пойдут. Реактивация авто после "
                    f"{AUTO_REACTIVATE_AFTER_UP} проб up подряд, или вручную "
                    f"<code>UPDATE servers SET is_active=1 WHERE id={server['id']}</code>.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning("health: failed to notify admin about deactivation: %s", e, exc_info=True)
    return True


async def _maybe_reactivate(db: aiosqlite.Connection, server: dict, bot) -> bool:
    """Если сервер `is_active=0` и последние AUTO_REACTIVATE_AFTER_UP проб
    подряд = up → реактивирует и шлёт alert. Возвращает True если реактивировали."""
    if server.get("is_active"):
        return False
    consec_up = await _consecutive_status_count(
        db, server["id"], "up", AUTO_REACTIVATE_AFTER_UP,
    )
    if consec_up < AUTO_REACTIVATE_AFTER_UP:
        return False
    await db.execute(
        "UPDATE servers SET is_active=1 WHERE id=?", (server["id"],),
    )
    logger.info(
        "health: AUTO-REACTIVATED server #%d %s (%d consecutive up probes)",
        server["id"], server.get("name", ""), consec_up,
    )
    if bot:
        try:
            from config import ADMIN_ID
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"✅ <b>Server auto-reactivated</b>\n\n"
                    f"{server.get('flag', '🌍')} <b>{server.get('name', '?')}</b> "
                    f"({server.get('host', '?')}) — {consec_up} проб up подряд, вернули в строй.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning("health: failed to notify admin about reactivation: %s", e, exc_info=True)
    return True


async def probe_all_servers(bot=None):
    """Главный тик: пробит ВСЕ серверы (включая is_active=0 для возможности
    auto-reactivate), пишет результаты, авто-(де)активирует по N consecutive
    проб.

    bot: aiogram Bot для alert'ов админу. Если None — alert'ы не уходят.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # ВКЛЮЧАЕМ is_active=0 серверы тоже — иначе reactivate невозможна
        async with db.execute(
            "SELECT * FROM servers ORDER BY id"
        ) as cur:
            servers = [dict(r) for r in await cur.fetchall()]

        if not servers:
            return

        results = await asyncio.gather(*[_probe_one(s) for s in servers])
        for server, (status, latency, error) in zip(servers, results):
            try:
                await _record_probe(db, server["id"], status, latency, error)
            except Exception as e:
                logger.warning("health: failed to record probe for server %d: %s",
                               server["id"], e, exc_info=True)

        # Авто-деактивация/реактивация после записи проб.
        for server, (status, _, _) in zip(servers, results):
            try:
                if status == "down":
                    await _maybe_deactivate(db, server, bot)
                elif status == "up":
                    await _maybe_reactivate(db, server, bot)
            except Exception as e:
                logger.warning("health: auto-(de)activate error for server %d: %s",
                               server["id"], e, exc_info=True)
        await db.commit()


async def cleanup_old_logs(keep_days: int = 31):
    """Чистит probes старше keep_days. Зовётся раз в день."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"DELETE FROM server_health_log WHERE checked_at < datetime('now', '-{keep_days} days')"
        )
        await db.commit()


# ── Aggregates для UI ────────────────────────────────────────────────────────

async def uptime_summary(server_id: int) -> dict:
    """Возвращает uptime % для одного сервера за 24h / 7d / 30d."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = {}
        for label, window in (("24h", "-1 days"), ("7d", "-7 days"), ("30d", "-30 days")):
            async with db.execute(
                f"""SELECT
                      COUNT(*) FILTER (WHERE status='up')   as up,
                      COUNT(*) FILTER (WHERE status='down') as down,
                      COUNT(*)                              as total
                    FROM server_health_log
                    WHERE server_id=? AND checked_at > datetime('now', '{window}')""",
                (server_id,),
            ) as cur:
                row = await cur.fetchone()
            up, down, total = row
            denom = (up or 0) + (down or 0)  # ignore 'unknown'
            pct = round(100.0 * (up or 0) / denom, 2) if denom else None
            result[label] = {"pct": pct, "samples": denom, "total": total}
        return result


async def last_24h_strip(server_id: int, buckets: int = 24) -> list[str]:
    """Возвращает массив из 24 элементов — статус по часам за последние 24 часа.
    'up' если в этом часе все пробы up, 'down' если хоть одна down, 'unknown' если
    проб не было.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT
                  CAST((julianday('now') - julianday(checked_at)) * 24 AS INTEGER) as hours_ago,
                  status,
                  COUNT(*) as n
               FROM server_health_log
               WHERE server_id=? AND checked_at > datetime('now', '-24 hours')
               GROUP BY hours_ago, status""",
            (server_id,),
        ) as cur:
            rows = await cur.fetchall()

    # Map hours_ago → status
    by_hour: dict[int, dict[str, int]] = {}
    for hours_ago, status, n in rows:
        by_hour.setdefault(hours_ago, {})[status] = n

    strip: list[str] = []
    for h in range(buckets):
        bucket_idx = buckets - 1 - h  # newest on the right
        counts = by_hour.get(bucket_idx, {})
        if counts.get("down", 0) > 0:
            strip.append("down")
        elif counts.get("up", 0) > 0:
            strip.append("up")
        else:
            strip.append("unknown")
    return strip


async def last_30d_strip(server_id: int, buckets: int = 30) -> list[str]:
    """Возвращает массив из 30 элементов — статус по дням за последние 30 дней.
    'up' если в этом дне ≥99% проб up, 'down' если ≥5% проб down, 'partial' если
    что-то посередине, 'unknown' если проб не было.

    Полезен для long-term uptime тренда: видно когда были инциденты неделю назад.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT
                  CAST(julianday('now') - julianday(checked_at) AS INTEGER) as days_ago,
                  status,
                  COUNT(*) as n
               FROM server_health_log
               WHERE server_id=? AND checked_at > datetime('now', '-30 days')
               GROUP BY days_ago, status""",
            (server_id,),
        ) as cur:
            rows = await cur.fetchall()

    by_day: dict[int, dict[str, int]] = {}
    for days_ago, status, n in rows:
        by_day.setdefault(days_ago, {})[status] = n

    strip: list[str] = []
    for d in range(buckets):
        bucket_idx = buckets - 1 - d  # newest on the right
        counts = by_day.get(bucket_idx, {})
        up = counts.get("up", 0)
        down = counts.get("down", 0)
        total = up + down  # ignore 'unknown' samples
        if total == 0:
            strip.append("unknown")
        elif down / total >= 0.05:  # ≥5% down — incident day
            strip.append("down")
        elif down / total > 0:  # 0 < down < 5% — partial
            strip.append("partial")
        else:
            strip.append("up")
    return strip


async def recent_incidents(limit: int = 10) -> list[dict]:
    """Возвращает последние N incidents — открытые и закрытые."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT i.id, i.server_id, i.started_at, i.resolved_at, i.duration_sec,
                      s.name as server_name, s.flag, s.location
               FROM incidents i
               JOIN servers s ON s.id = i.server_id
               ORDER BY i.started_at DESC
               LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def all_incidents(limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """Возвращает все incidents с пагинацией. (incidents, total_count).

    Используется для отдельной incident history страницы. `recent_incidents`
    оставлен как fastpath для основной /status (limit=5).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM incidents") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            """SELECT i.id, i.server_id, i.started_at, i.resolved_at, i.duration_sec,
                      s.name as server_name, s.flag, s.location
               FROM incidents i
               JOIN servers s ON s.id = i.server_id
               ORDER BY i.started_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return rows, total
