"""
Клиент для vpnctl агента.
Вызывается ботом при создании/удалении/suspend/resume пиров.
"""

import aiohttp
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class PeerResult:
    public_key: str
    assigned_ip: str
    label: str
    wg_config: str        # готовый .conf файл (только для WG)
    vless_url: str = ""   # vless://... (только для VLess)


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

    # ── WireGuard ──────────────────────────────────────────────────────────────

    async def add_wg_peer(self, label: str) -> PeerResult:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self.base}/peers",
                json={"label": label},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                if r.status != 200:
                    raise VpnctlError(f"add_wg_peer failed: {data}")
                return PeerResult(
                    public_key=data["peer"]["public_key"],
                    assigned_ip=data["peer"]["assigned_ip"],
                    label=data["peer"]["label"],
                    wg_config=data["wg_config"],
                )

    async def remove_wg_peer(self, pubkey: str):
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{self.base}/peers/{pubkey}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status not in (200, 404):
                    raise VpnctlError(f"remove_wg_peer failed: {r.status}")

    async def suspend_wg_peer(self, pubkey: str):
        await self._put(f"/peers/{pubkey}/suspend")

    async def resume_wg_peer(self, pubkey: str):
        await self._put(f"/peers/{pubkey}/resume")

    async def suspend_all_wg(self, pubkeys: list[str]):
        await self._post("/peers/suspend-all", {"pubkeys": pubkeys})

    async def resume_all_wg(self, pubkeys: list[str]):
        await self._post("/peers/resume-all", {"pubkeys": pubkeys})

    async def list_wg_peers(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{self.base}/peers",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json()

    # ── VLess ──────────────────────────────────────────────────────────────────

    async def add_vless_user(self, label: str) -> PeerResult:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self.base}/vless/users",
                json={"label": label},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                if r.status != 200:
                    raise VpnctlError(f"add_vless_user failed: {data}")
                return PeerResult(
                    public_key=data["uuid"],
                    assigned_ip="",
                    label=label,
                    wg_config="",
                    vless_url=data["vless_url"],
                )

    async def remove_vless_user(self, uuid: str):
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{self.base}/vless/users/{uuid}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status not in (200, 404):
                    raise VpnctlError(f"remove_vless_user failed: {r.status}")

    async def suspend_vless_user(self, uuid: str):
        await self._put(f"/vless/users/{uuid}/suspend")

    async def resume_vless_user(self, uuid: str):
        await self._put(f"/vless/users/{uuid}/resume")

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


def client_for_server(server: dict) -> VpnctlClient:
    """Создаёт клиент из словаря сервера (из БД)."""
    if not server.get("agent_url") or not server.get("agent_token"):
        raise VpnctlError(f"Server {server['id']} has no agent configured")
    return VpnctlClient(server["agent_url"], server["agent_token"])


async def provision_peer(server: dict, label: str, protocol: str) -> PeerResult:
    """
    Создаёт пир на сервере через vpnctl.
    protocol: 'awg' или 'vless'
    """
    c = client_for_server(server)
    if protocol == "vless":
        return await c.add_vless_user(label)
    else:
        return await c.add_wg_peer(label)


async def revoke_peer(server: dict, wg_pubkey: str | None,
                       vless_uuid: str | None, protocol: str):
    """Удаляет пир с сервера."""
    try:
        c = client_for_server(server)
        if protocol == "vless" and vless_uuid:
            await c.remove_vless_user(vless_uuid)
        elif wg_pubkey:
            await c.remove_wg_peer(wg_pubkey)
    except Exception as e:
        log.warning(f"revoke_peer error (server={server['id']}): {e}")


async def suspend_peer(server: dict, wg_pubkey: str | None,
                        vless_uuid: str | None, protocol: str):
    """Приостанавливает пир."""
    try:
        c = client_for_server(server)
        if protocol == "vless" and vless_uuid:
            await c.suspend_vless_user(vless_uuid)
        elif wg_pubkey:
            await c.suspend_wg_peer(wg_pubkey)
    except Exception as e:
        log.warning(f"suspend_peer error: {e}")


async def resume_peer(server: dict, wg_pubkey: str | None,
                       vless_uuid: str | None, protocol: str):
    """Возобновляет пир."""
    try:
        c = client_for_server(server)
        if protocol == "vless" and vless_uuid:
            await c.resume_vless_user(vless_uuid)
        elif wg_pubkey:
            await c.resume_wg_peer(wg_pubkey)
    except Exception as e:
        log.warning(f"resume_peer error: {e}")
