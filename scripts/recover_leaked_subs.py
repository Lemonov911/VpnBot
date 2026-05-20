#!/usr/bin/env python3
"""Recovery script для подписок-утечек.

Контекст: audit 21.05 нашёл что handle_admin_sub_refund не делал revoke
конфигов до фикса от 21.05. Также _process_grace_expired_subscriptions
может пропустить sub при race с renew (aborted-в-середине). Результат —
sub в БД status='refunded'/'expired', а её configs до сих пор status='active'
и peer'ы на агенте работают. Юзер продолжает пользоваться VPN.

Прямой кейс: user 594024866 sub#5 (vpn_max, expired 15 мая) — 4 VLESS + 1 AWG
живут 6 дней без действующей подписки.

Скрипт:
1. Находит ВСЕ sub'ы со status NOT IN ('active', 'grace') у которых есть
   configs.status='active'.
2. Для каждой sub'ы вызывает revoke_subscription_configs (тот же helper
   что новый refund использует).
3. Continue-on-error: ошибка на агенте не блокирует следующую sub.

Запуск:
   python3 scripts/recover_leaked_subs.py                  # dry-run, только report
   python3 scripts/recover_leaked_subs.py --apply          # реально revoke'ить
   python3 scripts/recover_leaked_subs.py --apply --sub-id 5  # только конкретная sub

Безопасность: dry-run по умолчанию.  --apply  обязателен для destructive ops.

Возвращает exit-code 0 если всё ok, 1 если были unrecoverable ошибки.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Допуск из любой точки запуска — добавляем bot/ в path.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("recover")


async def find_leaked_subs(only_sub_id: int | None = None) -> list[dict]:
    """Find subscriptions со status NOT IN ('active','grace') у которых
    остались active configs.  По одному сабу если только_sub_id передан."""
    import aiosqlite
    from services.database import DB_PATH

    query = """
        SELECT s.id, s.user_id, s.plan, s.status, s.expires_at, s.refunded_at,
               (SELECT COUNT(*) FROM configs c
                 WHERE c.subscription_id = s.id AND c.status='active') AS active_configs
        FROM subscriptions s
        WHERE s.status NOT IN ('active', 'grace')
          AND EXISTS (SELECT 1 FROM configs c
                       WHERE c.subscription_id = s.id AND c.status='active')
    """
    params: tuple = ()
    if only_sub_id is not None:
        query += " AND s.id = ?"
        params = (only_sub_id,)
    query += " ORDER BY s.id"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Реально revoke'ить (default: dry-run)")
    parser.add_argument("--sub-id", type=int, default=None,
                        help="Конкретный sub_id (default: все leak'и)")
    args = parser.parse_args()

    leaked = await find_leaked_subs(only_sub_id=args.sub_id)
    if not leaked:
        print("✓ No leaked subs found. БД consistent.")
        return 0

    print(f"\nFound {len(leaked)} leaked subscription(s):")
    print(f"{'sub_id':>6}  {'user_id':>12}  {'plan':<14}  {'status':<10}  "
          f"{'expired_at':<20}  {'active_cfg':>10}")
    print("-" * 80)
    total_configs = 0
    for sub in leaked:
        print(f"{sub['id']:>6}  {sub['user_id']:>12}  {sub['plan']:<14}  "
              f"{sub['status']:<10}  {(sub.get('expires_at') or '-')[:19]:<20}  "
              f"{sub['active_configs']:>10}")
        total_configs += sub["active_configs"] or 0
    print(f"\nTotal active configs to revoke: {total_configs}")

    if not args.apply:
        print("\n[dry-run] Чтобы реально revoke'ить — добавь --apply")
        return 0

    print(f"\n[apply] Revoke начат...")
    from services.revoke import revoke_subscription_configs

    total_revoked = 0
    total_failed = 0
    for sub in leaked:
        sub_id = sub["id"]
        plan = sub["plan"]
        logger.info("revoke sub #%d (user=%d plan=%s status=%s)",
                    sub_id, sub["user_id"], plan, sub["status"])
        try:
            revoked, failed = await revoke_subscription_configs(
                sub_id, plan, log_prefix=f"recover#{sub_id}"
            )
            total_revoked += revoked
            total_failed += failed
            logger.info("sub #%d done: revoked=%d failed=%d",
                        sub_id, revoked, failed)
        except Exception as e:
            logger.error("sub #%d UNRECOVERABLE: %s", sub_id, e, exc_info=True)
            total_failed += 1

    print(f"\n=== Recovery complete ===")
    print(f"  Subscriptions processed: {len(leaked)}")
    print(f"  Configs revoked:         {total_revoked}")
    print(f"  Configs failed:          {total_failed}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
