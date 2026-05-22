"""
Scheduler `_process_expired_subscriptions` — edge cases НЕ покрытые в
`test_scheduler_grace.py`.

Что уже там:
  - happy path AWG → grace + throttle
  - bot-offline guard (>14d expired) → direct expired
  - VLESS → grace + add_peer(vless-grace) + compensating remove
  - VLESS grace add_peer fail → no compensating remove

Что добавляем здесь:
  1. **Pending-downgrade ordering**: revoke-excess + apply_pending выполняются
     ДО throttle-loop'а (crash-safe ordering из scheduler.py:248-258).
  2. **Throttle-rollback race**: sub стала active mid-throttle → mark_grace
     возвращает False → scheduler ЗОВЁТ unthrottle для rollback.
  3. **Refund mid-throttle**: sub стала refunded → unthrottle всё равно
     вызывается (idempotent path для tc-filter cleanup).
  4. **AWG без assigned_ip**: data drift — throttle skip + admin alert.
  5. **Late-expire с server_id**: revoke_subscription_configs вызван перед
     mark_expired (peers ДОЛЖНЫ быть удалены).
  6. **Notification после grace transition**: bot.send_message с inline КБ.

Каждый сценарий — отдельный production bug в прошлом или защитная инварианта,
которая в `services.scheduler.py` baked-in to comments.
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
    schedule_plan_change,
    get_subscription_by_id,
)


PAST = (datetime.utcnow() - timedelta(days=1)).isoformat()
LONG_AGO = (datetime.utcnow() - timedelta(days=20)).isoformat()  # > GRACE_DAYS=14


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_with_server(fresh_db):
    """Test server с agent_url. Возвращает server_id."""
    async with aiosqlite.connect(fresh_db) as db:
        cur = await db.execute(
            """INSERT INTO servers (name, host, agent_url, agent_token, is_active, active_peers)
               VALUES ('Test', '1.2.3.4', 'http://agent:8080', 'tok', 1, 10)"""
        )
        await db.commit()
        return cur.lastrowid


async def _make_user_and_sub(*, user_id: int, expires_at: str,
                              plan: str = "vpn_base",
                              pending_plan: str | None = None) -> int:
    """Создаёт user + sub. Если pending_plan задан — schedule_plan_change."""
    await upsert_user(user_id, "u", "U")
    sub_id = await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"t_{user_id}_{plan}_{expires_at[:10]}",
        stars_paid=145, expires_at=datetime.fromisoformat(expires_at),
    )
    if pending_plan:
        await schedule_plan_change(sub_id, pending_plan)
    return sub_id


async def _make_active_awg(sub_id: int, server_id: int, *,
                            user_id: int,
                            peer_name: str = "p_awg",
                            assigned_ip: str = "10.0.0.2") -> int:
    cfg_id = await create_config_record(sub_id, user_id, protocol="awg",
                                         server_id=server_id)
    assert await claim_config_slot_for_activation(cfg_id), "claim failed"
    assert await activate_config_slot(
        cfg_id, peer_name=peer_name, config_data="[Interface]\n...",
        server_id=server_id, assigned_ip=assigned_ip,
    ), "activate CAS failed"
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
    client.add_peer = AsyncMock(return_value=MagicMock(config="service:vless-grace"))
    return client


# ── pending downgrade ordering ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_downgrade_applied_before_throttle_loop(
        fresh_db, db_with_server):
    """Юзер на vpn_max запланировал downgrade на vpn_base. Sub истекает →
    scheduler должен:
      1) revoke лишние configs ПО vpn_base количеству (revoke_excess)
      2) apply_pending_plan_change → plan теперь vpn_base
      3) throttle-loop с обновлённым plan_key

    Crash-safety: revoke ПЕРЕД apply (см. scheduler.py:248). Если crash между
    ними — pending_plan ещё стоит, next tick повторит обе операции (idempotent).
    Если бы порядок был apply→revoke, crash оставил бы lишние configs активными
    на новом тарифе (юзер платит vpn_base, получает vpn_max ресурсов)."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(
        user_id=42, expires_at=PAST,
        plan="vpn_max", pending_plan="vpn_base",
    )
    await _make_active_awg(sub_id, server_id, user_id=42,
                            peer_name="awg_42", assigned_ip="10.0.0.42")

    mock_client = _mock_client()
    revoke_calls = []

    # Перехватываем revoke_excess_configs_on_downgrade чтобы убедиться что
    # она вызвана с правильными аргументами (old_plan_key=vpn_max,
    # new_plan_key=vpn_base).
    async def _fake_revoke_excess(*args, **kwargs):
        revoke_calls.append((args, kwargs))
        return (0, 0)  # revoked=0, failed=0

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("services.revoke.revoke_excess_configs_on_downgrade",
                new=AsyncMock(side_effect=_fake_revoke_excess)) as mock_revoke:
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # revoke_excess вызвана с (old=vpn_max, new=vpn_base)
    assert mock_revoke.await_count == 1, "revoke_excess должна быть вызвана ровно 1 раз"
    kwargs = revoke_calls[0][1]
    assert kwargs.get("old_plan_key") == "vpn_max"
    assert kwargs.get("new_plan_key") == "vpn_base"

    # plan_key обновился в БД до vpn_base
    sub = await get_subscription_by_id(sub_id)
    assert sub["plan"] == "vpn_base", "apply_pending_plan_change должен был сработать"
    assert sub["pending_plan"] is None, "pending_plan сброшен после apply"
    assert sub["status"] == "grace"  # переход в grace всё равно произошёл


# ── throttle-rollback race ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unthrottle_rollback_when_sub_renewed_mid_throttle(
        fresh_db, db_with_server):
    """RACE: scheduler начал throttle-loop, recurring webhook продлил sub
    (status=active, expires_at в будущем) до того как scheduler дошёл до
    mark_subscription_grace. CAS откажет → scheduler ЗОВЁТ unthrottle_sub_configs
    для rollback'а throttle-операций.

    Без этого rollback'а paying user остался бы на 256 кбит/с навсегда
    (scheduler следующим тиком уже не возьмёт sub — она active с expires_at
    в будущем).

    Мы имитируем race через monkeypatch mark_subscription_grace: пока он
    «работает», DB уже изменена параллельным процессом."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(user_id=100, expires_at=PAST)
    await _make_active_awg(sub_id, server_id, user_id=100,
                            peer_name="p_race", assigned_ip="10.0.0.100")

    mock_client = _mock_client()
    unthrottle_called = []

    async def _race_mark_grace(sub_id_arg, grace_until_arg):
        """Имитируем race: пока scheduler звал mark_grace, webhook
        продлил sub. mark_grace CAS видит status='active' + future expires_at
        → возвращает False."""
        async with aiosqlite.connect(fresh_db) as db:
            await db.execute(
                "UPDATE subscriptions SET expires_at=datetime('now', '+30 days') "
                "WHERE id=?",
                (sub_id_arg,),
            )
            await db.commit()
        return False  # CAS отказал

    async def _track_unthrottle(*args, **kwargs):
        unthrottle_called.append((args, kwargs))

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("services.scheduler.mark_subscription_grace",
                new=AsyncMock(side_effect=_race_mark_grace)), \
         patch("services.grace.unthrottle_sub_configs",
                new=AsyncMock(side_effect=_track_unthrottle)) as mock_unthrottle:
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # Throttle всё ещё был вызван (это first step, ДО CAS)
    mock_client.throttle_peer.assert_awaited_once()
    # И UNTHROTTLE вызван для rollback'а
    assert mock_unthrottle.await_count == 1, (
        "race rollback должен ЗВАТЬ unthrottle_sub_configs — без этого "
        "paying user залип на slow tier навсегда"
    )
    # sub осталась active (race winner)
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_unthrottle_called_when_sub_refunded_mid_throttle(
        fresh_db, db_with_server):
    """RACE-вариант: sub refunded во время throttle-loop'а (юзер запросил
    refund, Stars/CryptoBot подтвердили). status='refunded' → mark_grace CAS
    откажет. Scheduler ЗОВЁТ unthrottle (idempotent path) для очистки
    tc-filter'а — он мог остаться по IP несмотря на пустой config_data
    после refund-каскада (refund→reset_config_slot, но tc осталось)."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(user_id=101, expires_at=PAST)
    await _make_active_awg(sub_id, server_id, user_id=101,
                            peer_name="p_refund", assigned_ip="10.0.0.101")

    mock_client = _mock_client()

    async def _race_to_refunded(sub_id_arg, _gu):
        async with aiosqlite.connect(fresh_db) as db:
            await db.execute(
                "UPDATE subscriptions SET status='refunded' WHERE id=?",
                (sub_id_arg,),
            )
            await db.commit()
        return False

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("services.scheduler.mark_subscription_grace",
                new=AsyncMock(side_effect=_race_to_refunded)), \
         patch("services.grace.unthrottle_sub_configs",
                new=AsyncMock()) as mock_unthrottle:
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # unthrottle всё равно вызван (idempotent cleanup)
    assert mock_unthrottle.await_count == 1


# ── AWG without assigned_ip ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awg_throttle_skipped_and_admin_alerted_when_no_assigned_ip(
        fresh_db, db_with_server):
    """Data drift: cfg.assigned_ip пустой (legacy миграция / прерванный
    provisioning). tc-filter на awg0 требует dst IP — без него throttle
    невозможен. scheduler.py:355-374 expects: log error + admin alert.

    Раньше silently skipped → юзер получал full speed бесплатно 14 дней
    grace. Audit 17.05 поймал."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(user_id=200, expires_at=PAST)
    # assigned_ip="" — data drift
    await _make_active_awg(sub_id, server_id, user_id=200,
                            peer_name="orphan_peer", assigned_ip="")

    mock_client = _mock_client()

    # ADMIN_ID не 0 — иначе admin-alert ветвь не сработает
    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("config.ADMIN_ID", new=12345):
        from services.scheduler import _process_expired_subscriptions
        bot = _fake_bot()
        await _process_expired_subscriptions(bot)

    # throttle_peer НЕ вызван (нет assigned_ip)
    mock_client.throttle_peer.assert_not_awaited()

    # Sub всё равно в grace (mark_grace не зависит от throttle success)
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"

    # Admin получил alert
    admin_messages = [
        c for c in bot.send_message.await_args_list
        if c.args and c.args[0] == 12345
    ]
    assert len(admin_messages) >= 1, "admin должен получить alert о data drift"
    text = admin_messages[0].args[1]
    assert "AWG" in text and "SKIPPED" in text


# ── late-expire (>GRACE_DAYS) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_late_expire_revokes_peers_via_revoke_subscription_configs(
        fresh_db, db_with_server):
    """Sub expired >14 дней назад (бот лежал в проде) → грейс пропускается,
    но peers ВСЕ ЕЩЁ активны на агенте (никто не revoke'нул их во время
    downtime'а). scheduler ДОЛЖЕН revoke их через revoke_subscription_configs
    ДО mark_expired — иначе юзер получит бесплатный VPN навсегда (sub expired
    в БД, peers active на агенте).

    Защитная инварианта от scheduler.py:223-232: revoke_subscription_configs
    вызывается ПЕРЕД mark_subscription_expired."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(user_id=300, expires_at=LONG_AGO)
    await _make_active_awg(sub_id, server_id, user_id=300,
                            peer_name="zombie", assigned_ip="10.0.0.300")

    mock_client = _mock_client()
    revoke_calls = []

    async def _track_revoke(*args, **kwargs):
        revoke_calls.append((args, kwargs))
        return (1, 0)

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled", new=AsyncMock()), \
         patch("services.revoke.revoke_subscription_configs",
                new=AsyncMock(side_effect=_track_revoke)) as mock_revoke:
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # revoke_subscription_configs вызван (это late-expire branch)
    assert mock_revoke.await_count == 1
    # Sub перешла в expired (НЕ grace — late-expire bypass)
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired"
    # throttle НЕ вызван (late-expire skip)
    mock_client.throttle_peer.assert_not_awaited()


# ── user notification after grace transition ──────────────────────────────────

@pytest.mark.asyncio
async def test_user_gets_grace_notice_with_renew_button(fresh_db, db_with_server):
    """После успешного перехода в grace юзер получает уведомление
    (`bot_grace_notice`) с inline-кнопкой «Продлить». Без этого retention
    проседает — юзер не знает что VPN на throttle и через 14 дней закроется."""
    server_id = db_with_server
    sub_id = await _make_user_and_sub(user_id=400, expires_at=PAST)
    await _make_active_awg(sub_id, server_id, user_id=400,
                            peer_name="p_notif", assigned_ip="10.0.0.400")

    mock_client = _mock_client()
    sent_calls = []

    async def _track_send(bot, user_id, text, **kwargs):
        sent_calls.append({"user_id": user_id, "text": text, "kwargs": kwargs})
        return True

    with patch("services.scheduler.client_for_server", return_value=mock_client), \
         patch("services.scheduler._send_throttled",
                new=AsyncMock(side_effect=_track_send)):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())

    # Юзер получил уведомление
    user_msgs = [c for c in sent_calls if c["user_id"] == 400]
    assert len(user_msgs) == 1, "юзер должен получить ровно 1 уведомление о grace"
    # с reply_markup (inline keyboard «Продлить»)
    assert user_msgs[0]["kwargs"].get("reply_markup") is not None, (
        "grace notice должен иметь inline keyboard с CTA на продление — "
        "без него retention падает"
    )


# ── no expired = noop ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runs_without_servers_table_state(fresh_db):
    """No expired subs, no servers — scheduler не падает. Минимальная sanity
    проверка что функция терпима к пустому состоянию (fresh install)."""
    with patch("services.scheduler._send_throttled", new=AsyncMock()):
        from services.scheduler import _process_expired_subscriptions
        await _process_expired_subscriptions(_fake_bot())  # must not raise
