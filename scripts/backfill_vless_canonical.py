#!/usr/bin/env python3
"""One-time backfill for the dynamic-VLESS-subscription refactor (2026-05-21).

Подготавливает прод-БД к новому endpoint /sub/{token}:

  1) Для каждого юзера с активными VLESS-конфигами берём один canonical UUID
     (предпочтительно из live-конфига под active/grace подпиской) и пишем
     его в `users.vless_uuid`. Этот UUID становится "именем" юзера в Reality
     и одинаков на всех VLESS-серверах.

  2) Для каждого VLESS-сервера (`servers.protocol='vless'`) заполняем колонки
     `xray_pubkey / xray_short_id / xray_sni / xray_dest / xray_port_*`:
       - pbk/sid парсим из любого активного config_data на этом сервере;
       - SNI выставляем в пустую строку: DPI-safe (см. memory/mts-dpi.md).
         Старые subscription-клиенты с захардкоженным sni=microsoft.com
         продолжают работать — сервер принимает оба варианта (мы оставили
         backward-compat в serverNames).
       - port-* — стандартный для нашего стека маппинг tier→port.

Идемпотентен: повторный запуск не ломает уже заполненные поля.

Прогон:
    ssh root@151.243.113.31 'python3 -' < scripts/backfill_vless_canonical.py

или скопировать на сервер и запустить локально.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from typing import Optional

DEFAULT_DB = "/opt/vpnbot/bot/bot.db"

# Эти порты у нас стандарт для всех VLESS-нод (см. Amsterdam + Charlotte).
# Скрипт не угадывает их с агента, а просто прописывает дефолтный набор —
# если в будущем будет нода с нестандартными портами, поправь руками в БД.
DEFAULT_PORTS = {
    "xray_port_base":      8443,
    "xray_port_max":       8448,
    "xray_port_base_slow": 9443,
    "xray_port_max_slow":  9448,
    "xray_port_grace":     9453,
}

# Пустой SNI — DPI bypass mode. См. session 20.05.
DEFAULT_SNI = ""
# dest — info-only поле в БД (для документирования куда Reality fallback'ит).
# Не используется при построении URL.
DEFAULT_DEST = "addons.mozilla.org:443"
DEFAULT_FP = "chrome"


def extract_url_param(url: str, key: str) -> Optional[str]:
    m = re.search(rf"[?&]{re.escape(key)}=([^&#]+)", url)
    return m.group(1) if m else None


def backfill_users(con: sqlite3.Connection, *, dry_run: bool) -> int:
    """Set users.vless_uuid for users with active VLESS configs. Returns count."""
    cur = con.cursor()
    # Кандидаты: юзеры без vless_uuid, но с минимум одним active VLESS-конфигом.
    # Источник UUID — `configs.vless_uuid` (там уже корректный после нашего
    # фикса в session 20.05); если NULL — парсим из config_data.
    rows = cur.execute("""
        SELECT u.id AS user_id, c.vless_uuid, c.config_data
        FROM users u
        JOIN configs c ON c.user_id = u.id
        WHERE u.vless_uuid IS NULL
          AND c.protocol = 'vless'
          AND c.status   = 'active'
        ORDER BY u.id, c.id  -- стабильно берём самый старый active-row
    """).fetchall()

    # Берём по одному UUID на юзера — первый встретившийся (фактически — самый
    # старый active config).
    chosen: dict[int, str] = {}
    for user_id, vless_uuid, config_data in rows:
        if user_id in chosen:
            continue
        uuid_val = vless_uuid
        if not uuid_val and config_data:
            m = re.match(r"vless://([0-9a-f-]{36})@", config_data)
            uuid_val = m.group(1) if m else None
        if uuid_val:
            chosen[user_id] = uuid_val

    print(f"users to backfill: {len(chosen)}")
    if dry_run:
        for uid, uuid in list(chosen.items())[:5]:
            print(f"  [dry] user {uid} → {uuid}")
        return len(chosen)

    for uid, uuid in chosen.items():
        try:
            cur.execute(
                "UPDATE users SET vless_uuid=? WHERE id=? AND vless_uuid IS NULL",
                (uuid, uid),
            )
        except sqlite3.IntegrityError as e:
            # UNIQUE constraint hit: другой юзер уже имеет этот UUID.
            # Маловероятно (UUID v4 collision-free), но логируем.
            print(f"  WARN user {uid} → {uuid}: {e}", file=sys.stderr)
    con.commit()
    return len(chosen)


def backfill_servers(con: sqlite3.Connection, *, dry_run: bool) -> int:
    """Set servers.xray_* for VLESS servers from live config_data. Returns count."""
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    servers = cur.execute("""
        SELECT id, name, host, xray_pubkey FROM servers
        WHERE protocol='vless' AND is_active=1
    """).fetchall()

    updated = 0
    for srv in servers:
        if srv["xray_pubkey"]:
            print(f"  server {srv['id']} ({srv['name']}): already backfilled, skip")
            continue

        sample = cur.execute("""
            SELECT config_data FROM configs
            WHERE server_id=? AND protocol='vless' AND status='active'
              AND config_data LIKE 'vless://%'
            ORDER BY id LIMIT 1
        """, (srv["id"],)).fetchone()
        if not sample:
            print(f"  server {srv['id']} ({srv['name']}): no live config to parse, skip")
            continue

        url = sample["config_data"]
        pbk = extract_url_param(url, "pbk")
        sid = extract_url_param(url, "sid") or ""
        if not pbk:
            print(f"  server {srv['id']} ({srv['name']}): could not parse pbk from URL, skip")
            continue

        print(f"  server {srv['id']} ({srv['name']}): pbk={pbk[:16]}… sid={sid[:8]}…")
        if dry_run:
            updated += 1
            continue

        cur.execute("""
            UPDATE servers SET
              xray_pubkey       = ?,
              xray_short_id     = ?,
              xray_sni          = ?,
              xray_dest         = ?,
              xray_fingerprint  = ?,
              xray_port_base       = ?,
              xray_port_max        = ?,
              xray_port_base_slow  = ?,
              xray_port_max_slow   = ?,
              xray_port_grace      = ?
            WHERE id=?
        """, (
            pbk, sid, DEFAULT_SNI, DEFAULT_DEST, DEFAULT_FP,
            DEFAULT_PORTS["xray_port_base"],
            DEFAULT_PORTS["xray_port_max"],
            DEFAULT_PORTS["xray_port_base_slow"],
            DEFAULT_PORTS["xray_port_max_slow"],
            DEFAULT_PORTS["xray_port_grace"],
            srv["id"],
        ))
        updated += 1
    con.commit()
    return updated


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("BOT_DB_PATH", DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true", help="Print actions, do not write")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(args.db)
    try:
        print("=== Backfill: users.vless_uuid ===")
        u = backfill_users(con, dry_run=args.dry_run)
        print(f"  {'(dry) ' if args.dry_run else ''}done, {u} user(s)")
        print()
        print("=== Backfill: servers.xray_* ===")
        s = backfill_servers(con, dry_run=args.dry_run)
        print(f"  {'(dry) ' if args.dry_run else ''}done, {s} server(s)")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
