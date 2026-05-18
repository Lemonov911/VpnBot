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

# Shared session — переиспользует TCP/TLS connection между запросами.
# Раньше aiohttp.ClientSession создавался на каждый _request() → новый
# TCP handshake + TLS, 50-200мс per call. На hourly sync с 5 сервисами
# × N серверов это ~секунды латентности впустую. Lazy-init чтобы не
# создавать session до первого фактического вызова (импорт безопасен).
_SHARED_SESSION: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    if _SHARED_SESSION is None or _SHARED_SESSION.closed:
        _SHARED_SESSION = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                limit=50,            # max parallel connections across all servers
                limit_per_host=10,   # max per agent — hourly sync hits ~5
                ttl_dns_cache=300,
            ),
        )
    return _SHARED_SESSION


async def close_shared_session() -> None:
    """Закрывает shared session. Вызывать на shutdown."""
    global _SHARED_SESSION
    if _SHARED_SESSION is not None and not _SHARED_SESSION.closed:
        await _SHARED_SESSION.close()
        _SHARED_SESSION = None


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
        # ВНИМАНИЕ: ранее тут отправлялся ещё `X-Agent-Token: <raw token>` как
        # legacy fallback. Это убивало защиту HMAC — перехват одного запроса
        # = вечный токен, привязка к timestamp/path/body выбрасывалась. Удалено
        # 15.05 (sec audit C1). Если агент на старой версии — пересобрать.
        return {
            "X-Agent-Sig":  f"{ts}.{sig}",
            "Content-Type": "application/json",
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
        s = _get_session()
        async with s.request(
            method, url,
            data=body_bytes if body_bytes else None,
            headers=headers,
            timeout=aiohttp.ClientTimeout(
                total=timeout_s,
                # connect/sock_connect предотвращают зависание при DROP-правиле
                # (без RST). TCP SYN retransmit без connect timeout = ~2 min hang,
                # что поглощает весь _safe() budget на один мёртвый сервер.
                connect=min(10, timeout_s),
                sock_connect=min(10, timeout_s),
            ),
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

    async def throttle_peer(self, service: str, peer_id: str, assigned_ip: str, kbps: int = 256):
        """Grace-period throttle: ограничивает скорость пира через tc на awg0.
        Для VLESS — переключает на vless-grace inbound (порт 9453)."""
        await self._request(
            "POST",
            f"/services/{service}/peers/{quote(peer_id, safe='')}/throttle",
            {"ip": assigned_ip, "kbps": kbps},
        )

    async def unthrottle_peer(self, service: str, peer_id: str, assigned_ip: str):
        """Снимает grace-period throttle."""
        await self._request(
            "DELETE",
            f"/services/{service}/peers/{quote(peer_id, safe='')}/throttle",
            {"ip": assigned_ip},
        )

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


async def provision_peer(server: dict, label: str, protocol: str,
                          *, peer_id: str | None = None) -> PeerResult:
    """Provision a peer on `server` for `protocol` (awg / vless / wg).

    `peer_id` — если задан, agent создаст пира с этим UUID. Используется для
    multi-location VLESS-подписок: один UUID реплицируется на N серверов,
    юзер импортирует subscription URL и видит список локаций.
    """
    return await client_for_server(server).add_peer(protocol, label, peer_id=peer_id)


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


async def throttle_peer(server: dict, peer_id: str, protocol: str, assigned_ip: str, kbps: int = 256):
    """Применяет tc-throttle на пир. Для AWG — фильтр по dst IP на awg0."""
    try:
        await client_for_server(server).throttle_peer(protocol, peer_id, assigned_ip, kbps=kbps)
    except Exception as e:
        log.warning(f"throttle_peer: {e}")


async def unthrottle_peer(server: dict, peer_id: str, protocol: str, assigned_ip: str):
    """Снимает tc-throttle с пира."""
    try:
        await client_for_server(server).unthrottle_peer(protocol, peer_id, assigned_ip)
    except Exception as e:
        log.warning(f"unthrottle_peer: {e}")


# ── Smoke-test CLI ─────────────────────────────────────────────────────────────
#
# `python -m services.vpnctl_client probe <server_id>` — быстрая диагностика
# связи с vpnctl-агентом. Печатает каноничную HMAC-строку (для дебага 401),
# тайминги, и список сервисов. Полезно когда что-то не работает в проде
# и надо понять — это бот, агент, сеть, или подпись.

async def _probe_server(server_id: int) -> int:
    """Пингует все эндпоинты агента сервера и печатает отчёт. Возвращает exit code."""
    import sys
    from services.database import get_server_by_id

    server = await get_server_by_id(server_id)
    if not server:
        print(f"❌ Server #{server_id} не найден в БД", file=sys.stderr)
        return 1
    if not server.get("agent_url") or not server.get("agent_token"):
        print(f"❌ Server #{server_id} не настроен (нет agent_url или agent_token)", file=sys.stderr)
        return 1

    print(f"Probing server #{server_id}: {server.get('name')} ({server['agent_url']})")
    print(f"Token: ****  (length: {len(server['agent_token'])})")
    print()

    client = VpnctlClient(server["agent_url"], server["agent_token"])
    ok_count = 0
    fail_count = 0

    # 1) /health — без auth (но всё равно подписываем чтоб проверить)
    print("─── 1) GET /health ───")
    t0 = time.time()
    try:
        st, data = await client._request("GET", "/health", timeout_s=10)
        elapsed = (time.time() - t0) * 1000
        print(f"  status: {st}, latency: {elapsed:.0f}ms")
        if st == 200:
            print(f"  uptime: {data.get('uptime')}, services: {data.get('services')}")
            ok_count += 1
        else:
            print(f"  ❌ body: {data}")
            fail_count += 1
    except Exception as e:
        print(f"  ❌ network error: {e}")
        fail_count += 1

    # 2) /services — требует HMAC
    print("\n─── 2) GET /services (HMAC required) ───")
    ts = str(int(time.time()))
    canonical = f"{ts}:GET/services:"
    print(f"  canonical string: {canonical!r}")
    print(f"  HMAC-SHA256(token, canonical) = подпись")
    t0 = time.time()
    try:
        st, data = await client._request("GET", "/services", timeout_s=10)
        elapsed = (time.time() - t0) * 1000
        print(f"  status: {st}, latency: {elapsed:.0f}ms")
        if st == 200:
            services = [s.get("name") for s in data] if isinstance(data, list) else data
            print(f"  ✅ services: {services}")
            ok_count += 1
        elif st == 401:
            print(f"  ❌ 401 — HMAC не верифицировался. Проверь:")
            print(f"    - Токен в БД совпадает с AGENT_TOKEN на сервере?")
            print(f"    - Время на боте и сервере синхронизировано (replay window 5 min)?")
            fail_count += 1
        else:
            print(f"  ❌ body: {data}")
            fail_count += 1
    except Exception as e:
        print(f"  ❌ network error: {e}")
        fail_count += 1

    print(f"\n{'='*50}")
    print(f"OK: {ok_count}  /  FAIL: {fail_count}")
    return 0 if fail_count == 0 else 1


def _cli() -> int:
    import asyncio as _asyncio
    import sys
    if len(sys.argv) < 3 or sys.argv[1] != "probe":
        print("Usage: python -m services.vpnctl_client probe <server_id>", file=sys.stderr)
        return 2
    try:
        server_id = int(sys.argv[2])
    except ValueError:
        print(f"❌ server_id must be int, got {sys.argv[2]!r}", file=sys.stderr)
        return 2
    code = _asyncio.run(_probe_server(server_id))
    # Корректно закрываем shared session чтобы asyncio не ругался при exit.
    _asyncio.run(close_shared_session())
    return code


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
