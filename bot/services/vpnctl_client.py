"""
Клиент для vpnctl агента.
Вызывается ботом при создании/удалении/suspend/resume пиров.
Поддерживает универсальный API: /services/{name}/peers
"""

import aiohttp
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

log = logging.getLogger(__name__)


@dataclass
class PeerResult:
    id: str              # peer ID (pubkey for WG, uuid for VLESS, etc.)
    label: str
    config: str          # connection config (WG .conf, AWG .conf, VLESS URL, etc.)
    extra: dict = None   # service-specific fields (assigned_ip, public_key, etc.)


class VpnctlError(Exception):
    pass


class VpnctlClient:
    def __init__(self, agent_url: str, agent_token: str):
        self.base = agent_url.rstrip("/")
        self.token = agent_token

    def _headers(self) -> dict:
        return {"X-Agent-Token": self.token, "Content-Type": "application/json"}

    async def health(self) -> dict:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.base}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status != 200:
                    raise VpnctlError(f"health check failed: {r.status}")
                return await r.json()

    async def list_services(self) -> list:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{self.base}/services",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status != 200:
                    raise VpnctlError(f"list_services failed: {r.status}")
                return await r.json()

    # ── Generic service API ────────────────────────────────────────────────────

    async def add_peer(self, service: str, label: str) -> PeerResult:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self.base}/services/{service}/peers",
                json={"label": label},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                if r.status != 200:
                    raise VpnctlError(f"add_peer({service}) failed: {data}")
                # New unified format
                if "id" in data:
                    return PeerResult(
                        id=data["id"],
                        label=data.get("label", label),
                        config=data.get("config", ""),
                        extra=data.get("extra"),
                    )
                # Old WG compat format
                if "peer" in data:
                    p = data["peer"]
                    return PeerResult(
                        id=p["public_key"],
                        label=p.get("label", label),
                        config=data.get("wg_config", ""),
                        extra={"public_key": p["public_key"], "assigned_ip": p.get("assigned_ip", "")},
                    )
                raise VpnctlError(f"unexpected response format: {data}")

    async def remove_peer(self, service: str, peer_id: str):
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{self.base}/services/{service}/peers/{self._enc(peer_id)}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status not in (200, 404):
                    raise VpnctlError(f"remove_peer({service}) failed: {r.status}")

    async def suspend_peer(self, service: str, peer_id: str):
        await self._put(f"/services/{service}/peers/{self._enc(peer_id)}/suspend")

    async def resume_peer(self, service: str, peer_id: str):
        await self._put(f"/services/{service}/peers/{self._enc(peer_id)}/resume")

    async def list_peers(self, service: str) -> list:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{self.base}/services/{service}/peers",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json()

    async def suspend_all(self, service: str, ids: list[str]):
        await self._post(f"/services/{service}/peers/suspend-all", {"ids": ids})

    async def resume_all(self, service: str, ids: list[str]):
        await self._post(f"/services/{service}/peers/resume-all", {"ids": ids})

    # ── Backward compat (WG-only) ──────────────────────────────────────────────

    async def add_wg_peer(self, label: str) -> PeerResult:
        return await self.add_peer("wg", label)

    async def remove_wg_peer(self, pubkey: str):
        await self.remove_peer("wg", pubkey)

    async def suspend_wg_peer(self, pubkey: str):
        await self.suspend_peer("wg", pubkey)

    async def resume_wg_peer(self, pubkey: str):
        await self.resume_peer("wg", pubkey)

    async def list_wg_peers(self) -> list:
        return await self.list_peers("wg")

    # ── helpers ────────────────────────────────────────────────────────────────

    async def _put(self, path: str):
        async with aiohttp.ClientSession() as s:
            async with s.put(
                f"{self.base}{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    raise VpnctlError(f"PUT {path} failed: {r.status}")

    async def _post(self, path: str, body: dict):
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self.base}{path}",
                json=body,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    raise VpnctlError(f"POST {path} failed: {r.status}")

    @staticmethod
    def _enc(peer_id: str) -> str:
        return quote(peer_id, safe='')


def client_for_server(server: dict) -> VpnctlClient:
    """Создаёт клиент из словаря сервера (из БД)."""
    if not server.get("agent_url") or not server.get("agent_token"):
        raise VpnctlError(f"Server {server['id']} has no agent configured")
    return VpnctlClient(server["agent_url"], server["agent_token"])


async def provision_peer(server: dict, label: str, protocol: str) -> PeerResult:
    """
    Создаёт пир на сервере через vpnctl.
    protocol: 'wg', 'awg', 'vless', etc. — maps to service name.
    """
    c = client_for_server(server)
    return await c.add_peer(protocol, label)


async def revoke_peer(server: dict, peer_id: str, protocol: str):
    """Удаляет пир с сервера."""
    try:
        c = client_for_server(server)
        await c.remove_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"revoke_peer error (server={server['id']}): {e}")


async def suspend_peer(server: dict, peer_id: str, protocol: str):
    """Приостанавливает пир."""
    try:
        c = client_for_server(server)
        await c.suspend_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"suspend_peer error: {e}")


async def resume_peer(server: dict, peer_id: str, protocol: str):
    """Возобновляет пир."""
    try:
        c = client_for_server(server)
        await c.resume_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"resume_peer error: {e}")