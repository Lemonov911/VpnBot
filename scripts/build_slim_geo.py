#!/usr/bin/env python3
"""Собирает slim-версии geosite.dat / geoip.dat для Happ split-routing.

Берёт полные базы runetfreedom (~85 МБ суммарно) и вырезает ТОЛЬКО категории,
которые реально использует наш routing-профиль (см.
`bot/services/webapp_api.py::_build_happ_routing_deeplink`):

  geosite: category-ru, ru-available-only-inside, category-ads-all, private
  geoip:   ru, private

Результат (~4 МБ) кладётся в `geo/geosite.dat` + `geo/geoip.dat` и хостится в
нашем публичном репо (raw.githubusercontent). Клиент Happ тянет их по
Geositeurl/Geoipurl — 4 МБ вместо 85.

Запуск (обновить базу, когда runetfreedom обновили списки):
    python3 scripts/build_slim_geo.py
"""
from __future__ import annotations

import os
import sys
import urllib.request

RUNETFREEDOM = "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO_DIR = os.path.join(REPO_ROOT, "geo")

GEOSITE_KEEP = {"CATEGORY-RU", "RU-AVAILABLE-ONLY-INSIDE", "CATEGORY-ADS-ALL", "PRIVATE"}
GEOIP_KEEP = {"RU", "PRIVATE"}


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        if not (x & 0x80):
            break
        shift += 7
    return result, i


def _write_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        out.append(x | 0x80 if n else x)
        if not n:
            break
    return bytes(out)


def _country_code(payload: bytes) -> str | None:
    """Первое внутреннее поле 1 (wire 2) — country_code (= имя категории)."""
    i = 0
    while i < len(payload):
        key, i = _read_varint(payload, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            _, i = _read_varint(payload, i)
        elif wt == 2:
            ln, i = _read_varint(payload, i)
            v = payload[i:i + ln]
            i += ln
            if fn == 1:
                return v.decode("utf-8", "replace")
        elif wt == 5:
            i += 4
        elif wt == 1:
            i += 8
        else:
            break
    return None


def slim(raw: bytes, keep: set[str]) -> tuple[bytes, list[str]]:
    """GeoSiteList/GeoIPList = repeated entry (field 1, wire 2). Оставляем
    только записи нужных категорий, переотдавая их сырые байты (lossless)."""
    out = bytearray()
    kept: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        key, i = _read_varint(raw, i)
        fn, wt = key >> 3, key & 7
        assert wt == 2 and fn == 1, f"unexpected top field {fn}/{wt}"
        ln, j = _read_varint(raw, i)
        payload = raw[j:j + ln]
        i = j + ln
        cc = _country_code(payload)
        if cc in keep:
            out += b"\x0a" + _write_varint(ln) + payload
            kept.append(cc)
    return bytes(out), kept


def _fetch(name: str) -> bytes:
    url = f"{RUNETFREEDOM}/{name}"
    print(f"[fetch] {url}")
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def main() -> int:
    os.makedirs(GEO_DIR, exist_ok=True)
    for name, keep in (("geosite.dat", GEOSITE_KEEP), ("geoip.dat", GEOIP_KEEP)):
        full = _fetch(name)
        slimmed, kept = slim(full, keep)
        miss = keep - set(kept)
        if miss:
            print(f"[WARN] {name}: категории не найдены: {miss}")
        dst = os.path.join(GEO_DIR, name)
        with open(dst, "wb") as f:
            f.write(slimmed)
        print(f"[done] {name}: {len(full)/1e6:.1f}MB -> {len(slimmed)/1e6:.2f}MB  "
              f"kept={sorted(set(kept))}  -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
