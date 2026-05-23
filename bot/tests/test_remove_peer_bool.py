"""Foundation tests для `VpnctlClient.remove_peer` bool-return contract.

Audit 2026-05-23 round-2 изменил signature: было `-> None` (raise на не-200/404),
стало `-> bool` (True если 200/204, False если 404, raise иначе). Этот контракт
позволяет caller'ам идемпотентно retry'ить remove_peer без double-decrement
счётчика `servers.active_peers` (см. `_process_orphan_active_configs`,
`revoke_subscription_configs`, `_close_dangling_grace` и т.д.).
"""
from unittest.mock import AsyncMock, patch

import pytest

from services.vpnctl_client import VpnctlClient, VpnctlError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (200, True),   # стандартный success
        (204, True),   # No Content тоже success
        (404, False),  # peer уже не было — caller НЕ должен декрементить counter
    ],
)
async def test_remove_peer_status_to_bool(status, expected):
    client = VpnctlClient("http://agent.test", "tok")
    with patch.object(client, "_request", AsyncMock(return_value=(status, None))):
        result = await client.remove_peer("awg", "peer-id-1")
    assert result is expected, (
        f"remove_peer должен вернуть {expected} для HTTP {status}, "
        f"чтобы caller мог идемпотентно decide про counter decrement"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 503])
async def test_remove_peer_raises_on_other_errors(status):
    """Любой не-(200/204/404) → VpnctlError. Идемпотентный retry строится
    только вокруг 404; transient errors caller должен сам обработать."""
    client = VpnctlClient("http://agent.test", "tok")
    with patch.object(client, "_request", AsyncMock(return_value=(status, None))):
        with pytest.raises(VpnctlError) as exc_info:
            await client.remove_peer("awg", "peer-id-1")
    assert str(status) in str(exc_info.value)
