#!/usr/bin/env python3
"""Standalone VLESS+Reality end-to-end tester.

Usage:
  ./test_vless.py "<vless://...>"
  ./test_vless.py            # default trial URL for 8443+vision

Downloads xray-core for the current macOS/Linux arch into ~/.cache/vpnbot-test/,
generates a client config from the parsed URL, opens a local SOCKS5 on 127.0.0.1:1080,
and probes egress through it. Prints whether traffic actually exits via the VPN.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

XRAY_VERSION = "v26.3.27"
CACHE = Path.home() / ".cache" / "vpnbot-test"
SOCKS_PORT = 1080
EXIT_PROBE_URLS = ["https://ipinfo.io/ip", "https://ifconfig.me/ip"]
DOWNLOAD_BYTES = 5_000_000  # 5 MB — enough to measure throughput, small enough to be quick
DOWNLOAD_URL = f"https://speed.cloudflare.com/__down?bytes={DOWNLOAD_BYTES}"
SITE_PROBES = [
    ("google", "https://www.google.com/generate_204"),  # 204 No Content
    ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("youtube", "https://www.youtube.com/favicon.ico"),
]

DEFAULT_URL = (
    "vless://5b8ec3bd-5fa4-413b-a13f-646713c73db8@68.183.15.95:8443"
    "?encryption=none&flow=xtls-rprx-vision&fp=chrome&headerType=none"
    "&pbk=LF3kEB9n6LMOQNKsC2kp18g_SvMlREZmMBr1hcR1FgM&security=reality"
    "&sid=2f59d765b9f5c068&sni=www.microsoft.com&spx=%2F&type=tcp"
    "#trial_594024866_test"
)


def detect_release_asset() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "Xray-macos-arm64-v8a.zip" if machine in ("arm64", "aarch64") else "Xray-macos-64.zip"
    if system == "linux":
        return "Xray-linux-arm64-v8a.zip" if machine in ("arm64", "aarch64") else "Xray-linux-64.zip"
    raise RuntimeError(f"unsupported platform: {system}/{machine}")


def ensure_xray() -> Path:
    binary = CACHE / "xray"
    if binary.exists():
        return binary
    CACHE.mkdir(parents=True, exist_ok=True)
    asset = detect_release_asset()
    url = f"https://github.com/XTLS/Xray-core/releases/download/{XRAY_VERSION}/{asset}"
    print(f"[setup] downloading xray-core {XRAY_VERSION} → {asset}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extract("xray", path=CACHE)
    binary.chmod(0o755)
    return binary


def parse_vless(url: str) -> dict:
    if not url.startswith("vless://"):
        raise ValueError("not a vless:// URL")
    parsed = urllib.parse.urlparse(url)
    uuid = parsed.username
    host = parsed.hostname
    port = parsed.port
    q = urllib.parse.parse_qs(parsed.query)
    flat = {k: v[0] for k, v in q.items()}
    return {
        "uuid": uuid,
        "host": host,
        "port": port,
        "flow": flat.get("flow", ""),
        "encryption": flat.get("encryption", "none"),
        "security": flat.get("security", "reality"),
        "sni": flat.get("sni", ""),
        "fp": flat.get("fp", "chrome"),
        "pbk": flat.get("pbk", ""),
        "sid": flat.get("sid", ""),
        "spx": flat.get("spx", "/"),
        "net": flat.get("type", "tcp"),
    }


def build_config(p: dict) -> dict:
    user = {"id": p["uuid"], "encryption": p["encryption"]}
    if p["flow"]:
        user["flow"] = p["flow"]
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            }
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": p["host"],
                            "port": p["port"],
                            "users": [user],
                        }
                    ]
                },
                "streamSettings": {
                    "network": p["net"],
                    "security": p["security"],
                    "realitySettings": {
                        "serverName": p["sni"],
                        "fingerprint": p["fp"],
                        "publicKey": p["pbk"],
                        "shortId": p["sid"],
                        "spiderX": p["spx"],
                    },
                },
                "tag": "proxy",
            },
            {"protocol": "freedom", "tag": "direct"},
        ],
    }


def wait_socks(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def probe_egress_ip(expected_host: str) -> dict:
    out = {"egress_ip": None, "latency_ms": None, "ok": False, "errors": []}
    for url in EXIT_PROBE_URLS:
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time", "12",
                    "--socks5-hostname", f"127.0.0.1:{SOCKS_PORT}",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            out["errors"].append(f"{url}: timeout")
            continue
        latency = int((time.monotonic() - t0) * 1000)
        if r.returncode == 0 and r.stdout.strip():
            out["egress_ip"] = r.stdout.strip()
            out["latency_ms"] = latency
            out["ok"] = out["egress_ip"] == expected_host
            return out
        out["errors"].append(f"{url}: rc={r.returncode} stderr={r.stderr.strip()[:160]}")
    return out


def probe_sites() -> list[dict]:
    """Walk a handful of representative hosts to confirm steady-state TLS works."""
    results = []
    for label, url in SITE_PROBES:
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "-o", "/dev/null",
                    "--max-time", "10",
                    "--socks5-hostname", f"127.0.0.1:{SOCKS_PORT}",
                    "-w", "%{http_code}",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=12,
            )
            latency = int((time.monotonic() - t0) * 1000)
            results.append({
                "label": label,
                "url": url,
                "http": r.stdout.strip() or "—",
                "latency_ms": latency,
                "ok": r.returncode == 0 and r.stdout.strip() not in ("", "000"),
                "err": r.stderr.strip()[:120] if r.returncode != 0 else "",
            })
        except subprocess.TimeoutExpired:
            results.append({
                "label": label, "url": url, "http": "timeout",
                "latency_ms": 10000, "ok": False, "err": "curl timeout",
            })
    return results


def probe_download() -> dict:
    """Download a known-size payload to measure real throughput end-to-end."""
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [
                "curl",
                "-sS",
                "-o", "/dev/null",
                "--max-time", "30",
                "--socks5-hostname", f"127.0.0.1:{SOCKS_PORT}",
                "-w", "size:%{size_download} time:%{time_total} code:%{http_code}",
                DOWNLOAD_URL,
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "timeout (30s)", "bytes": 0, "mbps": 0.0}

    elapsed = time.monotonic() - t0
    if r.returncode != 0:
        return {"ok": False, "err": r.stderr.strip()[:200] or r.stdout.strip()[:200], "bytes": 0, "mbps": 0.0}

    # Parse "size:NNN time:NNN code:NNN"
    fields = {}
    for chunk in r.stdout.strip().split():
        if ":" in chunk:
            k, v = chunk.split(":", 1)
            fields[k] = v
    size = int(fields.get("size", "0"))
    code = fields.get("code", "—")
    mbps = (size * 8 / 1_000_000) / elapsed if elapsed > 0 else 0.0
    expected_ok = size >= DOWNLOAD_BYTES * 0.95 and code.startswith("2")
    return {
        "ok": expected_ok,
        "err": "" if expected_ok else f"got {size} bytes, code {code} (expected ~{DOWNLOAD_BYTES})",
        "bytes": size,
        "mbps": mbps,
        "elapsed_s": elapsed,
        "code": code,
    }


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    params = parse_vless(url)
    print(f"[parse] host={params['host']}:{params['port']} flow={params['flow'] or '<none>'} "
          f"sni={params['sni']} sid={params['sid'][:8]}…")

    xray = ensure_xray()
    config = build_config(params)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        cfg_path = f.name

    print(f"[run] xray={xray} config={cfg_path} socks=127.0.0.1:{SOCKS_PORT}")
    proc = subprocess.Popen(
        [str(xray), "-c", cfg_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        if not wait_socks(SOCKS_PORT, timeout=4):
            proc.terminate()
            log = proc.stdout.read() if proc.stdout else ""
            print(f"[fail] SOCKS port {SOCKS_PORT} never came up\n{log}")
            return 2

        egress = probe_egress_ip(params["host"])
        if not egress["egress_ip"]:
            print("[FAIL] no traffic exited through SOCKS5 — Reality handshake likely failed.")
            for e in egress["errors"]:
                print(f"       · {e}")
            return 1
        if not egress["ok"]:
            print(f"[FAIL] traffic went out, but egress IP = {egress['egress_ip']}, "
                  f"NOT {params['host']}. SOCKS5 didn't route via VLESS.")
            return 1

        print(f"[egress] {egress['egress_ip']} (matches server), first-byte latency {egress['latency_ms']}ms")

        sites = probe_sites()
        site_failed = 0
        for s in sites:
            mark = "✓" if s["ok"] else "✗"
            err = f"  err={s['err']}" if s["err"] else ""
            print(f"  {mark} {s['label']:<10} HTTP {s['http']:<3} {s['latency_ms']}ms{err}")
            if not s["ok"]:
                site_failed += 1

        dl = probe_download()
        if dl["ok"]:
            print(f"[download] {dl['bytes']/1_000_000:.2f} MB in {dl['elapsed_s']:.2f}s "
                  f"→ {dl['mbps']:.2f} Mbps")
        else:
            print(f"[download] FAIL: {dl['err']}")

        if site_failed == 0 and dl["ok"]:
            print(f"[PASS] all probes succeeded via {params['host']}")
            return 0
        print(f"[FAIL] handshake ok but traffic broken: {site_failed} site(s) failed, "
              f"download {'ok' if dl['ok'] else 'failed'}")
        # Dump xray output for diagnostics
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_tail = ""
        if proc.stdout:
            try:
                log_tail = proc.stdout.read()
            except Exception:
                pass
        if log_tail.strip():
            print("\n--- xray output ---")
            print(log_tail.strip())
        return 1
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.unlink(cfg_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
