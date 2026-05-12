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


async def probe_all_servers():
    """Главный тик: пробит все is_active=1 серверы и пишет результаты."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM servers WHERE is_active=1 ORDER BY id"
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
                               server["id"], e)
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
