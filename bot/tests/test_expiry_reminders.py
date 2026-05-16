"""
Expiry-reminder dedup tests.

Two layers:

1. DB-layer (get_subscriptions_expiring_soon / mark_reminded) — window
   queries and idempotency.

2. Scheduler (_send_expiry_reminders) — verifies bot.send_message is called
   once per sub per day-bucket, trial gets only the 1-day reminder, and
   replaying the scheduler does NOT re-send.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from services.database import (
    upsert_user,
    create_subscription,
    get_subscriptions_expiring_soon,
    mark_reminded,
    get_subscription_by_id,
)


# ── helpers ───────────────────────────────────────────────────────────────────

USER_ID = 7001


async def _make_sub(*, days_until_expiry: float, plan: str = "vpn_base") -> int:
    """Creates an active sub with expires_at = now + days."""
    expires_at = datetime.utcnow() + timedelta(days=days_until_expiry)
    return await create_subscription(
        user_id=USER_ID, plan=plan,
        payment_id=f"chg_{plan}_{days_until_expiry}_{datetime.utcnow().timestamp()}",
        stars_paid=145 if plan != "vpn_trial" else 0,
        expires_at=expires_at,
    )


# ── DB-layer: window query ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sub_expiring_in_2_5_days_appears_in_3day_query(fresh_db):
    """Sub истекает через 2.5 дня → попадает в 3-day reminder window (2-3 дня)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=2.5)

    subs = await get_subscriptions_expiring_soon(3)
    assert any(s["id"] == sub_id for s in subs)


@pytest.mark.asyncio
async def test_sub_expiring_in_0_5_days_appears_in_1day_query(fresh_db):
    """Sub истекает через 12 часов → попадает в 1-day window (0-1 дня)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=0.5)

    subs = await get_subscriptions_expiring_soon(1)
    assert any(s["id"] == sub_id for s in subs)


@pytest.mark.asyncio
async def test_sub_expiring_in_5_days_not_in_3day_query(fresh_db):
    """Sub до истечения далеко → не в 3-day window."""
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(days_until_expiry=5.0)
    subs = await get_subscriptions_expiring_soon(3)
    assert subs == []


@pytest.mark.asyncio
async def test_sub_expiring_in_2_days_not_in_1day_query(fresh_db):
    """Sub в 3-day window не должен попадать в 1-day window (буквальные границы)."""
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(days_until_expiry=2.0)
    subs = await get_subscriptions_expiring_soon(1)
    assert subs == []


@pytest.mark.asyncio
async def test_expired_sub_not_in_reminder_query(fresh_db):
    """Sub уже истекшая → status='active' но expires_at в прошлом → не reminder."""
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(days_until_expiry=-1.0)
    subs_3d = await get_subscriptions_expiring_soon(3)
    subs_1d = await get_subscriptions_expiring_soon(1)
    assert subs_3d == subs_1d == []


# ── DB-layer: mark_reminded dedup ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_reminded_3d_excludes_sub_from_3day_query(fresh_db):
    """После mark_reminded(sub_id, 3) — sub НЕ возвращается из 3-day query."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=2.5)
    assert any(s["id"] == sub_id for s in await get_subscriptions_expiring_soon(3))

    await mark_reminded(sub_id, 3)

    subs = await get_subscriptions_expiring_soon(3)
    assert not any(s["id"] == sub_id for s in subs)


@pytest.mark.asyncio
async def test_mark_reminded_3d_does_not_block_1d_reminder(fresh_db):
    """3-day reminder marked → sub всё ещё может получить 1-day reminder
    (это разные колонки, разные напоминания)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=0.5)
    await mark_reminded(sub_id, 3)

    subs = await get_subscriptions_expiring_soon(1)
    assert any(s["id"] == sub_id for s in subs), (
        "1-day reminder колонка независима от 3-day"
    )


@pytest.mark.asyncio
async def test_mark_reminded_is_idempotent(fresh_db):
    """Повторный mark_reminded не падает и не ломает значение (idempotent UPDATE)."""
    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=2.5)

    await mark_reminded(sub_id, 3)
    await mark_reminded(sub_id, 3)  # no-op
    await mark_reminded(sub_id, 1)

    subs_3d = await get_subscriptions_expiring_soon(3)
    subs_1d = await get_subscriptions_expiring_soon(1)
    assert not any(s["id"] == sub_id for s in subs_3d)
    assert not any(s["id"] == sub_id for s in subs_1d)


# ── Scheduler integration ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_sends_3day_reminder_once(fresh_db):
    """_send_expiry_reminders: sub в 3-day window → bot.send_message один раз,
    подписка помечена reminded_3d=1."""
    from services.scheduler import _send_expiry_reminders
    from unittest.mock import MagicMock

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=2.5)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    from unittest.mock import patch
    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)

    send_t.assert_awaited_once()
    # Проверяем dedup через DB
    subs = await get_subscriptions_expiring_soon(3)
    assert not any(s["id"] == sub_id for s in subs)


@pytest.mark.asyncio
async def test_scheduler_does_not_resend_on_replay(fresh_db):
    """Второй вызов _send_expiry_reminders — НЕ должен отправлять второе сообщение."""
    from services.scheduler import _send_expiry_reminders
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    await _make_sub(days_until_expiry=2.5)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)
        first_count = send_t.await_count
        await _send_expiry_reminders(bot)  # replay
        second_count = send_t.await_count

    assert first_count == 1
    assert second_count == 1, "Replay must not send a second reminder"


@pytest.mark.asyncio
async def test_scheduler_trial_3day_reminder_silently_marked(fresh_db):
    """Триал в 3-day window: bot.send_message НЕ вызывается (бесполезный спам:
    триал=3 дня, 3-day напоминание попало бы сразу после активации),
    но reminded_3d=1 проставляется чтобы потом не пытаться снова."""
    from services.scheduler import _send_expiry_reminders
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=2.5, plan="vpn_trial")

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)

    send_t.assert_not_awaited()
    # Но reminded_3d=1, чтобы при следующем тике не пытаться снова
    subs = await get_subscriptions_expiring_soon(3)
    assert not any(s["id"] == sub_id for s in subs)


@pytest.mark.asyncio
async def test_scheduler_trial_1day_reminder_sent(fresh_db):
    """Триал в 1-day window: reminder ОТПРАВЛЯЕТСЯ (главный конверсионный момент)."""
    from services.scheduler import _send_expiry_reminders
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    await _make_sub(days_until_expiry=0.5, plan="vpn_trial")

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)

    send_t.assert_awaited_once()
    text = send_t.await_args.args[2]  # (bot, user_id, text, ...)
    assert "триал" in text.lower() or "24 часа" in text.lower()


# ── Grace-reminder: 3 дня до конца grace_until ────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_grace_reminder_sent_3d_before_end(fresh_db):
    """Sub в grace с grace_until через 2.5 дня → reminder уходит, flag ставится."""
    from services.scheduler import _send_expiry_reminders
    from services.database import (
        mark_subscription_grace, get_subscriptions_grace_ending_soon,
    )
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=-1.0)  # уже истекла
    grace_end = (datetime.utcnow() + timedelta(days=2.5)).isoformat()
    await mark_subscription_grace(sub_id, grace_end)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)

    send_t.assert_awaited_once()
    text = send_t.await_args.args[2]
    # Текст про закрытие через 3 дня + что-то про продление
    assert "3 дня" in text or "отключится" in text.lower()
    # Не должен попасть второй раз
    subs = await get_subscriptions_grace_ending_soon(3)
    assert not any(s["id"] == sub_id for s in subs), "должен быть reminded_grace_3d=1"


@pytest.mark.asyncio
async def test_scheduler_grace_reminder_skips_if_far_from_end(fresh_db):
    """Sub в grace с grace_until через 10 дней (только начал grace) →
    reminder НЕ должен слать, ждём пока приблизится к концу."""
    from services.scheduler import _send_expiry_reminders
    from services.database import mark_subscription_grace
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=-1.0)
    grace_end = (datetime.utcnow() + timedelta(days=10)).isoformat()
    await mark_subscription_grace(sub_id, grace_end)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)

    send_t.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_grace_reminder_no_replay(fresh_db):
    """Уже отправленный grace-reminder не повторяется на следующем тике."""
    from services.scheduler import _send_expiry_reminders
    from services.database import mark_subscription_grace
    from unittest.mock import MagicMock, patch

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=-1.0)
    grace_end = (datetime.utcnow() + timedelta(days=2.5)).isoformat()
    await mark_subscription_grace(sub_id, grace_end)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.scheduler._send_throttled", new=AsyncMock()) as send_t:
        await _send_expiry_reminders(bot)
        first = send_t.await_count
        await _send_expiry_reminders(bot)  # replay
        second = send_t.await_count

    assert first == 1
    assert second == 1, "replay must not re-send grace reminder"


# ── renew_subscription_from_grace ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_renew_from_grace_restores_active_and_resets_reminders(fresh_db):
    """Sub в grace + renew → status='active', grace_until=NULL, expires_at +30д,
    reminded_*-флаги сброшены (новый период → новые напоминания должны идти)."""
    from services.database import (
        mark_subscription_grace, mark_reminded, mark_grace_reminded,
        renew_subscription_from_grace, get_subscription_by_id,
    )
    import aiosqlite

    await upsert_user(USER_ID, "u", "U")
    sub_id = await _make_sub(days_until_expiry=-1.0)
    await mark_subscription_grace(sub_id, (datetime.utcnow() + timedelta(days=5)).isoformat())
    await mark_reminded(sub_id, 3)
    await mark_reminded(sub_id, 1)
    await mark_grace_reminded(sub_id)

    result = await renew_subscription_from_grace(sub_id, days=30)
    assert result is not None
    assert result["status"] == "active"

    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active"
    assert sub["grace_until"] is None
    expires = datetime.fromisoformat(sub["expires_at"].replace(" ", "T"))
    days_ahead = (expires - datetime.utcnow()).total_seconds() / 86400
    assert 29 < days_ahead < 31

    # Flags сброшены — за 3 дня до новой даты истечения reminder снова пойдёт
    async with aiosqlite.connect(fresh_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT reminded_3d, reminded_1d, reminded_grace_3d FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = dict(await cur.fetchone())
    assert row["reminded_3d"] == 0
    assert row["reminded_1d"] == 0
    assert row["reminded_grace_3d"] == 0
