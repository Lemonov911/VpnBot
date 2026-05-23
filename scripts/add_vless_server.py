#!/usr/bin/env python3
"""Provision an existing-in-DB VLESS server with all users' canonical UUIDs.

Заменяет ручной backfill, который раньше делался при каждой добавке сервера
(см. session 20.05 — там был label-collision bug на admin'е). Идемпотентен:
повторный прогон не создаёт дублей, агент возвращает существующего клиента
если email уже совпадает.

Pre-conditions:
  1. Сервер уже создан в БД `servers`:
       - protocol='vless', is_active=1
       - agent_url + agent_token заполнены
       - xray_pubkey + xray_short_id заполнены
  2. Сам сервер (Xray + agent) поднят и достижим.
  3. Schema migration v1 прогнана: `users.vless_uuid` существует.
     `scripts/backfill_vless_canonical.py` уже отработал для существующих
     юзеров.

Что делает:
  • Для каждого юзера у которого users.vless_uuid IS NOT NULL И есть active/grace
    подписка с vless-протоколом — определяет tier (vless-base / vless-max /
    vless-grace) и POST'ит peer на agent нового сервера с этим UUID и
    уникальным label `u<user_id>_v_s<server_id>` (избегает email-collision'а
    как в xray.go::addUserToConfig).

Usage:
    ssh root@151.243.113.31 \\
        'python3 - <server_id>' < scripts/add_vless_server.py

или (локально):
    BOT_DB_PATH=/path/to/bot.db python3 scripts/add_vless_server.py <server_id>
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DEFAULT_DB = "/opt/vpnbot/bot/bot.db"

# Маппинг плана + статуса подписки → tier-сервис на агенте. Совпадает с
# логикой bot/services/plans.py и database.vless_port_column.
def tier_for(plan: str, sub_status: str) -> str:
    if sub_status == "grace":
        return "vless-grace"
    if plan == "vpn_base":
        return "vless-base"
    if plan == "vpn_max":
        return "vless-max"
    # legacy / trial / unknown → ставим на vless-base (это наш дефолт для
    # «обычного» tier'а; trial-юзеры сейчас именно там).
    return "vless-base"


def agent_post(agent_url: str, token: str, path: str, body: dict) -> tuple[int, dict | str]:
    """POST с HMAC-подписью X-Agent-Sig: <ts>.<hex(hmac)>."""
    body_bytes = json.dumps(body).encode()
    ts = str(int(time.time()))
    msg = (ts + ":POST" + path + ":" + body_bytes.decode()).encode()
    sig = hmac.new(token.encode(), msg, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        agent_url.rstrip("/") + path,
        data=body_bytes,
        headers={"X-Agent-Sig": ts + "." + sig, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode()
            return resp.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("server_id", type=int)
    ap.add_argument("--db", default=os.environ.get("BOT_DB_PATH", DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    srv = cur.execute("""
        SELECT id, name, host, agent_url, agent_token, xray_pubkey, is_active, protocol
        FROM servers WHERE id=?
    """, (args.server_id,)).fetchone()
    if not srv:
        print(f"server_id={args.server_id} not found in DB", file=sys.stderr)
        return 1
    if srv["protocol"] != "vless":
        print(f"server_id={args.server_id} is protocol={srv['protocol']}, not vless", file=sys.stderr)
        return 1
    if not srv["is_active"]:
        print(f"WARN: server_id={args.server_id} is_active=0", file=sys.stderr)
    if not srv["agent_url"] or not srv["agent_token"]:
        print(f"server_id={args.server_id} missing agent_url/agent_token", file=sys.stderr)
        return 1
    if not srv["xray_pubkey"]:
        print(f"server_id={args.server_id} has no xray_pubkey — run backfill or "
              f"populate manually first", file=sys.stderr)
        return 1

    print(f"target: server_id={srv['id']} name={srv['name']} host={srv['host']}")

    # Все юзеры с canonical UUID + active/grace подпиской
    candidates = cur.execute("""
        SELECT u.id AS user_id, u.vless_uuid,
               s.plan, s.status AS sub_status
        FROM users u
        JOIN subscriptions s ON s.user_id = u.id
        WHERE u.vless_uuid IS NOT NULL
          AND s.status IN ('active', 'grace')
        ORDER BY u.id
    """).fetchall()

    # Дедупликация: у юзера может быть несколько active/grace подписок
    # (например trial + paid). Берём ОДНУ — приоритет active > grace,
    # с самой высокой plan-tier (max > base).
    PLAN_RANK = {"vpn_max": 3, "vpn_base": 2, "vpn_trial": 1}
    by_user: dict[int, sqlite3.Row] = {}
    for row in candidates:
        prev = by_user.get(row["user_id"])
        rank = (
            0 if row["sub_status"] == "active" else 1,  # active first
            -PLAN_RANK.get(row["plan"], 0),              # higher tier first
        )
        prev_rank = (
            0 if prev and prev["sub_status"] == "active" else 1,
            -PLAN_RANK.get(prev["plan"], 0) if prev else 0,
        )
        if prev is None or rank < prev_rank:
            by_user[row["user_id"]] = row

    print(f"users to provision: {len(by_user)}")

    ok = fail = 0
    for uid, row in by_user.items():
        tier = tier_for(row["plan"], row["sub_status"])
        label = f"u{uid}_v_s{srv['id']}"  # уникальный per (user, server) → нет collision
        if args.dry_run:
            print(f"  [dry] user {uid}: tier={tier} uuid={row['vless_uuid']} label={label}")
            ok += 1
            continue

        code, resp = agent_post(
            srv["agent_url"], srv["agent_token"],
            f"/services/{tier}/peers",
            {"id": row["vless_uuid"], "label": label},
        )
        if code == 200:
            print(f"  user {uid}: OK (tier={tier})")
            ok += 1
        else:
            print(f"  user {uid}: FAIL HTTP {code}: {str(resp)[:200]}")
            fail += 1

    print()
    print(f"=== done: {ok} provisioned, {fail} failed ===")

    # Mark server as backfilled so /sub/{token} starts emitting its URLs.
    # Без этого новый сервер уже в БД и юзеры пиры на нём имеют, но
    # endpoint /sub фильтрует servers WHERE backfilled=1 — и сервер не
    # появляется в подписке. С Frankfurt это пришлось делать вручную.
    if not args.dry_run and fail == 0:
        cur.execute("UPDATE servers SET backfilled=1 WHERE id=?", (args.server_id,))
        con.commit()
        print(f"server_id={args.server_id} marked backfilled=1")

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
