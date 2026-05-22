"""
Regression: handle_vpn_invoice (services/webapp_api.py) отбивает 400
для legacy plan_keys (vpn_pro/family/popular/start/1m/3m/1y).

Legacy планы помечены `"legacy": True` в plans.py — UI Plans.tsx их скрывает,
но прямой POST /api/vpn/invoice с initData мог создать legacy invoice
(особенно recurring=True для 30-day legacy plans — Stars запустил бы
subscription, бот потом обрабатывал legacy plan-key для всех будущих чарджей).
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import web

from services.webapp_api import handle_vpn_invoice


@pytest_asyncio.fixture
async def app_client(fresh_db, aiohttp_client, monkeypatch):
    """aiohttp test app + monkeypatch _resolve_user чтобы не проверять initData."""
    import services.webapp_api as wapi
    # Bypass initData verification — мы тестируем business logic legacy-guard,
    # не auth flow.
    monkeypatch.setattr(
        wapi, "_resolve_user",
        lambda req, body=None: {"id": 7777},
    )
    # Bypass ban check
    monkeypatch.setattr(
        wapi, "_check_banned",
        AsyncMock(return_value=False),
    )
    # Bypass rate-limit (in-memory)
    monkeypatch.setattr(
        wapi, "_rate_limit_check_evict",
        lambda *a, **kw: True,
    )
    # Stub Bot — handler делает bot.create_invoice_link (мы до неё не доходим
    # в legacy-кейсах, но в no-legacy кейсах нужен).
    fake_bot = MagicMock()
    fake_bot.create_invoice_link = AsyncMock(return_value="https://t.me/invoice/abc")

    app = web.Application()
    app["bot"] = fake_bot
    app.router.add_post("/api/vpn/invoice", handle_vpn_invoice)
    return await aiohttp_client(app)


# ── legacy plans rejected ────────────────────────────────────────────────────

@pytest.mark.parametrize("legacy_plan", [
    "vpn_pro", "vpn_family", "vpn_popular", "vpn_start",
    "vpn_1m", "vpn_3m", "vpn_1y",
])
async def test_legacy_plan_returns_400(app_client, legacy_plan):
    """Все legacy plan_keys должны отлететь 400 с error=legacy_plan."""
    resp = await app_client.post(
        "/api/vpn/invoice",
        json={"plan_key": legacy_plan},
    )
    assert resp.status == 400, (
        f"legacy plan {legacy_plan} должен отбиваться, got {resp.status}"
    )
    data = await resp.json()
    assert data.get("error") == "legacy_plan", (
        f"error code должен быть 'legacy_plan', got {data}"
    )


# ── non-legacy plans pass through ────────────────────────────────────────────

@pytest.mark.parametrize("normal_plan", [
    "vpn_base", "vpn_max",
])
async def test_normal_plan_creates_invoice(app_client, normal_plan):
    """Не-legacy планы НЕ должны отлетать на legacy-guard — handler доходит
    до create_invoice_link и возвращает 200 + invoice_url."""
    resp = await app_client.post(
        "/api/vpn/invoice",
        json={"plan_key": normal_plan},
    )
    assert resp.status == 200, f"normal plan {normal_plan} failed: {resp.status}"
    data = await resp.json()
    assert "invoice_url" in data


# ── unknown plan rejected (existing behavior) ───────────────────────────────

async def test_unknown_plan_returns_400(app_client):
    resp = await app_client.post(
        "/api/vpn/invoice",
        json={"plan_key": "vpn_made_up_xyz"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data.get("error") == "Unknown plan"
