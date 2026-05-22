"""
Regression test for BUG-1 (audit 2026-05-23, scenario flow #1 — purchase):

Транспортные ошибки aiohttp/asyncio в `VpnctlClient._request()` должны
оборачиваться в `VpnctlError`, чтобы per-slot try/except VpnctlError в
provision-циклах (handlers/vpn.py:_deliver_vpn и provision_vpn_slots_async)
ловил их единообразно.

До фикса `asyncio.TimeoutError` / `aiohttp.ServerDisconnectedError` пробивали
сквозь per-slot guard, exception доходил до webhook-caller'а который
сбрасывал `delivered=0` и помечал sub expired — при этом уже созданные на
агенте пиры оставались orphan'ами + соответствующие `configs.status='active'`
в БД для expired sub. `reconcile_active_peers_counters` их не подбирал,
`get_partial_refunds` искал только `status='refunded'`.
"""
import asyncio
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from services.vpnctl_client import VpnctlClient, VpnctlError


class _RaisingRequestCM:
    """async-context-manager заглушка: на __aenter__ бросает заданное исключение.

    aiohttp.ClientSession.request() возвращает CM, а не корутину; ошибки коннекта
    обычно вылетают на входе в `async with`, поэтому реалистичнее всего
    имитировать их именно так.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _client_with_failing_session(exc: BaseException) -> VpnctlClient:
    """Сконструировать VpnctlClient так, чтобы любой HTTP-вызов падал с `exc`."""
    fake_session = MagicMock()
    fake_session.request = MagicMock(return_value=_RaisingRequestCM(exc))
    # _get_session() — модульная функция; patch'им чтобы вернуть наш fake.
    patcher = patch("services.vpnctl_client._get_session", return_value=fake_session)
    patcher.start()
    client = VpnctlClient("http://agent.test:8080", "test-token")
    client.__test_patcher = patcher  # type: ignore[attr-defined]
    return client


def _stop(client: VpnctlClient) -> None:
    patcher = getattr(client, "__test_patcher", None)
    if patcher is not None:
        patcher.stop()


@pytest.mark.asyncio
async def test_timeout_error_wrapped_as_vpnctl_error():
    """asyncio.TimeoutError → VpnctlError (а не сквозной TimeoutError)."""
    client = _client_with_failing_session(asyncio.TimeoutError())
    try:
        with pytest.raises(VpnctlError) as exc_info:
            await client.add_peer("awg", "test-label")
        # Сообщение содержит тип оригинальной ошибки — для удобства логов.
        assert "TimeoutError" in str(exc_info.value)
    finally:
        _stop(client)


@pytest.mark.asyncio
async def test_connector_error_wrapped_as_vpnctl_error():
    """aiohttp.ClientConnectorError (agent down) → VpnctlError."""
    # ClientConnectorError требует connection_key + OSError — собираем минимальный.
    conn_key = MagicMock()
    conn_key.ssl = False
    conn_key.host = "agent.test"
    conn_key.port = 8080
    os_err = OSError(111, "Connection refused")
    client = _client_with_failing_session(
        aiohttp.ClientConnectorError(conn_key, os_err)
    )
    try:
        with pytest.raises(VpnctlError) as exc_info:
            await client.add_peer("awg", "test-label")
        assert "ClientConnectorError" in str(exc_info.value)
    finally:
        _stop(client)


@pytest.mark.asyncio
async def test_server_disconnected_wrapped_as_vpnctl_error():
    """aiohttp.ServerDisconnectedError (TCP RST mid-request) → VpnctlError."""
    client = _client_with_failing_session(aiohttp.ServerDisconnectedError())
    try:
        with pytest.raises(VpnctlError) as exc_info:
            await client.add_peer("awg", "test-label")
        assert "ServerDisconnectedError" in str(exc_info.value)
    finally:
        _stop(client)


@pytest.mark.asyncio
async def test_cancelled_error_propagates_as_is():
    """asyncio.CancelledError — lifecycle событие, должно проходить
    сквозь _request без обёртки. Иначе при graceful shutdown'е/cancel'е
    coroutine'ы провижинга будут видеть VpnctlError и думать что упал
    запрос к агенту.
    На Python 3.8+ CancelledError = BaseException, и `except Exception`
    его не ловит — этот тест защищает от регрессии (кто-то напишет
    `except BaseException` в _request)."""
    client = _client_with_failing_session(asyncio.CancelledError())
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.add_peer("awg", "test-label")
    finally:
        _stop(client)
