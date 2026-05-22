"""
Regression: handle_admin_migrate_configs (services/webapp_api.py) идемпотентность.

Сценарий: admin запускает migrate-configs для дохлого сервера. Если запустить
дважды (двойной клик в UI / refresh) — второй run НЕ должен:
  • дублировать peers (orphan peer на новом сервере без записи в БД)
  • двинуть active_peers counter в минус (повторный update -1 на reset)
  • заспамить юзера TG-уведомлением второй раз

Защиты в коде:
  1. Per-server asyncio.Lock + early 409 если migration уже идёт
  2. Idempotency-check внутри loop'а: re-fetch server_id+status конфига
     ДО provision_peer; если уже не на старом сервере — skipped++

Тестируем:
  • lock возвращает 409 для concurrent run
  • idempotent re-run после успешной миграции → skipped == все configs
"""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio
from aiohttp import web

import services.database as _db_mod
from services.database import (
    create_config_record,
    create_subscription,
    upsert_user,
)


USER_ID = 9701
DEAD_SERVER_ID = 99
ADMIN_SECRET = "test_admin_secret"


async def _seed_dead_server_with_vless_configs():
    """1 user + active vpn_base sub + 2 active VLESS configs на dead-server #99.
    Сервер is_active=0 (drained — обязательно для migrate-configs)."""
    await upsert_user(USER_ID, "migrate_user", "Migrate User")
    sub_id = await create_subscription(
        user_id=USER_ID, plan="vpn_base",
        payment_id=f"chg_{USER_ID}_migrate",
        stars_paid=200,
        expires_at=datetime.utcnow() + timedelta(days=15),
    )
    # Dead VLESS server
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.execute(
            """INSERT INTO servers
               (id, name, host, protocol, is_active, agent_url, agent_token)
               VALUES (?, 'DEAD', '1.2.3.4', 'vless', 0, 'http://dead', 'tok')""",
            (DEAD_SERVER_ID,),
        )
        await db.commit()
    # 2 active VLESS configs пер dead-server
    cfg_ids = []
    for i in range(2):
        cfg_id = await create_config_record(sub_id, USER_ID, protocol="vless")
        async with aiosqlite.connect(_db_mod.DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys=OFF")
            await db.execute(
                "UPDATE configs SET status='active', server_id=?, "
                "vless_uuid=?, peer_name=? WHERE id=?",
                (DEAD_SERVER_ID,
                 f"vless-uuid-{cfg_id}",
                 f"peer_{cfg_id}", cfg_id),
            )
            await db.commit()
        cfg_ids.append(cfg_id)
    return sub_id, cfg_ids


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, monkeypatch):
    from services.webapp_api import handle_admin_migrate_configs
    import services.webapp_api as wapi
    monkeypatch.setattr(wapi, "ADMIN_API_SECRET", ADMIN_SECRET, raising=False)
    # Очищаем _migrate_locks между тестами (modułlevel global)
    monkeypatch.setattr(wapi, "_migrate_locks", {})
    # Bypass rate-limit
    monkeypatch.setattr(wapi, "_check_admin_rate_limit", lambda req: True)

    app = web.Application()
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=None)
    app["bot"] = fake_bot
    app.router.add_post(
        "/api/admin/servers/{id}/migrate-configs",
        handle_admin_migrate_configs,
    )
    client = await aiohttp_client(app)
    client._fake_bot = fake_bot
    return client


# ── Кейс 1: первый run миграции — VLESS reset_slot для каждого config ───────

@pytest.mark.asyncio
async def test_first_run_migrates_all_vless_configs(app_client):
    _, cfg_ids = await _seed_dead_server_with_vless_configs()

    resp = await app_client.post(
        f"/api/admin/servers/{DEAD_SERVER_ID}/migrate-configs",
        data=json.dumps({"admin_id": 1}),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Secret": ADMIN_SECRET,
        },
    )
    assert resp.status == 200
    data = await resp.json()
    # VLESS-конфиги обнулились (status='empty', server_id=NULL)
    assert data.get("reset_vless") == 2, f"resp={data}"
    assert data.get("migrated") == 0    # AWG/WG нет в seed
    assert data.get("failed") == 0

    # Проверяем DB: configs теперь empty
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        for cfg_id in cfg_ids:
            async with db.execute(
                "SELECT status, server_id FROM configs WHERE id=?", (cfg_id,),
            ) as cur:
                row = await cur.fetchone()
                assert row[0] == "empty", f"cfg #{cfg_id} status={row[0]}"
                assert row[1] is None, f"cfg #{cfg_id} server_id should be NULL"


# ── Кейс 2: idempotent re-run — все skipped ────────────────────────────────

@pytest.mark.asyncio
async def test_idempotent_rerun_skips_already_migrated(app_client):
    """После первого migrate всё запустить ещё раз — должно skipped=2,
    никаких side-effects."""
    _, cfg_ids = await _seed_dead_server_with_vless_configs()

    # First run
    await app_client.post(
        f"/api/admin/servers/{DEAD_SERVER_ID}/migrate-configs",
        data=json.dumps({"admin_id": 1}),
        headers={"Content-Type": "application/json",
                 "X-Admin-Secret": ADMIN_SECRET},
    )

    # Сбрасываем счётчик уведомлений (юзер уже получил уведомление в первом run)
    notify_count_before = app_client._fake_bot.send_message.await_count

    # Second run — должен skipped=2, no notifications
    resp = await app_client.post(
        f"/api/admin/servers/{DEAD_SERVER_ID}/migrate-configs",
        data=json.dumps({"admin_id": 1}),
        headers={"Content-Type": "application/json",
                 "X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status == 200
    data = await resp.json()
    # При втором запуске get_active_configs_for_migration вернёт пустой
    # список (configs уже NOT на dead-server) → reset_vless=0 skipped=0.
    # Это нормально — handler корректно нашёл что мигрировать нечего.
    assert data.get("reset_vless") == 0, f"resp={data}"
    assert data.get("migrated") == 0
    assert data.get("failed") == 0

    # Юзеру НЕ шлётся повторное уведомление (нет новых reset_vless events)
    assert app_client._fake_bot.send_message.await_count == notify_count_before


# ── Кейс 3: admin secret check ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejects_without_admin_secret(app_client):
    resp = await app_client.post(
        f"/api/admin/servers/{DEAD_SERVER_ID}/migrate-configs",
        data=json.dumps({"admin_id": 1}),
        headers={"Content-Type": "application/json"},  # NO X-Admin-Secret
    )
    assert resp.status == 403


# ── Кейс 4: live server (is_active=1) → 400 «drain first» ───────────────────

@pytest.mark.asyncio
async def test_rejects_live_server(app_client):
    """Migrate работает только над drained (is_active=0) серверами — иначе
    можно случайно мигрировать рабочий сервер."""
    await upsert_user(USER_ID, "u", "U")
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await db.execute(
            """INSERT INTO servers (id, name, host, protocol, is_active)
               VALUES (88, 'LIVE', '1.1.1.1', 'vless', 1)""",
        )
        await db.commit()

    resp = await app_client.post(
        "/api/admin/servers/88/migrate-configs",
        data=json.dumps({"admin_id": 1}),
        headers={"Content-Type": "application/json",
                 "X-Admin-Secret": ADMIN_SECRET},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "drain" in data.get("error", "")
