"""
Клиент для vpnctl агента.
Каждый запрос подписан HMAC-SHA256 (заголовок X-Agent-Sig: ts.hex(hmac)),
плюс совместимость с legacy X-Agent-Token (агент принимает любой из двух).
"""

import hashlib
import hmac as _hmac
import json as _json
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import aiohttp

log = logging.getLogger(__name__)


@dataclass
class PeerResult:
    id: str
    label: str
    config: str
    extra: dict = None


class VpnctlError(Exception):
    pass


class VpnctlClient:
    def __init__(self, agent_url: str, agent_token: str):
        self.base = agent_url.rstrip("/")
        self.token = agent_token

    # ── HMAC подпись ───────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: bytes = b"") -> dict:
        ts = str(int(time.time()))
        msg = f"{ts}:{method}{path}:".encode() + body
        sig = _hmac.new(self.token.encode(), msg, hashlib.sha256).hexdigest()
        return {
            "X-Agent-Sig":   f"{ts}.{sig}",
            "X-Agent-Token": self.token,  # legacy fallback
            "Content-Type":  "application/json",
        }

    @staticmethod
    def _path(url: str) -> str:
        return urlsplit(url).path or "/"

    async def _request(
        self, method: str, path: str, body: dict | None = None, *, timeout_s: int = 30,
    ) -> tuple[int, dict | list | None]:
        body_bytes = b"" if body is None else _json.dumps(body, separators=(",", ":")).encode()
        headers = self._sign(method, path, body_bytes)
        url = f"{self.base}{path}"
        async with aiohttp.ClientSession() as s:
            async with s.request(
                method, url,
                data=body_bytes if body_bytes else None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as r:
                ctype = r.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    data = await r.json()
                else:
                    data = await r.text()
                return r.status, data

    # ── Public API ─────────────────────────────────────────────────────────────

    async def health(self) -> dict:
        # /health не требует auth, но всё равно подпишем — не повредит
        st, data = await self._request("GET", "/health", timeout_s=10)
        if st != 200:
            raise VpnctlError(f"health: {st}")
        return data

    async def list_services(self) -> list:
        st, data = await self._request("GET", "/services", timeout_s=10)
        if st != 200:
            raise VpnctlError(f"list_services: {st}")
        return data

    async def add_peer(self, service: str, label: str, *, peer_id: str | None = None) -> PeerResult:
        body: dict = {"label": label}
        if peer_id:
            body["id"] = peer_id
        st, data = await self._request("POST", f"/services/{service}/peers", body)
        if st != 200:
            raise VpnctlError(f"add_peer({service}): {data}")
        if isinstance(data, dict) and "id" in data:
            return PeerResult(
                id=data["id"], label=data.get("label", label),
                config=data.get("config", ""), extra=data.get("extra"),
            )
        if isinstance(data, dict) and "peer" in data:
            p = data["peer"]
            return PeerResult(
                id=p["public_key"], label=p.get("label", label),
                config=data.get("wg_config", ""),
                extra={"public_key": p["public_key"], "assigned_ip": p.get("assigned_ip", "")},
            )
        raise VpnctlError(f"unexpected response: {data}")

    async def remove_peer(self, service: str, peer_id: str):
        st, _ = await self._request(
            "DELETE", f"/services/{service}/peers/{quote(peer_id, safe='')}"
        )
        if st not in (200, 404):
            raise VpnctlError(f"remove_peer({service}): {st}")

    async def suspend_peer(self, service: str, peer_id: str):
        await self._request("PUT", f"/services/{service}/peers/{quote(peer_id, safe='')}/suspend")

    async def resume_peer(self, service: str, peer_id: str):
        await self._request("PUT", f"/services/{service}/peers/{quote(peer_id, safe='')}/resume")

    async def list_peers(self, service: str) -> list:
        st, data = await self._request("GET", f"/services/{service}/peers")
        return data if isinstance(data, list) else []

    async def suspend_all(self, service: str, ids: list[str]):
        await self._request("POST", f"/services/{service}/peers/suspend-all", {"ids": ids})

    async def resume_all(self, service: str, ids: list[str]):
        await self._request("POST", f"/services/{service}/peers/resume-all", {"ids": ids})

    async def sync_active_ids(self, service: str, valid_ids: list[str]) -> dict:
        st, data = await self._request(
            "POST", f"/services/{service}/sync", {"valid_ids": valid_ids}, timeout_s=15,
        )
        if st != 200:
            raise VpnctlError(f"sync({service}): {data}")
        return data

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


def client_for_server(server: dict) -> VpnctlClient:
    if not server.get("agent_url") or not server.get("agent_token"):
        raise VpnctlError(f"Server {server['id']} has no agent configured")
    return VpnctlClient(server["agent_url"], server["agent_token"])


async def provision_peer(server: dict, label: str, protocol: str) -> PeerResult:
    return await client_for_server(server).add_peer(protocol, label)


async def revoke_peer(server: dict, peer_id: str, protocol: str):
    try:
        await client_for_server(server).remove_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"revoke_peer (server={server.get('id')}): {e}")


async def suspend_peer(server: dict, peer_id: str, protocol: str):
    try:
        await client_for_server(server).suspend_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"suspend_peer: {e}")


async def resume_peer(server: dict, peer_id: str, protocol: str):
    try:
        await client_for_server(server).resume_peer(protocol, peer_id)
    except Exception as e:
        log.warning(f"resume_peer: {e}")
