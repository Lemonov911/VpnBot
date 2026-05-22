"""
Scheduler `_process_grace_expired_subscriptions` — edge cases НЕ покрытые
в `test_scheduler_grace.py`.

Что там уже:
  - AWG grace → expired (unthrottle + remove + slot reset)
  - VLESS grace → expired (remove vless-grace + slot reset)

Что добавляем:
  1. **Race с renew-from-grace**: `mark_subscription_expired_from_grace` CAS
     отказал (юзер заплатил между snapshot и mark) → НИКАКОЙ revoke не идёт.
     Это самая опасная защита: scheduler.py:550-555 — без atomic mark
     старая логика revoke'ла часть configs пока юзер платил.
  2. **AWG unthrottle fails → remove всё равно идёт** (continue-on-error).
  3. **Empty snapshot = noop** (нет grace-expired subs).
  4. **User notification отправлена** (`bot_expiry_notice` с CTA).
  5. **Multiple subs**: все обрабатываются.
  6. **Cfg без server_id**: slot reset без agent call.
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


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_with_server(fresh_db):
    async with aiosqlite.connect(fresh_db) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active, active_peers)
               VALUES ('Test', '1.2.3.4', 'http://agent:8080', 'tok', 1, 10)"""
        )
        await db.commit()
        return cur.lastrowid


async def _make_grace_sub(*, user_id: int, plan: str = "vpn_base",
                            grace_until_offset_hours: float = -1) -> int:
    """Создаёт sub в grace со заданным grace_until (offset от now()).

    `grace_until_offset_hours < 0` → grace истёк, scheduler возьмёт.
    `grace_until_offset_hours > 0` → grace ещё активен, scheduler пропустит.
    """
    await upsert_user(user_id, "u", "U")
    sub_id = await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"t_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145,
        expires_at=datetime.utcnow() - timedelta(days=15),  # давно истекла
    )
    # mark_subscription_grace CAS требует past expires_at — у нас -15д ✓
    gu = (datetime.utcnow() + timedelta(hours=grace_until_offset_hours)).isoformat()
    ok = await mark_subscription_grace(sub_id, gu)
    assert ok, "fixture: pre-mark to grace failed"
    return sub_id


async def _make_active_awg(sub_id: int, server_id: int, *,
                            user_id: int,
                            peer_name: str = "p_awg",
                            assigned_ip: str = "10.0.0.2") -> int:
    cfg_id = await create_config_record(sub_id, user_id, protocol="awg",
                                         server_id=server_id)
    assert await claim_config_slot_for_activation(cfg_id)
    assert await activate_config_slot(
        cfg_id, peer_name=peer_name, config_data="[Interface]\n...",
        server_id=server_id, assigned_ip=assigned_ip,
    )
    return cfg_id


def _fake_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _mock_client():
    client = AsyncMock()
    client.throttle_peer = AsyncMock()
    client.unthrottle_peer = AsyncMock()
    client.remove_peer = AsyncMock()
    client.add_peer = AsyncMock(return_value=MagicMock(config="x"))
    return client


# ── race protection ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_revoke_when_user_paid_mid_grace_expiry(fresh_db, db_with_server):
    """RACE: scheduler выбрал sub из get_grace_expired_subscriptions, в этот
    момент юзер заплатил и renew-from-grace перевёл sub → active. Atomic CAS
    mark_subscription_expired_from_grace откажет (status='active' уже не grace).
    Scheduler должен НЕ ревокать ни один config — иначе юзер заплатил, а
    через пару секунд все его VLESS peers удалены.

    Audit 17.05 #2 — без CAS partial revoke оставлял sub в inconsistent
    state: configs 0..K отозваны, K+ нет, status=active. Юзер видит «отвал»
    сразу после оплаты."""
    server_id = db_with_server
    sub_id = await _make_grace_sub(user_id=500)
    cfg_id = await _make_active_awg(sub_id, server_id, user_id=500,
                                       peer_name="p_race",
                                       assigned_ip="10.0.0.500")

    mock_client = _mock_client()

    # Имитируем race: до того как scheduler вызовет mark_expired_from_grace,
    # renew перевёл sub в active.
    async with aiosqlite.connect(fresh_db) as db:
        await db.execute(
            "UPDATE subscriptions SET status='active', grace_until=NULL, "
            "expires_at=datetime('now', '+30 days') WHERE id=?",
            (sub_id,),
        )
        await db.commit()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    # CAS отказал → ни remove_peer, ни unthrottle не вызваны
    mock_client.remove_peer.assert_not_awaited()
    mock_client.unthrottle_peer.assert_not_awaited()

    # Config slot нетронут
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "active", (
        "race winner — sub renewed — configs ДОЛЖНЫ остаться active. "
        "Если scheduler revoke'нул бы, юзер увидел бы «отвал» сразу после оплаты."
    )

    # Sub осталась active
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active"


# ── AWG unthrottle fails ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awg_remove_continues_when_unthrottle_fails(fresh_db, db_with_server):
    """AWG grace → expired: сначала unthrottle (снять tc), потом remove_peer.
    Если unthrottle падает (peer уже не throttled или агент глюкнул) —
    remove_peer ВСЁ РАВНО должен пройти. scheduler.py:577-587 явно ловит
    `unthrottle_err` и продолжает к remove. Без этого ghost peer остался бы
    на агенте, занимая capacity."""
    server_id = db_with_server
    sub_id = await _make_grace_sub(user_id=600)
    cfg_id = await _make_active_awg(sub_id, server_id, user_id=600,
                                       peer_name="p_unth_fail",
                                       assigned_ip="10.0.0.600")

    mock_client = _mock_client()
    # unthrottle_peer бросает, remove_peer работает
    mock_client.unthrottle_peer.side_effect = VpnctlError("tc filter not found")

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    # unthrottle вызван — но фейлится
    mock_client.unthrottle_peer.assert_awaited_once()
    # remove_peer ВСЁ РАВНО вызван (continue-on-error)
    mock_client.remove_peer.assert_awaited_once()
    assert mock_client.remove_peer.await_args.args[0] == "awg"

    # Slot всё равно reset
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"
    # Sub expired
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


# ── empty snapshot ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_grace_expired_subs_is_noop(fresh_db, db_with_server):
    """Никаких grace-expired subs (нет в БД ИЛИ есть но grace_until в будущем)
    → функция тихо возвращается без ошибок."""
    # Делаем sub в grace, но grace_until в будущем (не «expired grace»)
    await _make_grace_sub(user_id=700, grace_until_offset_hours=+24)

    with patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())  # must not raise


# ── notification with renew CTA ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_gets_final_expiry_notice_with_renew_button(
        fresh_db, db_with_server):
    """После окончательного перехода в expired юзер получает уведомление
    `bot_expiry_notice` с inline CTA «Купить снова». Это последний шанс
    retention'а — юзер на 14-day cooldown потеряет конфиги полностью."""
    server_id = db_with_server
    sub_id = await _make_grace_sub(user_id=800)
    await _make_active_awg(sub_id, server_id, user_id=800,
                            peer_name="p_notif", assigned_ip="10.0.0.800")

    mock_client = _mock_client()
    sent_calls = []

    async def _track_send(bot, user_id, text, **kwargs):
        sent_calls.append({"user_id": user_id, "text": text, "kwargs": kwargs})
        return True

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled",
                new=AsyncMock(side_effect=_track_send)):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    user_msgs = [c for c in sent_calls if c["user_id"] == 800]
    assert len(user_msgs) == 1, "юзер должен получить уведомление о full expiry"
    assert user_msgs[0]["kwargs"].get("reply_markup") is not None, (
        "expiry notice должен иметь inline CTA — retention loss без него"
    )


# ── multiple subs ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multiple_grace_expired_subs_all_processed(fresh_db, db_with_server):
    """Два юзера, у обоих grace истёк → оба обрабатываются (не abort после
    первого). Защита от bug'а когда исключение в одном sub'е блокировало
    обработку остальных."""
    server_id = db_with_server
    sub_a = await _make_grace_sub(user_id=900)
    sub_b = await _make_grace_sub(user_id=901)
    cfg_a = await _make_active_awg(sub_a, server_id, user_id=900,
                                      peer_name="pa", assigned_ip="10.0.0.901")
    cfg_b = await _make_active_awg(sub_b, server_id, user_id=901,
                                      peer_name="pb", assigned_ip="10.0.0.902")

    mock_client = _mock_client()

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    # Оба remove_peer'a (один на sub_a, один на sub_b)
    assert mock_client.remove_peer.await_count == 2
    assert (await get_subscription_by_id(sub_a))["status"] == "expired"
    assert (await get_subscription_by_id(sub_b))["status"] == "expired"
    assert (await get_config_by_id(cfg_a))["status"] == "empty"
    assert (await get_config_by_id(cfg_b))["status"] == "empty"


# ── cfg without server_id ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cfg_without_server_id_resets_slot_without_agent(
        fresh_db, db_with_server):
    """Cfg.server_id=NULL — slot никогда не provision'ился на агенте.
    scheduler не должен звать agent (нечего удалять), но slot RESET всё
    равно нужен."""
    sub_id = await _make_grace_sub(user_id=1000)
    cfg_id = await create_config_record(sub_id, 1000, protocol="awg",
                                          server_id=None)
    assert await claim_config_slot_for_activation(cfg_id)
    assert await activate_config_slot(
        cfg_id, peer_name="orphan", config_data="x",
        server_id=None, assigned_ip="10.0.0.1",
    )

    mock_client = _mock_client()
    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    # Agent НЕ вызван
    mock_client.remove_peer.assert_not_awaited()
    mock_client.unthrottle_peer.assert_not_awaited()

    # Cfg всё равно reset
    cfg = await get_config_by_id(cfg_id)
    assert cfg["status"] == "empty"
    # Sub всё равно expired
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"


# ── cache invalidate ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sub_cache_invalidated_on_grace_to_expired(fresh_db, db_with_server):
    """Audit F5: после grace→expired нужно invalidate sub_cache юзера —
    иначе Mini App до 2с после events будет показывать stale grace-banner
    вместо «подписка истекла, купите снова»."""
    sub_id = await _make_grace_sub(user_id=1100)
    await _make_active_awg(sub_id, db_with_server, user_id=1100,
                            peer_name="p_cache", assigned_ip="10.0.0.1100")

    mock_client = _mock_client()
    invalidated = []

    def _track_invalidate(uid):
        invalidated.append(uid)

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("services.sub_cache.invalidate", side_effect=_track_invalidate):
        from services.scheduler import _process_grace_expired_subscriptions
        await _process_grace_expired_subscriptions(_fake_bot())

    assert 1100 in invalidated, "sub_cache.invalidate должен быть вызван для юзера"
