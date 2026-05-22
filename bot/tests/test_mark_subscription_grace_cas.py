"""
CAS-уровень: `mark_subscription_grace` атомарность.

ЗАЧЕМ ВАЖНО:
  Throttle-loop в `_process_expired_subscriptions` идёт ДО mark_grace
  (см. comment в scheduler.py:439 — «throttle ПЕРЕД mark_subscription_grace»),
  чтобы получить chance откатить throttle если sub успели продлить
  recurring webhook'ом / admin extend'ом за время throttle-loop'а
  (которая может тянуться секунды при N peer'ах × RTT до агента).

  CAS-guard «WHERE status='active' AND expires_at <= now()» гарантирует
  что мы НЕ перейдём в grace в двух кейсах:
    1) status уже не active (renew/refund/expired flow выиграл)
    2) expires_at был отодвинут в будущее (admin extend / recurring renew)
  Без guard'а мы бы получили inconsistent state: status='grace' но
  expires_at в будущем — UI рисует «эконом-режим», юзер пытается
  «продлить» уже продлённую sub, oops.

  Эти тесты идут MIMO непосредственно функции (не через scheduler) чтобы
  изолировать contract атомарного UPDATE и не зависеть от mock'ов агента.
"""
from datetime import datetime, timedelta

import pytest

from services.database import (
    upsert_user,
    create_subscription,
    mark_subscription_grace,
    mark_subscription_expired,
    get_subscription_by_id,
)


USER_ID = 11001


async def _make_sub(*, days_offset: float, status_after: str | None = None) -> int:
    """Создаёт sub с expires_at = now + days_offset.

    `status_after`: если None — остаётся 'active'.
                     'expired' → переводит mark_subscription_expired.
                     'grace' → mark_subscription_grace c past expires_at.
    """
    await upsert_user(USER_ID, "u", "U")
    expires_at = datetime.utcnow() + timedelta(days=days_offset)
    sub_id = await create_subscription(
        user_id=USER_ID, plan="vpn_base",
        payment_id=f"chg_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=expires_at,
    )
    if status_after == "expired":
        await mark_subscription_expired(sub_id)
    elif status_after == "grace":
        # mark_subscription_grace требует past expires_at — для fixture'а
        # нам это уже задано days_offset<0, так что просто помечаем.
        gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
        ok = await mark_subscription_grace(sub_id, gu)
        assert ok, "fixture: pre-mark to grace failed"
    return sub_id


# ── happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_with_past_expires_at_transitions_to_grace(fresh_db):
    """Канонический случай: scheduler находит истёкшую active sub и зовёт
    mark_grace → True, sub теперь в grace, grace_until заполнен."""
    sub_id = await _make_sub(days_offset=-1.0)  # истекла сутки назад

    grace_until = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(sub_id, grace_until)

    assert ok is True
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"
    assert sub["grace_until"] == grace_until


# ── CAS guard: статус не active ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_already_expired_sub_cannot_go_to_grace(fresh_db):
    """Sub уже expired (scheduler сам перевёл на прошлом тике, или admin
    закрыл вручную). mark_grace должен no-op'нуться, иначе мы бы оживляли
    «закрытую» подписку и юзер на неё бесплатно сидел 14 дней."""
    sub_id = await _make_sub(days_offset=-1.0, status_after="expired")

    gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(sub_id, gu)

    assert ok is False
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "expired", "expired sub must stay expired"
    assert sub["grace_until"] is None, "must not set grace_until on expired sub"


@pytest.mark.asyncio
async def test_already_grace_sub_does_not_re_transition(fresh_db):
    """Sub уже в grace (предыдущий tick scheduler'а перевёл). Повторный вызов
    mark_grace не должен «обновлять» grace_until — иначе scheduler-replay
    после restart'а сдвигал бы grace_until на каждый тик, давая бесконечный
    grace юзеру."""
    sub_id = await _make_sub(days_offset=-1.0, status_after="grace")
    sub_before = await get_subscription_by_id(sub_id)
    grace_until_before = sub_before["grace_until"]

    # Попытка перевести в grace второй раз с другим grace_until
    new_gu = (datetime.utcnow() + timedelta(days=99)).isoformat()
    ok = await mark_subscription_grace(sub_id, new_gu)

    assert ok is False
    sub_after = await get_subscription_by_id(sub_id)
    assert sub_after["grace_until"] == grace_until_before, (
        "повторный mark_grace не должен переписать grace_until — это сдвигало "
        "бы окончание grace на 14 дней с каждого scheduler tick'а"
    )


# ── CAS guard: expires_at в будущем ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_with_future_expires_at_does_not_go_to_grace(fresh_db):
    """RACE: scheduler начал throttle-loop, в середине recurring webhook /
    admin extend продлил sub (expires_at → now + 30д). После throttle-loop'а
    mark_grace должен НЕ срабатывать — иначе paying user окажется в grace
    с status='grace' но expires_at в будущем (UI breaks: «продлите подписку»
    хотя она УЖЕ продлена)."""
    sub_id = await _make_sub(days_offset=+30.0)  # active, истекает через 30д

    gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(sub_id, gu)

    assert ok is False
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "active", "active future-expiry sub must stay active"
    assert sub["grace_until"] is None


@pytest.mark.asyncio
async def test_active_with_expires_at_exactly_now_transitions(fresh_db):
    """Boundary: expires_at = «прямо сейчас» (datetime('now') в SQL).
    SQL guard: `datetime(expires_at) <= datetime('now')` — это <=, не <.
    Значит row на грани (expires_at == now) должна пройти.

    Тест fragile к точному совпадению timestamp'ов, поэтому добавляем 1 сек
    fudge factor в прошлое (datetime('now') в момент UPDATE будет позже)."""
    # 1 секунда в прошлом — гарантированно <= datetime('now') в момент UPDATE
    sub_id = await _make_sub(days_offset=-1.0 / 86400)  # ~1 сек назад

    gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(sub_id, gu)

    assert ok is True
    sub = await get_subscription_by_id(sub_id)
    assert sub["status"] == "grace"


# ── non-existent sub ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nonexistent_sub_returns_false(fresh_db):
    """sub_id = 99999, в БД его нет. UPDATE matches 0 rows, должен вернуть
    False без ошибок. Это защита от race с refund-cascade'ом который удалял
    sub (DELETE) в старых версиях — UPDATE на missing row просто no-op."""
    await upsert_user(USER_ID, "u", "U")

    gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(99999, gu)

    assert ok is False


# ── trial subs guard ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trial_sub_can_also_go_to_grace(fresh_db):
    """Замечание: `mark_subscription_grace` НЕ фильтрует по plan — таким
    образом vpn_trial теоретически тоже может пройти CAS. Это OK: триал в
    проде в grace не попадает потому что `get_expired_subscriptions`
    исключает plan='vpn_trial' (триал идёт в `get_expired_trials` → direct
    expired без grace). Но если кто-то когда-то вызовет mark_grace напрямую
    на trial sub — defensive guard'а здесь нет.

    Тест документирует это поведение чтобы будущая правка не сломала
    предположение «scheduler-trial-flow обходит grace на trial-уровне, не на
    DB-уровне». Если решат добавить guard в SQL — этот тест надо
    переписать на assert ok is False."""
    sub_id = await _make_sub(days_offset=-1.0)
    # Меняем plan на vpn_trial — симулируем что в БД триал
    import aiosqlite
    import services.database as _db_mod
    async with aiosqlite.connect(_db_mod.DB_PATH) as db:
        await db.execute("UPDATE subscriptions SET plan='vpn_trial' WHERE id=?", (sub_id,))
        await db.commit()

    gu = (datetime.utcnow() + timedelta(days=14)).isoformat()
    ok = await mark_subscription_grace(sub_id, gu)

    # Сейчас DB-уровень разрешает (фильтрация наверху). Если в будущем
    # сменится — тест надо обновить, чтобы зафиксировать новый contract.
    assert ok is True, (
        "DB-CAS контракт: mark_grace допускает trial. "
        "Фильтрация trial живёт в get_expired_subscriptions (plan filter)."
    )
