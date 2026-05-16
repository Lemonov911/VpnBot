"""
Multi-location VLESS backfill: get_vless_slots_missing_from_server.

При подключении нового VLESS-сервера эта функция возвращает active/grace-слоты
которые ещё не реплицированы — для каждого надо провижить пир с тем же UUID,
чтобы юзер увидел новую локацию в Happ без переимпорта подписки.
"""
from datetime import datetime, timedelta

import pytest
import aiosqlite

from services.database import (
    create_subscription,
    create_config_record,
    save_peer_to_config,
    mark_subscription_grace,
    mark_subscription_expired,
    get_vless_slots_missing_from_server,
)

FUTURE = (datetime.utcnow() + timedelta(days=30)).isoformat()


async def _insert_server(db_path, *, name: str, protocol: str = "vless",
                          is_active: int = 1) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, protocol, agent_url, agent_token,
                                    is_active, capacity, active_peers)
               VALUES (?, '1.2.3.4', ?, 'http://agent:8080', 't', ?, 100, 0)""",
            (name, protocol, is_active),
        )
        await db.commit()
        return cur.lastrowid


async def _make_sub(user_id: int, plan: str = "vpn_base") -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"test_{user_id}_{plan}",
        stars_paid=0, expires_at=datetime.fromisoformat(FUTURE),
    )


async def _replicate_vless_slot(sub_id: int, user_id: int, uuid_: str,
                                 server_ids: list[int]) -> None:
    """Эмулирует multi-location slot: один UUID, N config-rows по серверам."""
    for sid in server_ids:
        cfg_id = await create_config_record(sub_id, user_id, protocol="vless", server_id=sid)
        await save_peer_to_config(
            cfg_id, sid, wg_pubkey=uuid_, assigned_ip="",
            config_data=f"vless://{uuid_}@srv{sid}", label=f"u{user_id}_s{sid}",
            vless_uuid=uuid_,
        )


# ── basic backfill detection ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_slot_missing_from_new_server(fresh_db):
    """Slot replicated on s1 only — backfill for s2 returns it."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    await _replicate_vless_slot(sub, 100, "uuid-abc", [s1])

    missing = await get_vless_slots_missing_from_server(s2)
    assert len(missing) == 1
    assert missing[0]["vless_uuid"] == "uuid-abc"
    assert missing[0]["subscription_id"] == sub
    assert missing[0]["user_id"] == 100
    assert missing[0]["sub_status"] == "active"


@pytest.mark.asyncio
async def test_skips_slot_already_on_target_server(fresh_db):
    """Slot already replicated on s2 — backfill for s2 is empty (idempotent)."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    await _replicate_vless_slot(sub, 100, "uuid-abc", [s1, s2])

    assert await get_vless_slots_missing_from_server(s2) == []


@pytest.mark.asyncio
async def test_idempotent_double_run(fresh_db):
    """После провижа на новый сервер повторный запуск ничего не находит."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    await _replicate_vless_slot(sub, 100, "uuid-abc", [s1])

    missing_before = await get_vless_slots_missing_from_server(s2)
    assert len(missing_before) == 1

    # Эмулируем успешный backfill: добавляем slot row на s2 с тем же UUID
    await _replicate_vless_slot(sub, 100, "uuid-abc", [s2])

    assert await get_vless_slots_missing_from_server(s2) == []


# ── multi-slot, multi-user ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multiple_slots_same_sub(fresh_db):
    """Family-план с двумя VLESS-слотами → backfill возвращает оба UUID."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100, plan="vpn_max")
    await _replicate_vless_slot(sub, 100, "uuid-slot1", [s1])
    await _replicate_vless_slot(sub, 100, "uuid-slot2", [s1])

    missing = await get_vless_slots_missing_from_server(s2)
    uuids = sorted(m["vless_uuid"] for m in missing)
    assert uuids == ["uuid-slot1", "uuid-slot2"]


@pytest.mark.asyncio
async def test_multiple_users(fresh_db):
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub_a = await _make_sub(user_id=100)
    sub_b = await _make_sub(user_id=200)
    await _replicate_vless_slot(sub_a, 100, "uuid-a", [s1])
    await _replicate_vless_slot(sub_b, 200, "uuid-b", [s1])

    missing = await get_vless_slots_missing_from_server(s2)
    assert len(missing) == 2
    users = sorted(m["user_id"] for m in missing)
    assert users == [100, 200]


# ── subscription status filter ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grace_subs_included(fresh_db):
    """Grace-подписки тоже бэкфиллятся — юзер активен, должен видеть новую локацию."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    grace_until = (datetime.utcnow() + timedelta(days=14)).isoformat()
    await mark_subscription_grace(sub, grace_until)
    await _replicate_vless_slot(sub, 100, "uuid-grace", [s1])

    missing = await get_vless_slots_missing_from_server(s2)
    assert len(missing) == 1
    assert missing[0]["sub_status"] == "grace"


@pytest.mark.asyncio
async def test_expired_subs_excluded(fresh_db):
    """Истёкшие подписки игнорируются — пир провижить некуда."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    await mark_subscription_expired(sub)
    await _replicate_vless_slot(sub, 100, "uuid-exp", [s1])

    assert await get_vless_slots_missing_from_server(s2) == []


# ── config status / protocol filter ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_slots_excluded(fresh_db):
    """Слоты в статусе empty (не активированные) не бэкфиллим."""
    s1 = await _insert_server(fresh_db, name="amsterdam")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    # Создаём empty-slot без save_peer_to_config — он останется status='empty'
    await create_config_record(sub, 100, protocol="vless", server_id=s1)

    assert await get_vless_slots_missing_from_server(s2) == []


@pytest.mark.asyncio
async def test_awg_configs_excluded(fresh_db):
    """AWG-конфиги — не multi-location, в backfill не попадают."""
    s1 = await _insert_server(fresh_db, name="awg-srv", protocol="awg")
    s2 = await _insert_server(fresh_db, name="frankfurt")

    sub = await _make_sub(user_id=100)
    cfg_id = await create_config_record(sub, 100, protocol="awg", server_id=s1)
    await save_peer_to_config(
        cfg_id, s1, wg_pubkey="awg-pub", assigned_ip="10.0.0.2",
        config_data="[Interface]\n...", label="awg-peer",
    )

    assert await get_vless_slots_missing_from_server(s2) == []
