"""
`_close_dangling_grace` — internal helper, тестируется отдельно от wrapper'а.

В `test_close_dangling_grace.py` уже мокается `_close_dangling_grace` целиком
и проверяется только что caller вызывает его с правильными аргументами.
Здесь — наоборот: мокаем агента и проверяем что _close_dangling_grace
ИЗНУТРИ корректно делает:

  1. AWG cfg → remove_peer("awg", peer_name) + peer_count -1 + slot reset.
  2. VLESS cfg → remove_peer(svc, uuid) + peer_count -1 + slot reset.
  3. VLESS-grace: detect по порту `:9453` в config_data → svc='vless-grace'.
  4. Agent error: continue вместо abort, peer_count всё равно декрементим
     (slot reset на DB-стороне → counter иначе застревает).
  5. Финальный CAS `grace → expired` через mark_subscription_expired_from_grace.

Эти инварианты критичны: каждый из них baked-in to comment'ах в production
коде (`scheduler.py`, `grace.py`) — без тестов они «защищены» только code-review.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from services.database import (
    upsert_user,
    create_subscription,
    create_config_record,
    activate_config_slot,
    claim_config_slot_for_activation,
    mark_subscription_grace,
    get_subscription_by_id,
    get_config_by_id,
)
from services.vpnctl_client import VpnctlError


USER_ID = 13001


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_with_server(fresh_db):
    """Test server с agent_url + active_peers=5 (чтобы видеть декремент).

    Колонка в БД называется `active_peers` (не `peer_count`) — это load-balancer
    counter, используется в `select_least_loaded_server` для выбора сервера
    с меньшей загрузкой при provisioning'е. `update_server_peer_count(-1)`
    декрементит именно эту колонку (MAX(0, ...) защита от ухода в минус)."""
    async with aiosqlite.connect(fresh_db) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active, active_peers)
               VALUES ('Test', '1.2.3.4', 'http://agent:8080', 'tok', 1, 5)"""
        )
        await db.commit()
        return cur.lastrowid


async def _make_grace_sub(plan: str = "vpn_base") -> int:
    """Создаёт sub в grace-статусе. expires_at в прошлом — иначе
    mark_subscription_grace CAS откажет."""
    await upsert_user(USER_ID, "u", "U")
    expires_at = datetime.utcnow() - timedelta(days=1)
    sub_id = await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=expires_at,
    )
    grace_until = (datetime.utcnow() + timedelta(days=10)).isoformat()
    ok = await mark_subscription_grace(sub_id, grace_until)
    assert ok, "fixture: pre-mark to grace failed"
    return sub_id


async def _make_active_cfg(sub_id: int, server_id: int, *,
                            protocol: str = "awg",
                            peer_name: str = "p1",
                            assigned_ip: str = "10.0.0.10",
                            vless_uuid: str | None = None,
                            config_data: str = "[Interface]\n...") -> int:
    cfg_id = await create_config_record(sub_id, USER_ID, protocol=protocol,
                                         server_id=server_id)
    assert await claim_config_slot_for_activation(cfg_id), "claim failed (test setup)"
    assert await activate_config_slot(
        cfg_id, peer_name=peer_name, config_data=config_data,
        server_id=server_id, assigned_ip=assigned_ip, vless_uuid=vless_uuid,
    ), "activate CAS failed (test setup)"
    return cfg_id


def _fake_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _mock_client():
    client = AsyncMock()
    client.remove_peer = AsyncMock()
    return client


# ── AWG path ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awg_cfg_removed_counter_decremented_slot_reset(fresh_db, db_with_server):
    """AWG-конфиг: remove_peer("awg", peer_name), counter -1, slot reset.

    Это «холодный» revoke (юзер уже в grace, теперь cross-plan upgrade →
    окончательно закрываем). Без декремента counter сервер бы постепенно
    «заполнялся» — capacity checker (used = active_peers / capacity)
    отказывал бы новым продажам несмотря на свободные слоты в реале."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    cfg_id = await _make_active_cfg(sub_id, db_with_server, protocol="awg",
                                      peer_name="awg_peer_42",
                                      assigned_ip="10.0.0.42")

    client = _mock_client()
    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    # remove_peer вызван корректно
    client.remove_peer.assert_awaited_once_with("awg", "awg_peer_42")

    # cfg ушёл в empty
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"
    assert cfg["peer_name"] is None

    # sub перешла в expired
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"

    # peer_count -1 (был 5, стал 4)
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute("SELECT active_peers FROM servers WHERE id=?", (db_with_server,)) as cur:
            row = await cur.fetchone()
    assert row[0] == 4, "active_peers must decrement after revoke"


@pytest.mark.asyncio
async def test_awg_counter_decrements_even_when_agent_fails(fresh_db, db_with_server):
    """Если remove_peer падает (агент down/peer уже не существует) — counter
    ВСЁ РАВНО уменьшаем, потому что DB-сторона делает reset_config_slot
    ниже. Если бы пропускали декремент, counter застрял бы навсегда — orphan
    на агенте подберёт hourly _sync_vless_active_uuids, но counter
    decoupled от agent state и нужен явный fix.

    Это поведение явно baked-in to comment в grace.py:302-307."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    await _make_active_cfg(sub_id, db_with_server, protocol="awg",
                           peer_name="ghost", assigned_ip="10.0.0.99")

    client = _mock_client()
    client.remove_peer.side_effect = VpnctlError("404 not found")  # ghost peer

    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    # Counter всё равно -1
    async with aiosqlite.connect(fresh_db) as db:
        async with db.execute("SELECT active_peers FROM servers WHERE id=?", (db_with_server,)) as cur:
            row = await cur.fetchone()
    assert row[0] == 4, (
        "counter decrement must happen even on agent failure — иначе counter "
        "застрял бы навсегда (DB сторона: slot reset, agent сторона: ghost peer)"
    )

    # Sub всё равно expired
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


# ── VLESS path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vless_in_grace_inbound_detected_by_port_marker(fresh_db, db_with_server):
    """VLESS-конфиг с config_data содержащим `:9453` (порт vless-grace) →
    current_vless_service возвращает 'vless-grace', revoke попадает в правильный
    inbound. Если бы revoke шёл по `vless-base` (по plan_key), agent вернул бы
    «peer not found» — пир остался бы в vless-grace навсегда."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    cfg_data = "vless://uuid@host:9453/?security=reality"  # маркер vless-grace
    await _make_active_cfg(
        sub_id, db_with_server, protocol="vless",
        peer_name="u_vless_1", vless_uuid="uuid-vless-1",
        config_data=cfg_data,
    )

    client = _mock_client()
    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    client.remove_peer.assert_awaited_once_with("vless-grace", "uuid-vless-1")


@pytest.mark.asyncio
async def test_vless_plan_dependent_inbound_when_no_marker(fresh_db, db_with_server):
    """VLESS-конфиг без маркерных портов (живёт в normal inbound) → revoke
    идёт в vless-base (или vless-max в зависимости от plan_key).

    Это путь cross-plan upgrade ПО ХОДУ active sub (без перехода в grace):
    юзер платит другой план не-в-grace, и старая sub ушла в grace одновременно
    с throttle, конфиг ещё в normal inbound — revoke должен это видеть."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    cfg_data = "vless://uuid@host:8443/?security=reality"  # vless-base порт
    await _make_active_cfg(
        sub_id, db_with_server, protocol="vless",
        peer_name="u_v", vless_uuid="uuid-normal",
        config_data=cfg_data,
    )

    client = _mock_client()
    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    # Для vpn_base normal inbound = vless-base
    client.remove_peer.assert_awaited_once_with("vless-base", "uuid-normal")


# ── edge: no server / no configs ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cfg_without_server_id_skips_agent_call_but_resets_slot(fresh_db, db_with_server):
    """Cfg создан, но server_id=NULL (slot был empty, никогда не provision'ился
    на агенте — например, юзер купил план, передумал, не открыл config'и).
    _close_dangling_grace должен пропустить agent call (нечего удалять) но
    всё равно сбросить slot и в финале mark sub expired."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    # cfg без server_id: создаём напрямую (не через _make_active_cfg которая
    # требует server_id для activate_config_slot)
    cfg_id = await create_config_record(sub_id, USER_ID, protocol="awg",
                                          server_id=None)
    assert await claim_config_slot_for_activation(cfg_id)
    assert await activate_config_slot(
        cfg_id, peer_name="dangling", config_data="...",
        server_id=None, assigned_ip="10.0.0.1",
    )

    client = _mock_client()
    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    # Agent НЕ был вызван (нет server_id)
    client.remove_peer.assert_not_awaited()

    # Slot всё равно сброшен
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"

    # Sub expired
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


@pytest.mark.asyncio
async def test_sub_with_no_configs_still_marked_expired(fresh_db, db_with_server):
    """Sub в grace без configs (например, refund уже сбросил все configs в
    empty, но статус остался grace из-за race). _close_dangling_grace должен
    финализировать переход в expired независимо."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    # ни одного config — не создаём

    with patch("services.grace.client_for_server"):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired", (
        "пустая sub (no configs) всё равно должна получить финальный CAS "
        "grace→expired — иначе она зависла бы в grace навсегда"
    )


# ── race protection on final mark ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_skipped_if_sub_not_in_grace_anymore(fresh_db, db_with_server):
    """Race: параллельно с _close_dangling_grace юзер заплатил, renew-from-grace
    вернул sub в active. Финальный mark_subscription_expired_from_grace CAS
    откажет (status != 'grace' уже) — функция не падает, sub остаётся active.

    Это критично: иначе мы бы revoke'ли peers + пометили sub expired у
    юзера который только что заплатил."""
    from services.grace import _close_dangling_grace

    sub_id = await _make_grace_sub("vpn_base")
    # cfg есть, чтобы revoke-loop отработал нормально
    await _make_active_cfg(sub_id, db_with_server, protocol="awg",
                           peer_name="p", assigned_ip="10.0.0.1")

    # Симулируем race: между _make_grace_sub и close_dangling_grace
    # юзер заплатил → renew_subscription_from_grace перевёл sub в active.
    # Делаем это прямо в БД для простоты (минимальная имитация renew).
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute(
            "UPDATE subscriptions SET status='active', grace_until=NULL, "
            "expires_at=datetime('now', '+30 days') WHERE id=?",
            (sub_id,),
        )
        await db.commit()

    client = _mock_client()
    with patch("services.grace.client_for_server", return_value=client):
        await _close_dangling_grace(_fake_bot(), sub_id, "vpn_base")

    # Sub осталась active (renew выиграл race)
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active", (
        "renew-from-grace выиграл race → финальный CAS mark_expired_from_grace "
        "должен no-op'нуться, sub остаётся active"
    )
    # ⚠️ Note: revoke-loop ВЫШЕ финального CAS — он уже отозвал peer на агенте.
    # Это known limitation: cross-plan close сейчас не атомарен (revoke peers
    # → final CAS). Если хотим строгую атомарность, надо CAS-проверять статус
    # ПЕРЕД revoke'ом. Сейчас защищаемся одним вызовом в практичном flow:
    # `close_dangling_grace_subs_after_upgrade` фильтрует grace-subs ДО входа
    # сюда. Это TODO: добавить опциональный pre-check status='grace' в начале
    # _close_dangling_grace чтобы при race не делать destructive ops.
    client.remove_peer.assert_awaited_once()
