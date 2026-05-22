"""
Multi-period upgrade guard tests (audit 23.05, BUG-2 / BUG-3).

Защищает handle_vpn_change_plan от двух связанных багов:

BUG-2: pro-rated формула `(new_rub - cur_rub) * remaining_days / 30`
       хардкодит знаменатель 30. Для multi-period планов (vpn_3m, vpn_base_3m
       и т.д.) расчёт ломается — в крайнем случае апгрейд из 90-дневного
       плана в 30-дневный «дороже-по-дню» уезжает в `max(1, -X) = 1₽`
       (общая цена длинного плана выше → разница отрицательная).

BUG-3: change_subscription_plan extends expires_at только при `status='grace'`.
       Юзер с активной 30-дневной подпиской апгрейдит в 90-дневный план →
       платит pro-rated за 3 месяца, получает остатки старой sub. То есть
       backend срезает купленный период до остатка предыдущего.

Frontend (Mini App) сейчас прячет multi-period планы в `VISIBLE_PLANS`,
но прямой POST с валидной initData (DevTools) обходит фильтр. Backend
guard закрывает exploit-сценарий независимо от фронта.

До правильной реализации multi-period upgrade'а (отдельный задел) —
любой 400 multi_period_unsupported.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import json
import pytest

from services.database import upsert_user, create_subscription


USER_ID = 7001
FUTURE = datetime.utcnow() + timedelta(days=30)


async def _make_sub(user_id: int = USER_ID, plan: str = "vpn_base") -> int:
    return await create_subscription(
        user_id=user_id, plan=plan,
        payment_id=f"chg_{user_id}_{plan}_{datetime.utcnow().timestamp()}",
        stars_paid=145, expires_at=FUTURE,
    )


def _make_request(body: dict) -> MagicMock:
    """Минимальный mock aiohttp Request. _resolve_user пропатчен отдельно,
    так что headers/initData не используется. request.json() async-returns body."""
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    req.headers = {}
    req.path = "/api/vpn/subscription/change"
    req.rel_url = MagicMock()
    req.rel_url.query = {}
    req.app = {"bot": MagicMock()}
    return req


async def _call_change_plan(plan_key: str):
    """Hits handle_vpn_change_plan with _resolve_user patched to USER_ID.
    Returns (status_code, body_dict)."""
    from services import webapp_api
    req = _make_request({"plan_key": plan_key})
    with patch.object(webapp_api, "_resolve_user", return_value={"id": USER_ID}):
        resp = await webapp_api.handle_vpn_change_plan(req)
    return resp.status, json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_guard_rejects_current_multi_period(fresh_db):
    """cur=vpn_3m (90 дней, legacy) → upgrade в vpn_max (30 дней).
    Без guard'а: pro-rated формула /30 underprice → 1₽ exploit.
    """
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(plan="vpn_3m")

    status, body = await _call_change_plan("vpn_max")

    assert status == 400, f"expected 400 multi_period_unsupported, got {status}: {body}"
    assert body.get("error") == "multi_period_unsupported", \
        f"expected error=multi_period_unsupported, got {body}"


@pytest.mark.asyncio
async def test_guard_rejects_target_multi_period(fresh_db):
    """cur=vpn_base (30 дней) → upgrade в vpn_max_3m (90 дней).
    Без guard'а: change_subscription_plan не extends expires_at для active sub →
    юзер платит за 3 месяца, получает остатки 30-дневной sub.
    """
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(plan="vpn_base")

    status, body = await _call_change_plan("vpn_max_3m")

    assert status == 400, f"expected 400 multi_period_unsupported, got {status}: {body}"
    assert body.get("error") == "multi_period_unsupported"


@pytest.mark.asyncio
async def test_guard_rejects_both_multi_period(fresh_db):
    """cur=vpn_base_3m → new=vpn_max_3m (оба 90 дней).
    Оба условия multi_period выполнены — guard всё равно отбивает,
    т.к. ни pro-rated formula, ни change_subscription_plan не готовы
    к таким парам.
    """
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(plan="vpn_base_3m")

    status, body = await _call_change_plan("vpn_max_3m")

    assert status == 400
    assert body.get("error") == "multi_period_unsupported"


@pytest.mark.asyncio
async def test_guard_passes_30d_to_30d(fresh_db):
    """cur=vpn_max → downgrade в vpn_base (оба 30 дней). Guard НЕ должен
    срабатывать; запрос уходит в downgrade-ветку (schedule_plan_change) и
    возвращает 200. Защита от регрессии в обратную сторону —
    «слишком жадный» guard сломал бы основной 30d→30d flow."""
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(plan="vpn_max")

    status, body = await _call_change_plan("vpn_base")

    # 200 (downgrade scheduled) — guard пропустил, обработка пошла дальше.
    assert status == 200, f"30d→30d downgrade should pass guard, got {status}: {body}"
    # Любая форма успеха: scheduled / cancelled / same — все НЕ multi_period.
    assert body.get("error") != "multi_period_unsupported"


@pytest.mark.asyncio
async def test_guard_same_plan_short_circuit_unchanged(fresh_db):
    """Same-plan early return (`{ok: True, same: True}`) выполняется ДО guard'а.
    Юзер на vpn_3m, пытается переключиться на vpn_3m → guard не должен
    мешать short-circuit'у. Иначе невинный «нажми сюда» лишний раз ломался бы
    у legacy юзеров на multi-period.
    """
    await upsert_user(USER_ID, "u", "U")
    await _make_sub(plan="vpn_3m")

    status, body = await _call_change_plan("vpn_3m")

    assert status == 200
    assert body.get("same") is True
    assert body.get("error") != "multi_period_unsupported"
