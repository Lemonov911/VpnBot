"""Regression тесты для UUID consistency (bug Ангелина 22.05).

Проблема была: provision_trial и bootstrap_vless_for_sub генерили
свой `uuid.uuid4()` для peer'а, а _resolve_vless_urls строит sub_url
из `users.vless_uuid` (per-user persistent). Mismatch → клиент шлёт
users.vless_uuid, agent не находит → Reality handshake EOF.

Fix: оба flow'а зовут ensure_user_vless_uuid(user_id) — один UUID
на все локации и согласован с тем что отдаётся в sub_url.

Тесты гарантируют что любой возврат к uuid.uuid4() в этих местах
немедленно ловится.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.database import (
    ensure_user_vless_uuid,
    upsert_user,
)


@pytest.mark.asyncio
async def test_ensure_user_vless_uuid_persists(fresh_db):
    """Sanity: ensure_user_vless_uuid возвращает одинаковый UUID при повторном
    вызове. Это базовое инвариантное свойство, на котором держится sub_url."""
    user_id = 7001
    await upsert_user(user_id, "test", "test")
    u1 = await ensure_user_vless_uuid(user_id)
    u2 = await ensure_user_vless_uuid(user_id)
    assert u1 == u2
    assert len(u1) == 36  # uuid v4 string length


@pytest.mark.asyncio
async def test_bootstrap_uses_users_vless_uuid(fresh_db, monkeypatch):
    """REGRESSION: bootstrap_vless_for_sub должен звать ensure_user_vless_uuid,
    а не uuid.uuid4(). Проверяем что peer провижится с users.vless_uuid."""
    from datetime import datetime, timedelta
    from services import trial
    from services.database import create_config_record, create_subscription

    user_id = 7002
    await upsert_user(user_id, "test", "test")
    # Заранее задаём users.vless_uuid через ensure — это значение должно
    # попасть в provision_peer как peer_id.
    expected_uuid = await ensure_user_vless_uuid(user_id)

    sub_id = await create_subscription(
        user_id=user_id, plan="vpn_max",
        payment_id="grant_uuid_test",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="gift",
    )
    await create_config_record(sub_id, user_id, protocol="vless")

    fake_server = {"id": 11, "name": "AMS", "agent_url": "http://x", "flag": "🇳🇱"}
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=[fake_server]))

    provision_peer_ids = []
    async def fake_provision(server, label, tier, peer_id=None):
        provision_peer_ids.append(peer_id)
        from dataclasses import dataclass
        @dataclass
        class _P:
            id: str
            config: str
        return _P(id=peer_id, config=f"vless://{peer_id}@x")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    monkeypatch.setattr(trial, "save_peer_to_config", AsyncMock())

    await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert len(provision_peer_ids) == 1
    # CRITICAL invariant: peer_id переданный в provision_peer == users.vless_uuid.
    # Если это сломается — sub_url отдаст один UUID, agent сохранит другой,
    # клиенты ломаются с Reality EOF.
    assert provision_peer_ids[0] == expected_uuid, (
        f"bootstrap_vless_for_sub использует левый UUID! "
        f"users.vless_uuid={expected_uuid}, provision_peer передан={provision_peer_ids[0]}. "
        f"Скорее всего вернулся uuid.uuid4() — sub_url сломается."
    )


@pytest.mark.asyncio
async def test_bootstrap_multi_server_same_uuid(fresh_db, monkeypatch):
    """Multi-location VLESS subscription: ВСЕ серверы получают ОДИН UUID
    (а не разные). Happ потом видит этот же UUID на каждой локации."""
    from datetime import datetime, timedelta
    from services import trial
    from services.database import create_config_record, create_subscription

    user_id = 7003
    await upsert_user(user_id, "test_multi", "test_multi")
    expected_uuid = await ensure_user_vless_uuid(user_id)

    sub_id = await create_subscription(
        user_id=user_id, plan="vpn_max",
        payment_id="grant_multi",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=30),
        payment_provider="gift",
    )
    for _ in range(3):
        await create_config_record(sub_id, user_id, protocol="vless")

    servers = [
        {"id": 11, "name": "AMS", "agent_url": "http://a", "flag": "🇳🇱"},
        {"id": 14, "name": "CLT", "agent_url": "http://b", "flag": "🇺🇸"},
        {"id": 20, "name": "TYO", "agent_url": "http://c", "flag": "🇯🇵"},
    ]
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=servers))

    all_peer_ids = []
    async def fake_provision(server, label, tier, peer_id=None):
        all_peer_ids.append(peer_id)
        from dataclasses import dataclass
        @dataclass
        class _P:
            id: str
            config: str
        return _P(id=peer_id, config=f"vless://{peer_id}@srv{server['id']}")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    monkeypatch.setattr(trial, "save_peer_to_config", AsyncMock())

    n = await trial.bootstrap_vless_for_sub(sub_id, user_id, "vpn_max")

    assert n == 3
    # ВСЕ peer'ы провижены с одним и тем же UUID
    assert all(pid == expected_uuid for pid in all_peer_ids), (
        f"Multi-location нарушение: разные UUID на разных серверах! "
        f"Ожидали {expected_uuid}, получили {all_peer_ids}"
    )


@pytest.mark.asyncio
async def test_provision_trial_uses_users_vless_uuid(fresh_db, monkeypatch):
    """REGRESSION: provision_trial тоже должен использовать users.vless_uuid,
    не uuid.uuid4() — потому что после triаl юзер сразу пользуется sub_url'ом,
    и mismatch сломает первую же сессию."""
    from services import trial

    user_id = 7004
    # Нужно создать user-row до provision_trial (FK constraint на subscriptions).
    # ensure_user_vless_uuid НЕ зовём — пусть provision_trial allocate'нет сам.
    await upsert_user(user_id, "test_trial_uuid", "test_trial_uuid")

    # provision_trial вызывает create_config_record(server_id=11) — FK на servers.
    # Вставляем fake row напрямую (не через add_server, который требует много полей).
    import aiosqlite
    from services.database import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO servers (id, name, host, protocol, is_active, agent_url) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (11, "AMS", "1.2.3.4", "vless", "http://x"),
        )
        await db.commit()

    fake_vless_server = {"id": 11, "name": "AMS", "host": "1.2.3.4",
                          "agent_url": "http://x", "flag": "🇳🇱", "city": "Amsterdam"}
    monkeypatch.setattr(trial, "get_all_active_servers",
                         AsyncMock(return_value=[fake_vless_server]))
    # best_server для AWG — вернём None чтобы AWG-блок не пытался работать.
    monkeypatch.setattr(trial, "get_best_server", AsyncMock(return_value=None))

    provision_peer_ids = []
    async def fake_provision(server, label, tier, peer_id=None):
        provision_peer_ids.append(peer_id)
        from dataclasses import dataclass
        @dataclass
        class _P:
            id: str
            config: str
        return _P(id=peer_id, config=f"vless://{peer_id}@x")
    monkeypatch.setattr(trial, "provision_peer", fake_provision)
    monkeypatch.setattr(trial, "update_server_peer_count", AsyncMock())
    monkeypatch.setattr(trial, "save_peer_to_config", AsyncMock())

    # provision_trial вызывает upsert_user внутри + create_subscription
    await trial.provision_trial(user_id)

    # Что users.vless_uuid стало — то и было передано в provision_peer
    final_uuid = await ensure_user_vless_uuid(user_id)
    assert len(provision_peer_ids) == 1
    assert provision_peer_ids[0] == final_uuid, (
        f"provision_trial не использует ensure_user_vless_uuid! "
        f"final users.vless_uuid={final_uuid}, "
        f"provision_peer передан={provision_peer_ids[0]}. "
        f"Это вернёт сломанный sub_url на первой же сессии триала."
    )
