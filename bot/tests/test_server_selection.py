"""
Server-selection logic — get_best_server() picks the least-loaded server.

Loadиndex = active_peers / capacity. Filters: is_active=1 AND agent_url NOT NULL
AND protocol matches.
"""
import pytest
import aiosqlite

from services.database import get_best_server, update_server_peer_count


# ── helpers ───────────────────────────────────────────────────────────────────

async def _insert_server(db_path, *, name: str, protocol: str = "awg",
                          active_peers: int = 0, capacity: int = 100,
                          is_active: int = 1,
                          agent_url: str | None = "http://agent:8080") -> int:
    """Inserts a server row. Returns server_id."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers)
               VALUES (?, '1.2.3.4', ?, ?, 't', ?, ?, ?)""",
            (name, protocol, agent_url, is_active, capacity, active_peers),
        )
        await db.commit()
        return cur.lastrowid


# ── basic load balancing ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_picks_least_loaded_server(fresh_db):
    """Из двух серверов одного протокола — берём с меньшим load%."""
    s1 = await _insert_server(fresh_db, name="loaded",  active_peers=80, capacity=100)
    s2 = await _insert_server(fresh_db, name="empty",   active_peers=5,  capacity=100)

    best = await get_best_server("awg")
    assert best is not None
    assert best["id"] == s2
    assert best["name"] == "empty"


@pytest.mark.asyncio
async def test_load_ratio_not_absolute_count(fresh_db):
    """Сервер с 80/200 (40%) лучше чем 50/100 (50%) — балансируется по %, не absolute."""
    s_big_loaded   = await _insert_server(fresh_db, name="big",  active_peers=80, capacity=200)
    s_small_loaded = await _insert_server(fresh_db, name="small", active_peers=50, capacity=100)

    best = await get_best_server("awg")
    assert best["id"] == s_big_loaded, "big server has lower load ratio (40% vs 50%)"


# ── filters ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_inactive_server(fresh_db):
    """is_active=0 → исключается из выборки даже если самый пустой."""
    await _insert_server(fresh_db, name="inactive-empty", active_peers=0,
                          capacity=100, is_active=0)
    s_active = await _insert_server(fresh_db, name="active-loaded",
                                     active_peers=90, capacity=100)

    best = await get_best_server("awg")
    assert best is not None
    assert best["id"] == s_active


@pytest.mark.asyncio
async def test_skips_server_without_agent_url(fresh_db):
    """agent_url IS NULL → сервер без агента, нельзя провижинить пир."""
    await _insert_server(fresh_db, name="no-agent",
                          active_peers=0, capacity=100, agent_url=None)
    s_agent = await _insert_server(fresh_db, name="with-agent",
                                    active_peers=90, capacity=100)

    best = await get_best_server("awg")
    assert best["id"] == s_agent


@pytest.mark.asyncio
async def test_filters_by_protocol(fresh_db):
    """Запрос awg НЕ должен вернуть vless-сервер."""
    s_vless = await _insert_server(fresh_db, name="vless-empty",
                                    protocol="vless", active_peers=0)
    s_awg   = await _insert_server(fresh_db, name="awg-loaded",
                                    protocol="awg", active_peers=90)

    awg_best = await get_best_server("awg")
    vless_best = await get_best_server("vless")
    assert awg_best["id"] == s_awg
    assert vless_best["id"] == s_vless


@pytest.mark.asyncio
async def test_returns_none_when_no_servers(fresh_db):
    """Нет ни одного сервера → None (caller'у нужно показать «нет серверов»)."""
    assert await get_best_server("awg") is None


@pytest.mark.asyncio
async def test_returns_none_when_only_inactive_servers(fresh_db):
    """Все сервера is_active=0 → None."""
    await _insert_server(fresh_db, name="off1", is_active=0)
    await _insert_server(fresh_db, name="off2", is_active=0)
    assert await get_best_server("awg") is None


# ── update_server_peer_count: load tracking ──────────────────────────────────

@pytest.mark.asyncio
async def test_update_peer_count_changes_selection(fresh_db):
    """После +1 к active_peers выбор может перейти на другой сервер."""
    s1 = await _insert_server(fresh_db, name="s1", active_peers=50, capacity=100)
    s2 = await _insert_server(fresh_db, name="s2", active_peers=51, capacity=100)

    assert (await get_best_server("awg"))["id"] == s1
    await update_server_peer_count(s1, +5)  # теперь s1 загружен сильнее
    assert (await get_best_server("awg"))["id"] == s2


@pytest.mark.asyncio
async def test_update_peer_count_clamps_at_zero(fresh_db):
    """active_peers НЕ должен уйти в минус (MAX(0, ...) в SQL)."""
    s = await _insert_server(fresh_db, name="s", active_peers=3, capacity=100)
    await update_server_peer_count(s, -10)

    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute(
            "SELECT active_peers FROM servers WHERE id=?", (s,)
        ) as cur:
            count = (await cur.fetchone())[0]
    assert count == 0, "Negative peer count would corrupt the load formula"
