from __future__ import annotations
"""
SQLite через aiosqlite.

Жизненный цикл слота конфига:
  empty   → куплен, конфиг не создан
  active  → конфиг создан, работает
  (revoked удалён — отзыв сбрасывает слот в empty, слот не исчезает)
"""

import logging

import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "bot.db"


@asynccontextmanager
async def _connect():
    """aiosqlite.connect + busy_timeout 5s.

    `journal_mode=WAL` ставится один раз в `init_db()` (это persistent
    pragma — переживает рестарт SQLite). `busy_timeout` — per-connection,
    поэтому ставится здесь на каждом подключении.

    Все вызовы в этом модуле и снаружи должны идти через `_connect()`
    (или прямой `aiosqlite.connect(DB_PATH)` для legacy-кода, но WAL+
    persistent journal-mode и так на месте).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


async def _pre_migrate_snapshot():
    """Snapshot БД перед миграциями. Если ALTER TABLE упадёт (constraints,
    disk space, etc), бот не стартует — но юзер может вручную восстановить
    snapshot из /opt/vpnbot/.snapshots/pre-migrate-*.db и откатить commit.
    """
    import shutil
    if not DB_PATH.exists():
        return  # первая инициализация, нет что снапшотить
    snap_dir = DB_PATH.parent / ".snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap_path = snap_dir / f"pre-migrate-{int(datetime.utcnow().timestamp())}.db"
    try:
        shutil.copy2(DB_PATH, snap_path)
        # Ротация: оставляем только последние 5 pre-migrate snapshots
        snaps = sorted(snap_dir.glob("pre-migrate-*.db"))
        for old in snaps[:-5]:
            try:
                old.unlink()
            except Exception:
                pass
        logger.info("pre-migrate snapshot: %s", snap_path)
    except Exception as e:
        logger.warning("pre-migrate snapshot failed: %s", e, exc_info=True)


async def init_db():
    # Snapshot перед миграциями (idempotent — если ALTER TABLE некритичный,
    # snapshot всё равно ротируется через 5 запусков)
    await _pre_migrate_snapshot()
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL — persistent, переживает рестарт; устанавливается один раз.
        # synchronous=NORMAL — fsync только при checkpoint, +производительность,
        # минимальный risk на современных SSD (worst case — теряется последняя
        # 1-2 минуты транзакций при power-loss).
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                product_type TEXT    NOT NULL,
                plan         TEXT    NOT NULL,
                stars_paid   INTEGER NOT NULL,
                payment_id   TEXT,
                status       TEXT DEFAULT 'pending',
                vpn_username TEXT,
                expires_at   TIMESTAMP,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL DEFAULT 'Сервер',
                location   TEXT NOT NULL DEFAULT '🌍',
                host       TEXT NOT NULL,
                user       TEXT NOT NULL DEFAULT 'root',
                password   TEXT,
                key_path   TEXT,
                protocol   TEXT NOT NULL DEFAULT 'awg',
                is_active  INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                plan       TEXT NOT NULL,
                payment_id TEXT UNIQUE,
                stars_paid INTEGER NOT NULL DEFAULT 0,
                status     TEXT NOT NULL DEFAULT 'active',
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                server_id       INTEGER,
                protocol        TEXT NOT NULL DEFAULT 'awg',
                peer_name       TEXT,
                config_data     TEXT,
                vless_uuid      TEXT,
                status          TEXT NOT NULL DEFAULT 'empty',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
                FOREIGN KEY (server_id) REFERENCES servers(id)
            )
        """)
        # Audit log: запись каждого admin-действия. Compliance + post-incident.
        # Намеренно простая schema: (admin_id, action, target, details JSON).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id   INTEGER NOT NULL,
                action     TEXT    NOT NULL,
                target     TEXT,
                details    TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_admin ON audit_log(admin_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                category   TEXT NOT NULL,
                message    TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS esim_profiles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                order_id        INTEGER,
                order_no        TEXT NOT NULL,
                tx_id           TEXT NOT NULL UNIQUE,
                esim_tran_no    TEXT UNIQUE,
                iccid           TEXT,
                package_code    TEXT NOT NULL,
                package_name    TEXT,
                location_code   TEXT,
                wholesale_price INTEGER,
                ac              TEXT,
                qr_url          TEXT,
                short_url       TEXT,
                smdp_address    TEXT,
                matching_id     TEXT,
                apn             TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                smdp_status     TEXT,
                esim_status     TEXT,
                total_volume    INTEGER,
                used_volume     INTEGER NOT NULL DEFAULT 0,
                expire_at       TIMESTAMP,
                activated_at    TIMESTAMP,
                last_sync_at    TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_esim_user ON esim_profiles(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_esim_order_no ON esim_profiles(order_no)"
        )
        # Индекс для cleanup_stuck_activating_slots — без него startup
        # делает full table scan по configs (worst case 10k+ строк, 5+ сек).
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_configs_status_created ON configs(status, created_at)"
        )
        # Lookups, бьющие в эти индексы: get_active_subscription (user_id),
        # try_award_referral_bonus + ref-стата (referred_by), и
        # get_configs_for_subscription / revoke-flows (subscription_id).
        # Без них — full table scan при росте до десятков тысяч строк.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_configs_subscription_id ON configs(subscription_id)"
        )
        await _migrate(db)
        # idx_users_referred_by must come after _migrate — referred_by column
        # is added by the migration and doesn't exist in the base CREATE TABLE.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)"
        )
        await db.commit()

    # Автозаполнение дефолтного сервера из env если таблица пустая
    await _seed_default_server()


async def _migrate(db: aiosqlite.Connection):
    """Добавляет новые колонки в существующие таблицы."""
    # orders
    async with db.execute("PRAGMA table_info(orders)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for col, defn in [("vpn_username", "TEXT"), ("expires_at", "TIMESTAMP")]:
        if col not in cols:
            await db.execute(f"ALTER TABLE orders ADD COLUMN {col} {defn}")

    # servers — agent fields + metadata
    async with db.execute("PRAGMA table_info(servers)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for col, defn in [
        ("name",         "TEXT NOT NULL DEFAULT 'Сервер'"),
        ("location",     "TEXT NOT NULL DEFAULT '🌍'"),
        ("flag",         "TEXT NOT NULL DEFAULT '🌍'"),
        ("city",         "TEXT NOT NULL DEFAULT ''"),
        ("agent_url",    "TEXT"),
        ("agent_token",  "TEXT"),
        ("wg_pubkey",    "TEXT"),
        ("capacity",     "INTEGER NOT NULL DEFAULT 100"),
        ("active_peers", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in cols:
            await db.execute(f"ALTER TABLE servers ADD COLUMN {col} {defn}")

    # configs — peer tracking fields
    async with db.execute("PRAGMA table_info(configs)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    for col, defn in [
        ("wg_pubkey",   "TEXT"),
        ("assigned_ip", "TEXT"),
        ("rx_bytes",    "INTEGER NOT NULL DEFAULT 0"),
        ("tx_bytes",    "INTEGER NOT NULL DEFAULT 0"),
        ("last_seen",   "TIMESTAMP"),
        ("label",       "TEXT"),
    ]:
        if col not in cols:
            await db.execute(f"ALTER TABLE configs ADD COLUMN {col} {defn}")

    # payments table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            subscription_id INTEGER,
            method          TEXT NOT NULL,
            amount_usd      REAL,
            stars           INTEGER,
            tx_id           TEXT,
            status          TEXT NOT NULL DEFAULT 'completed',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # payments — refund tracking. Без этого Stars-refund при provision fail
    # может быть вызван дважды (retry) → flood-control от Telegram + ложное
    # "звёзды возвращены" сообщение юзеру.
    async with db.execute("PRAGMA table_info(payments)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "refunded_at" not in cols:
        await db.execute("ALTER TABLE payments ADD COLUMN refunded_at TIMESTAMP")

    # subscriptions — pending_plan, expiry reminders
    async with db.execute("PRAGMA table_info(subscriptions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "pending_plan" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN pending_plan TEXT")
    if "reminded_3d" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN reminded_3d INTEGER NOT NULL DEFAULT 0")
    if "reminded_1d" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN reminded_1d INTEGER NOT NULL DEFAULT 0")
    # Reminder за 3 дня до конца grace_until — без него юзер в grace получает
    # 0 уведомлений до полного закрытия доступа через 14 дней.  Retention loss.
    if "reminded_grace_3d" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN reminded_grace_3d INTEGER NOT NULL DEFAULT 0")
    # Tracking: какому рефереру был начислен бонус за эту подписку.
    # NULL = бонус не начислен, иначе referrer_id. Нужно для rollback при refund.
    if "ref_bonus_awarded_to" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN ref_bonus_awarded_to INTEGER")
    if "ref_bonus_days_awarded" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN ref_bonus_days_awarded INTEGER NOT NULL DEFAULT 0")
    # Refund tracking на уровне подписки. payments.refunded_at был добавлен в
    # P0-5, но MRR/аналитика смотрит на subscriptions — без зеркала там
    # отчёты показывают "active" / "expired" по refunded подпискам как доход.
    if "refunded_at" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN refunded_at TIMESTAMP")
    if "amount_rub" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN amount_rub INTEGER NOT NULL DEFAULT 0")
    if "grace_until" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN grace_until TEXT")

    # support_tickets — admin_msg_id for reply relay
    async with db.execute("PRAGMA table_info(support_tickets)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "admin_msg_id" not in cols:
        await db.execute("ALTER TABLE support_tickets ADD COLUMN admin_msg_id INTEGER")

    # users — referral tracking + sub_token (for subscription URL)
    async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "referred_by" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    if "ref_bonus_days" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN ref_bonus_days INTEGER NOT NULL DEFAULT 0")
    if "sub_token" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN sub_token TEXT")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sub_token ON users(sub_token)")
    # is_banned — admin-выставляемый флаг, блокирует новые покупки / триал.
    # banned_at оставлен NULL пока юзер не забанен; ставится в CURRENT_TIMESTAMP.
    if "is_banned" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
    if "banned_at" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN banned_at TIMESTAMP")
    if "banned_reason" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN banned_reason TEXT")
    # users.email — для Lava.top (там нет custom payload, идентификация по email).
    # NULL пока юзер не платил через Lava. Используем для recurring сверки.
    if "email" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN email TEXT")

    # subscriptions — recurring tracking для Lava.top
    async with db.execute("PRAGMA table_info(subscriptions)") as cur:
        sub_cols = {row[1] for row in await cur.fetchall()}
    # parent_contract_id — UUID контракта Lava, по нему recurring webhook'и
    # коррелируем с нашей sub-row. NULL = подписка не recurring (one-time / Stars / CryptoBot).
    if "parent_contract_id" not in sub_cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN parent_contract_id TEXT")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_parent_contract "
            "ON subscriptions(parent_contract_id)"
        )
    # auto_renew — флаг что подписка будет продлеваться автоматически (Lava recurring).
    # При отмене из Lava-кабинета или нашего UI — ставится в 0, существующий период дослужит.
    if "auto_renew" not in sub_cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN auto_renew INTEGER NOT NULL DEFAULT 0")
    # payment_provider — 'stars' | 'cryptobot' | 'cryptomus' | 'lavatop' | 'trial' | 'gift'.
    # Для аналитики + чтобы знать какой refund-API использовать.
    if "payment_provider" not in sub_cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN payment_provider TEXT")
    # reminded_renewal_3d — отдельный флаг для напоминания о предстоящем
    # auto-charge (Lava/Stars recurring) за 3 дня. Не путать с reminded_3d
    # который про истечение обычной подписки. Сбрасывается в 0 при extend
    # (расширение через successful renewal) — следующий цикл стартует.
    if "reminded_renewal_3d" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN reminded_renewal_3d INTEGER NOT NULL DEFAULT 0"
        )

    # server_health_log — sparse time-series of up/down probes per server.
    # Источник для расчёта uptime % и страницы /status.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS server_health_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id   INTEGER NOT NULL,
            checked_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status      TEXT NOT NULL,         -- 'up' | 'down' | 'unknown'
            latency_ms  INTEGER,
            error       TEXT,
            FOREIGN KEY (server_id) REFERENCES servers(id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_health_server_time ON server_health_log(server_id, checked_at)")

    # incidents — открытые/закрытые периоды простоя.
    # Один incident на сервер: started_at при первом 'down', resolved_at при первом
    # 'up' после down. Если сервер ещё лежит — resolved_at NULL.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id    INTEGER NOT NULL,
            started_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at  TIMESTAMP,
            duration_sec INTEGER,
            FOREIGN KEY (server_id) REFERENCES servers(id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_incidents_server ON incidents(server_id, started_at DESC)")


async def _seed_default_server():
    """Если серверов нет — добавляет дефолтный из переменных окружения."""
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) FROM servers") as cur:
            count = (await cur.fetchone())[0]
        if count > 0:
            return
        from config import VPN_SERVER_HOST, VPN_SERVER_USER, VPN_SERVER_PASS, VPN_SERVER_KEY
        if VPN_SERVER_HOST:
            await db.execute(
                """INSERT INTO servers (name, location, host, user, password, key_path, protocol)
                   VALUES (?, ?, ?, ?, ?, ?, 'awg')""",
                ("США #1", "🇺🇸 США", VPN_SERVER_HOST,
                 VPN_SERVER_USER, VPN_SERVER_PASS or None, VPN_SERVER_KEY or None),
            )
            await db.commit()


# ── users ──────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, username: str | None, first_name: str):
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name),
        )
        await db.commit()


# ── orders ─────────────────────────────────────────────────────────────────────

async def create_order(user_id, product_type, plan, stars_paid,
                       vpn_username=None, expires_at=None) -> int:
    async with _connect() as db:
        cur = await db.execute(
            """INSERT INTO orders (user_id, product_type, plan, stars_paid, vpn_username, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, product_type, plan, stars_paid, vpn_username,
             expires_at.isoformat() if expires_at else None),
        )
        await db.commit()
        return cur.lastrowid


async def complete_order(order_id: int, payment_id: str):
    async with _connect() as db:
        await db.execute(
            "UPDATE orders SET status='completed', payment_id=? WHERE id=?",
            (payment_id, order_id),
        )
        await db.commit()


async def get_expired_orders() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, user_id, vpn_username FROM orders
            WHERE product_type='vpn' AND status='completed'
              AND expires_at IS NOT NULL AND expires_at <= ?
        """, (datetime.utcnow().isoformat(),)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_order_expired(order_id: int):
    async with _connect() as db:
        await db.execute("UPDATE orders SET status='expired' WHERE id=?", (order_id,))
        await db.commit()


async def get_stats() -> tuple[int, int, int]:
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('completed','expired')"
        ) as cur:
            orders = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COALESCE(SUM(stars_paid),0) FROM orders WHERE status IN ('completed','expired')"
        ) as cur:
            stars = (await cur.fetchone())[0]
    return users, orders, stars


# ── servers ────────────────────────────────────────────────────────────────────

async def get_servers_by_protocol(protocol: str) -> list[dict]:
    """Активные серверы для протокола (AWG или VLESS)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM servers "
            "WHERE protocol=? AND is_active=1 ORDER BY id",
            (protocol,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_server_by_id(server_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM servers WHERE id=?", (server_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── subscriptions ──────────────────────────────────────────────────────────────

async def create_subscription(
    user_id, plan, payment_id, stars_paid, expires_at, amount_rub: int = 0,
    *,
    parent_contract_id: str | None = None,
    auto_renew: bool = False,
    payment_provider: str | None = None,
) -> int | None:
    """Создаёт подписку. Возвращает sub_id или None если payment_id уже использован
    (UNIQUE-constraint сработал → дубль платежа от Telegram, идемпотентный no-op).

    Lava recurring: передаём parent_contract_id (UUID контракта Lava) и
    auto_renew=True. По parent_contract_id потом коррелируем recurring webhook'и.

    payment_provider — 'stars'/'cryptobot'/'cryptomus'/'lavatop'/'trial'/'gift'.
    """
    import sqlite3
    try:
        async with _connect() as db:
            cur = await db.execute(
                """INSERT INTO subscriptions
                   (user_id, plan, payment_id, stars_paid, amount_rub, expires_at,
                    parent_contract_id, auto_renew, payment_provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, plan, payment_id, stars_paid, amount_rub, expires_at.isoformat(),
                 parent_contract_id, 1 if auto_renew else 0, payment_provider),
            )
            await db.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


async def get_subscription_by_payment_id(payment_id: str) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE payment_id=?", (payment_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_expired_subscriptions() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, user_id, plan, expires_at, pending_plan FROM subscriptions
            WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= ?
        """, (datetime.utcnow().isoformat(),)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_subscription_expired(subscription_id: int):
    """Помечает подписку expired. Заодно сбрасывает pending_plan —
    он стал мёртвым атрибутом (downgrade некуда применять, sub закрыта)."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET status='expired', pending_plan=NULL WHERE id=?",
            (subscription_id,),
        )
        await db.commit()


async def mark_subscription_refunded(subscription_id: int):
    """Помечает подписку как возвращённую. Используется при Stars refund /
    manual CryptoBot refund. Отличается от 'expired' тем что MRR-аналитика
    и referral logic не должны считать refunded как реальный доход."""
    async with _connect() as db:
        await db.execute(
            """UPDATE subscriptions
               SET status='refunded', refunded_at=CURRENT_TIMESTAMP, pending_plan=NULL
               WHERE id=?""",
            (subscription_id,),
        )
        await db.commit()


async def extend_subscription(subscription_id: int, days: int) -> dict | None:
    """Добавляет `days` дней к expires_at.  Если sub была в grace — возвращает
    status='active', очищает grace_until.  Возвращает обновлённую запись (id,
    user_id, plan, status, expires_at) или None если sub не найдена.

    Используется админкой для compensation gifts.  Atomic в одной транзакции,
    чтобы scheduler не успел grace-expire-нуть подписку посередине.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM subscriptions WHERE id=?", (subscription_id,)
        ) as cur:
            if not await cur.fetchone():
                return None
        await db.execute(
            """UPDATE subscriptions
               SET expires_at = CASE
                       WHEN expires_at IS NULL OR expires_at < datetime('now')
                       THEN datetime('now', ?)
                       ELSE datetime(expires_at, ?)
                   END,
                   status = CASE WHEN status='grace' THEN 'active' ELSE status END,
                   grace_until = CASE WHEN status='grace' THEN NULL ELSE grace_until END
               WHERE id=?""",
            (f"+{days} days", f"+{days} days", subscription_id),
        )
        await db.commit()
        async with db.execute(
            "SELECT id, user_id, plan, status, expires_at FROM subscriptions WHERE id=?",
            (subscription_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_user_banned(user_id: int, banned: bool, reason: str | None = None) -> bool:
    """Ставит/снимает is_banned флаг.  Возвращает True если строка обновлена.

    Бан = silent block: бот при /start и попытках покупки покажет красивое
    "доступ ограничен"; конфиги остаются работающими до естественного expiry
    (нет принудительного revoke — это отдельное решение через refund/extend=0).
    """
    async with _connect() as db:
        cur = await db.execute(
            """UPDATE users
               SET is_banned=?, banned_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                   banned_reason=?
               WHERE id=?""",
            (1 if banned else 0, 1 if banned else 0, reason if banned else None, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_user_banned(user_id: int) -> bool:
    """Быстрая проверка для гейтов в handlers (start/vpn/payments)."""
    async with _connect() as db:
        async with db.execute(
            "SELECT is_banned FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def mark_subscription_grace(subscription_id: int, grace_until: str):
    """Переводит подписку в grace-period (14 дней низкой скорости)."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET status='grace', grace_until=? WHERE id=?",
            (grace_until, subscription_id),
        )
        await db.commit()


async def get_grace_expired_subscriptions() -> list[dict]:
    """Подписки в grace-state у которых grace_until уже прошёл."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, user_id, plan FROM subscriptions
            WHERE status='grace' AND grace_until IS NOT NULL AND grace_until <= ?
        """, (datetime.utcnow().isoformat(),)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def ensure_grace_column():
    """Миграция: добавляет grace_until в subscriptions если её нет."""
    async with _connect() as db:
        async with db.execute("PRAGMA table_info(subscriptions)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "grace_until" not in cols:
            await db.execute("ALTER TABLE subscriptions ADD COLUMN grace_until TEXT")
            await db.commit()


# ── configs / slots ────────────────────────────────────────────────────────────

async def create_config_record(subscription_id, user_id,
                                protocol="awg", server_id=None) -> int:
    """Создаёт пустой слот. Возвращает id."""
    async with _connect() as db:
        cur = await db.execute(
            """INSERT INTO configs (subscription_id, user_id, protocol, server_id, status)
               VALUES (?, ?, ?, ?, 'empty')""",
            (subscription_id, user_id, protocol, server_id),
        )
        await db.commit()
        return cur.lastrowid


async def activate_config_slot(config_id: int, peer_name: str,
                                config_data: str, server_id: int | None = None,
                                wg_pubkey: str | None = None,
                                assigned_ip: str | None = None,
                                vless_uuid: str | None = None):
    """Переводит слот empty → active, записывает конфиг и сервер."""
    async with _connect() as db:
        await db.execute(
            """UPDATE configs
               SET peer_name=?, config_data=?, server_id=?, wg_pubkey=?,
                   assigned_ip=?, vless_uuid=?, status='active'
               WHERE id=?""",
            (peer_name, config_data, server_id, wg_pubkey, assigned_ip, vless_uuid, config_id),
        )
        await db.commit()


async def reset_config_slot(config_id: int):
    """
    Сбрасывает слот обратно в empty после отзыва конфига.
    Слот остаётся в подписке — пользователь может добавить новый конфиг.
    """
    async with _connect() as db:
        await db.execute(
            """UPDATE configs
               SET status='empty', peer_name=NULL, config_data=NULL,
                   server_id=NULL, vless_uuid=NULL
               WHERE id=?""",
            (config_id,),
        )
        await db.commit()


async def claim_config_slot_for_activation(config_id: int) -> bool:
    """Atomic claim: переводит слот из 'empty' в 'activating'.

    Защищает от race: два браузера одновременно жмут «Добавить» на одном
    слоте → оба прошли select-check status='empty' → оба вызывают
    provision_peer → два пира на агенте, один в БД, второй orphan.

    Возвращает True если claim удался (значит можно делать provision),
    False если слот уже в other-status (другая вкладка опередила).
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE configs SET status='activating' WHERE id=? AND status='empty'",
            (config_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_config_record(config_id: int):
    """Полностью удаляет config-запись из БД.
    Используется для cleanup orphan-слотов после failed provision."""
    async with _connect() as db:
        await db.execute("DELETE FROM configs WHERE id=?", (config_id,))
        await db.commit()


async def get_active_subscription_by_id(sub_id: int) -> dict | None:
    """Возвращает subscription по id (любой статус). Для re-check внутри
    critical sections где статус мог измениться параллельно (race)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE id=? LIMIT 1", (sub_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def apply_pending_plan_change(sub_id: int, new_plan: str):
    """Переключает plan подписки на pending_plan и сбрасывает pending_plan.

    Вызывается scheduler'ом когда подписка истекает а у юзера был
    запланирован downgrade (vpn_max → vpn_base). После этого следующая
    покупка/продление будет за новый тариф.
    """
    async with _connect() as db:
        # WHERE pending_plan=? — guard от race: если юзер успел сделать
        # upgrade параллельно, pending_plan уже NULL → UPDATE no-op, не
        # перезаписываем актуальный plan. apply_pending_plan_change
        # вызывается из scheduler на момент истечения подписки.
        await db.execute(
            "UPDATE subscriptions SET plan=?, pending_plan=NULL "
            "WHERE id=? AND pending_plan=?",
            (new_plan, sub_id, new_plan),
        )
        await db.commit()


# ── Lava.top recurring helpers ────────────────────────────────────────────────

async def set_user_email(user_id: int, email: str):
    """Сохраняет email юзера (для Lava-инвойсов + recurring tracking)."""
    async with _connect() as db:
        await db.execute("UPDATE users SET email=? WHERE id=?", (email, user_id))
        await db.commit()


async def get_user_email(user_id: int) -> str | None:
    async with _connect() as db:
        async with db.execute("SELECT email FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def get_user_id_by_email(email: str) -> int | None:
    """Lava webhook fallback: если email юзера реальный (а не tg-{id}@maxvpnesim.com),
    ищем user_id по сохранённой почте."""
    async with _connect() as db:
        async with db.execute("SELECT id FROM users WHERE email=? LIMIT 1", (email,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_subscription_by_parent_contract(contract_id: str) -> dict | None:
    """Lava recurring: для webhook'а с parentContractId находим нашу sub-row."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE parent_contract_id=? "
            "ORDER BY id DESC LIMIT 1",
            (contract_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def extend_subscription_expires_at(sub_id: int, new_expires_at: str, *, reset_status: bool = True):
    """Продлевает подписку (Lava recurring success). Сбрасывает status='active'
    если он был 'grace' — recurring деньги пришли вовремя или с задержкой,
    значит юзер хочет оставаться на VPN. Сбрасываются все reminded_*
    флаги — следующий цикл начнётся с чистого листа.
    """
    async with _connect() as db:
        if reset_status:
            await db.execute(
                "UPDATE subscriptions SET expires_at=?, status='active', "
                "grace_until=NULL, reminded_3d=0, reminded_1d=0, "
                "reminded_renewal_3d=0, reminded_grace_3d=0 WHERE id=?",
                (new_expires_at, sub_id),
            )
        else:
            await db.execute(
                "UPDATE subscriptions SET expires_at=?, reminded_renewal_3d=0 "
                "WHERE id=?",
                (new_expires_at, sub_id),
            )
        await db.commit()


async def get_recurring_renewal_due_soon(days_before: int = 3) -> list[dict]:
    """Returns recurring sub'ы у которых auto_renew=1 + expires_at <= now+N дней
    + reminded_renewal_3d=0. Используется в scheduler для напоминания о
    предстоящем автосписании (за 3 дня до charge'а).
    """
    cutoff = (datetime.utcnow() + timedelta(days=days_before)).isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, user_id, plan, payment_provider, expires_at, amount_rub,
                   parent_contract_id
            FROM subscriptions
            WHERE auto_renew=1
              AND payment_provider IN ('lavatop', 'stars')
              AND expires_at <= ?
              AND expires_at > ?
              AND COALESCE(reminded_renewal_3d, 0) = 0
              AND status IN ('active','grace')
        """, (cutoff, datetime.utcnow().isoformat())) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_renewal_reminded(sub_id: int):
    """Ставит reminded_renewal_3d=1 чтобы не слать второе напоминание."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET reminded_renewal_3d=1 WHERE id=?",
            (sub_id,),
        )
        await db.commit()


async def disable_auto_renew(sub_id: int):
    """Юзер отменил автопродление (из нашего UI или Lava-кабинета).
    Сама подписка дослужит до expires_at."""
    async with _connect() as db:
        await db.execute("UPDATE subscriptions SET auto_renew=0 WHERE id=?", (sub_id,))
        await db.commit()


async def get_recurring_sub_for_renewal(user_id: int, plan_key: str) -> dict | None:
    """Stars renewal lookup: ищем последнюю auto_renew подписку юзера на этот план.

    Используется в _handle_stars_renewal — мы получили renewal-charge от Telegram
    и нужно найти existing sub чтобы extend expires_at (а не создавать новую).
    Расширяем поиск со statuses ('active', 'grace') до ('active','grace','expired')
    потому что Telegram может прислать renewal даже если sub была короткое время expired.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM subscriptions
               WHERE user_id=? AND plan=? AND auto_renew=1 AND payment_provider='stars'
               ORDER BY id DESC LIMIT 1""",
            (user_id, plan_key),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def is_payment_refunded(tx_id: str) -> bool:
    """Проверка идемпотентности — refund уже был сделан?"""
    async with _connect() as db:
        async with db.execute(
            "SELECT refunded_at FROM payments WHERE tx_id=? AND refunded_at IS NOT NULL LIMIT 1",
            (tx_id,),
        ) as cur:
            row = await cur.fetchone()
            return row is not None


async def mark_payment_refunded(tx_id: str) -> bool:
    """Фиксирует факт успешного refund'а для идемпотентности.

    `WHERE refunded_at IS NULL` — защита от двойной отметки если webhook
    дойдёт повторно или админ вручную нажмёт refund после уже-успешного.
    Возвращает True если запись была обновлена впервые, False — повтор.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE payments SET refunded_at=CURRENT_TIMESTAMP, status='refunded' "
            "WHERE tx_id=? AND refunded_at IS NULL",
            (tx_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def audit_log_record(admin_id: int, action: str,
                             target: str | None = None,
                             details: str | None = None):
    """Записывает admin-действие в audit_log для compliance / forensics.
    Best-effort: ошибка записи не должна ломать саму команду."""
    try:
        async with _connect() as db:
            await db.execute(
                "INSERT INTO audit_log (admin_id, action, target, details) VALUES (?, ?, ?, ?)",
                (admin_id, action, target, details),
            )
            await db.commit()
    except Exception as e:
        logger.warning("audit_log write failed action=%s: %s", action, e, exc_info=True)


async def cleanup_stuck_activating_slots() -> int:
    """Сбрасывает в 'empty' слоты которые застряли в 'activating' >5 минут.

    Race: claim_config_slot_for_activation переводит slot в 'activating',
    потом идёт provision_peer (3-10 сек), потом activate_config_slot →
    'active'. Если бот рестартанётся между claim и activate — слот
    зависает 'activating' навсегда (юзер не может ни добавить, ни отозвать).

    Вызывается при старте бота: если activating-record старше 5 минут —
    активация точно провалилась, чистим в 'empty'. Возвращает count.
    """
    async with _connect() as db:
        cur = await db.execute(
            """UPDATE configs
               SET status='empty', peer_name=NULL, config_data=NULL,
                   server_id=NULL, vless_uuid=NULL
               WHERE status='activating'
                 AND created_at < datetime('now', '-5 minutes')"""
        )
        await db.commit()
        return cur.rowcount


async def get_user_configs(user_id: int) -> list[dict]:
    """Все слоты пользователя (empty + active) по активным подпискам."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                c.id,
                c.subscription_id,
                c.protocol,
                c.peer_name,
                c.status,
                c.config_data IS NOT NULL AS has_config,
                s.plan,
                s.expires_at,
                ROW_NUMBER() OVER (
                    PARTITION BY c.subscription_id, c.protocol
                    ORDER BY c.id
                ) AS slot_num
            FROM configs c
            JOIN subscriptions s ON c.subscription_id = s.id
            WHERE c.user_id=?
              AND c.status IN ('empty','active')
              AND s.status='active'
            ORDER BY s.created_at DESC, c.protocol DESC, c.id ASC
        """, (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_configs_for_subscription(subscription_id: int) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, peer_name, protocol, server_id, assigned_ip, vless_uuid, config_data FROM configs "
            "WHERE subscription_id=? AND status='active'",
            (subscription_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_config_by_id(config_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM configs WHERE id=?", (config_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def revoke_config(config_id: int):
    """Legacy alias — теперь просто сбрасывает в empty."""
    await reset_config_slot(config_id)


async def get_subscription_by_id(sub_id: int) -> dict | None:
    """Возвращает подписку по id или None. Используется для проверки
    user_id при апгрейде (sec audit H6)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, plan, status, expires_at, grace_until, pending_plan FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_subscriptions_by_plan(user_id: int, plan: str, status: str = "active") -> list[dict]:
    """Список подписок юзера определённого плана и статуса. Используется чтобы
    найти активный триал когда юзер платит за тариф."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, plan, status, expires_at, created_at FROM subscriptions
               WHERE user_id=? AND plan=? AND status=?
               ORDER BY created_at DESC""",
            (user_id, plan, status),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_active_subscription(user_id: int) -> dict | None:
    """Возвращает активную подписку пользователя или None.

    Включает status IN ('active', 'grace') — grace это «истекла, но 14 дней
    на 256 кбит/с»; для UX и provisioning это всё ещё валидная подписка с
    живыми пирами. UI отличает по `grace_until IS NOT NULL`.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, plan, stars_paid, status, expires_at, pending_plan, created_at, grace_until
            FROM subscriptions
            WHERE user_id=? AND status IN ('active', 'grace')
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_last_expired_subscription(user_id: int) -> dict | None:
    """Возвращает последнюю истёкшую подписку пользователя или None."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, plan, stars_paid, status, expires_at, pending_plan, created_at
            FROM subscriptions
            WHERE user_id=? AND status='expired'
            ORDER BY created_at DESC LIMIT 1
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def change_subscription_plan(sub_id: int, new_plan: str, user_id: int,
                                    awg_delta: int, vless_delta: int,
                                    wg_delta: int = 0):
    """
    Немедленно меняет план подписки (апгрейд).
    Добавляет новые пустые слоты если awg_delta/vless_delta/wg_delta > 0.
    Снимает pending_plan если он был.

    Если подписка в grace (256 кбит/с после истечения) — возвращает её в active
    с продлением expires_at на 30 дней. Иначе апгрейд из grace оставил бы юзера
    на 256 кбит/с с новым планом — UI «Plan: Max», фактически throttle.

    Sec/edge audit C4 (15.05): caller'у (handlers/vpn.py:_apply_plan_upgrade)
    после этого вызова нужно отдельно вызвать unthrottle на vpnctl_client
    чтобы вернуть AWG-пиры с tc, и переместить VLESS из vless-grace inbound
    обратно в vless-base/max — это делается там, потому что требует agent_url.
    """
    async with _connect() as db:
        # Если был в grace — продлеваем active на полные 30 дней от now
        await db.execute(
            """UPDATE subscriptions
               SET plan=?, pending_plan=NULL,
                   status='active',
                   grace_until=NULL,
                   expires_at = CASE
                       WHEN status='grace' THEN datetime('now', '+30 days')
                       ELSE expires_at
                   END
               WHERE id=?""",
            (new_plan, sub_id),
        )
        for proto, delta in (("awg", awg_delta), ("vless", vless_delta), ("wg", wg_delta)):
            for _ in range(max(0, delta)):
                await db.execute(
                    "INSERT INTO configs (subscription_id, user_id, protocol, status) "
                    "VALUES (?, ?, ?, 'empty')",
                    (sub_id, user_id, proto),
                )
        await db.commit()


async def schedule_plan_change(sub_id: int, pending_plan: str | None):
    """
    Ставит (или снимает) запланированный даунгрейд на следующий месяц.
    pending_plan=None — отменить запланированное изменение.
    """
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET pending_plan=? WHERE id=?",
            (pending_plan, sub_id),
        )
        await db.commit()


async def has_active_subscription(user_id: int) -> bool:
    """True если у пользователя есть активная подписка."""
    sub = await get_active_subscription(user_id)
    return sub is not None


# ── support_tickets ────────────────────────────────────────────────────────────

async def create_support_ticket(user_id: int, category: str, message: str) -> int:
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO support_tickets (user_id, category, message) VALUES (?, ?, ?)",
            (user_id, category, message),
        )
        await db.commit()
        return cur.lastrowid


async def update_ticket_admin_msg(ticket_id: int, admin_msg_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE support_tickets SET admin_msg_id=? WHERE id=?",
            (admin_msg_id, ticket_id),
        )
        await db.commit()


async def get_ticket_by_admin_msg(admin_msg_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM support_tickets WHERE admin_msg_id=?", (admin_msg_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_ticket_by_id(ticket_id: int) -> dict | None:
    """Возвращает тикет с данными юзера для админ-панели."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT t.id, t.user_id, t.category, t.message, t.status,
                      t.created_at, t.admin_msg_id,
                      u.username, u.first_name
               FROM support_tickets t
               JOIN users u ON u.id = t.user_id
               WHERE t.id=?""",
            (ticket_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def close_ticket(ticket_id: int):
    """Закрывает тикет — простой UPDATE статуса. Идемпотентно (close-on-closed = ok)."""
    async with _connect() as db:
        await db.execute(
            "UPDATE support_tickets SET status='closed' WHERE id=?",
            (ticket_id,),
        )
        await db.commit()


# ── expiry reminders ───────────────────────────────────────────────────────────

async def get_subscriptions_expiring_soon(days: int) -> list[dict]:
    """Возвращает активные подписки, истекающие через `days` дней (±12 ч).

    `datetime(expires_at)` нормализует формат: aiosqlite сохраняет datetime
    как ISO с 'T'-разделителем (`2026-05-19T00:42:00.x`), а `datetime('now', …)`
    возвращает с пробелом (`2026-05-19 00:42:00`).  Без normalization строковое
    сравнение ломалось на дне-граничном времени (T=0x54 > space=0x20).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        col = "reminded_3d" if days >= 2 else "reminded_1d"
        async with db.execute(
            f"""SELECT * FROM subscriptions
                WHERE status='active'
                AND {col}=0
                AND datetime(expires_at) > datetime('now', '+{days-1} days')
                AND datetime(expires_at) < datetime('now', '+{days} days')""",
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_reminded(sub_id: int, days: int):
    col = "reminded_3d" if days >= 2 else "reminded_1d"
    async with _connect() as db:
        await db.execute(f"UPDATE subscriptions SET {col}=1 WHERE id=?", (sub_id,))
        await db.commit()


async def get_subscriptions_grace_ending_soon(days: int = 3) -> list[dict]:
    """Подписки в grace у которых `grace_until` истечёт через `days` дней.
    Используется для напоминания «3 дня до полного закрытия — успей продлить».
    Без него юзер в grace не получает уведомлений и пропускает окно retention.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT * FROM subscriptions
                WHERE status='grace'
                AND reminded_grace_3d=0
                AND grace_until IS NOT NULL
                AND datetime(grace_until) > datetime('now', '+{days-1} days')
                AND datetime(grace_until) < datetime('now', '+{days} days')""",
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_grace_reminded(sub_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET reminded_grace_3d=1 WHERE id=?", (sub_id,)
        )
        await db.commit()


async def renew_subscription_from_grace(sub_id: int, days: int = 30) -> dict | None:
    """Продление из grace-состояния: status→active, grace_until=NULL,
    expires_at = now + days.  Сбрасывает reminded-флаги (новый период,
    новые напоминания).

    Возвращает обновлённую запись или None если sub не найдена.

    Caller (handlers/vpn.py:_deliver_vpn) после этого должен вызвать
    unthrottle/move через vpnctl_client — мы только меняем БД, агент
    отдельно (см. _apply_plan_upgrade для образца).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM subscriptions WHERE id=?", (sub_id,)
        ) as cur:
            if not await cur.fetchone():
                return None
        await db.execute(
            f"""UPDATE subscriptions
                SET status='active',
                    grace_until=NULL,
                    expires_at=datetime('now', '+{days} days'),
                    reminded_3d=0,
                    reminded_1d=0,
                    reminded_grace_3d=0
                WHERE id=?""",
            (sub_id,),
        )
        await db.commit()
        async with db.execute(
            "SELECT id, user_id, plan, status, expires_at FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ── referrals ─────────────────────────────────────────────────────────────────

async def set_referred_by(user_id: int, referrer_id: int):
    """Записывает реферера только если у пользователя его ещё нет.

    Защита от self-referral (юзер кидает свою ссылку себе же через
    другой аккаунт/VPN): silent reject.
    """
    if user_id == referrer_id:
        return  # self-referral — gaming
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL",
            (referrer_id, user_id),
        )
        await db.commit()


async def get_referral_stats(referrer_id: int) -> dict:
    """Сколько пользователей привёл реферер и сколько из них купили."""
    async with _connect() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=?", (referrer_id,)
        ) as cur:
            invited = (await cur.fetchone())[0]
        async with db.execute(
            """SELECT COUNT(DISTINCT u.id) FROM users u
               JOIN subscriptions s ON s.user_id=u.id
               WHERE u.referred_by=? AND s.status IN ('active','expired')""",
            (referrer_id,),
        ) as cur:
            converted = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT ref_bonus_days FROM users WHERE id=?", (referrer_id,)
        ) as cur:
            row = await cur.fetchone()
            bonus_days = row[0] if row else 0
    return {"invited": invited, "converted": converted, "bonus_days": bonus_days}


async def get_best_server(protocol: str) -> dict | None:
    """Сервер с наименьшей загрузкой для данного протокола."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        proto_field = "awg" if protocol == "awg" else "vless"
        async with db.execute("""
            SELECT * FROM servers
            WHERE protocol=? AND is_active=1 AND agent_url IS NOT NULL
            ORDER BY (CAST(active_peers AS REAL) / capacity) ASC
            LIMIT 1
        """, (proto_field,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_active_servers(protocol: str) -> list[dict]:
    """Все active сервера для протокола с настроенным agent_url.
    Используется для multi-location VLESS provisioning: один UUID
    реплицируется на каждый сервер, юзер видит N локаций в sub-URL."""
    proto_field = "awg" if protocol == "awg" else "vless" if protocol == "vless" else protocol
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM servers
            WHERE protocol=? AND is_active=1 AND agent_url IS NOT NULL
            ORDER BY (CAST(active_peers AS REAL) / capacity) ASC
        """, (proto_field,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_server_peer_count(server_id: int, delta: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE servers SET active_peers=MAX(0, active_peers+?) WHERE id=?",
            (delta, server_id),
        )
        await db.commit()


async def save_peer_to_config(config_id: int, server_id: int, wg_pubkey: str,
                               assigned_ip: str, config_data: str, label: str,
                               vless_uuid: str | None = None):
    """Активирует слот после успешного provision на агенте.

    `wg_pubkey` хранит «идентификатор пира на агенте» — для AWG это WG-pubkey,
    для VLESS это UUID (так уж исторически). Для VLESS дополнительно пишем
    UUID в отдельную колонку `vless_uuid`, чтобы scheduler мог идентифицировать
    пира при grace/expiry не полагаясь на label (с multi-location label
    уникален per-server, а UUID шарится между всеми локациями одного слота).
    """
    async with _connect() as db:
        await db.execute("""
            UPDATE configs SET
                server_id=?, wg_pubkey=?, assigned_ip=?,
                config_data=?, peer_name=?, label=?,
                vless_uuid=COALESCE(?, vless_uuid),
                status='active'
            WHERE id=?
        """, (server_id, wg_pubkey, assigned_ip, config_data, label, label,
              vless_uuid, config_id))
        await db.commit()


async def update_config_traffic(config_id: int, rx: int, tx: int, last_seen: str | None):
    async with _connect() as db:
        await db.execute(
            "UPDATE configs SET rx_bytes=?, tx_bytes=?, last_seen=? WHERE id=?",
            (rx, tx, last_seen, config_id),
        )
        await db.commit()


async def get_config_id_by_vless_uuid(vless_uuid: str) -> int | None:
    async with _connect() as db:
        async with db.execute(
            "SELECT id FROM configs WHERE vless_uuid=? LIMIT 1", (vless_uuid,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_active_vless_uuids_by_server(server_id: int) -> list[str]:
    """UUIDs of currently active VLESS configs on the given server.
    Used by sync job to tell agent which UUIDs are still paid for.

    ВКЛЮЧАЕТ grace-подписки: их пиры висят в `vless-grace` inbound 14 дней
    и не должны быть удалены агентом при hourly sync.
    """
    async with _connect() as db:
        async with db.execute(
            """SELECT c.vless_uuid FROM configs c
               JOIN subscriptions s ON c.subscription_id = s.id
               WHERE c.server_id=? AND c.protocol='vless' AND c.status='active'
                 AND c.vless_uuid IS NOT NULL AND c.vless_uuid != ''
                 AND s.status IN ('active', 'grace')""",
            (server_id,),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


async def get_active_vless_configs_with_plan() -> list[dict]:
    """Active VLESS configs along with the plan_key of their subscription.
    Used by quota-throttle scheduler."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT c.id AS config_id, c.user_id, c.server_id, c.vless_uuid, c.config_data,
                      c.rx_bytes, c.tx_bytes, s.plan AS plan_key, s.id AS subscription_id
               FROM configs c
               JOIN subscriptions s ON c.subscription_id = s.id
               WHERE c.protocol='vless' AND c.status='active'
                 AND c.vless_uuid IS NOT NULL AND c.vless_uuid != ''
                 AND s.status='active'"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_config_data(config_id: int, config_data: str):
    async with _connect() as db:
        await db.execute(
            "UPDATE configs SET config_data=? WHERE id=?",
            (config_data, config_id),
        )
        await db.commit()


async def get_vless_slots_missing_from_server(server_id: int) -> list[dict]:
    """Multi-location backfill: возвращает активные VLESS-слоты которые ещё
    не реплицированы на `server_id`. Для каждого выдаёт одну строку с
    (subscription_id, user_id, vless_uuid, plan, sub_status) — этого хватает
    провижить пир с тем же UUID на новом сервере.

    Идемпотентна: если слот уже на сервере — не возвращается. Используется
    кнопкой «Backfill VLESS» в админке при подключении нового VLESS-сервера.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT DISTINCT c.subscription_id, c.user_id, c.vless_uuid,
                      s.plan, s.status AS sub_status
               FROM configs c
               JOIN subscriptions s ON c.subscription_id = s.id
               WHERE c.protocol='vless' AND c.status='active'
                 AND c.vless_uuid IS NOT NULL AND c.vless_uuid != ''
                 AND s.status IN ('active', 'grace')
                 AND c.vless_uuid NOT IN (
                     SELECT vless_uuid FROM configs
                     WHERE server_id=? AND protocol='vless' AND status='active'
                       AND vless_uuid IS NOT NULL AND vless_uuid != ''
                 )""",
            (server_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_or_create_sub_token(user_id: int) -> str:
    """Returns the user's stable subscription token (creates one on first call)."""
    import secrets
    async with _connect() as db:
        async with db.execute("SELECT sub_token FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            return row[0]
        token = secrets.token_urlsafe(24)
        await db.execute("UPDATE users SET sub_token=? WHERE id=?", (token, user_id))
        await db.commit()
        return token


async def rotate_sub_token(user_id: int) -> str:
    """Issues a new sub_token, invalidating the previous subscription URL."""
    import secrets
    token = secrets.token_urlsafe(24)
    async with _connect() as db:
        await db.execute("UPDATE users SET sub_token=? WHERE id=?", (token, user_id))
        await db.commit()
    return token


async def get_user_by_sub_token(token: str) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE sub_token=? LIMIT 1", (token,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_vless_configs_for_user(user_id: int) -> list[dict]:
    """Active VLESS configs (with config_data) belonging to the user.
    Used to render subscription endpoint.

    Включает grace: `/sub/{token}` должен работать 14 дней после истечения —
    конфиг в config_data во время grace указывает на vless-grace inbound (порт 9453).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT c.id, c.config_data, c.peer_name, c.label, c.protocol
               FROM configs c
               JOIN subscriptions s ON c.subscription_id = s.id
               WHERE c.user_id=? AND c.protocol='vless' AND c.status='active'
                 AND s.status IN ('active', 'grace')
                 AND c.config_data IS NOT NULL AND c.config_data != ''
               ORDER BY c.id""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_user_configs_full(user_id: int) -> list[dict]:
    """Конфиги пользователя с данными сервера.

    Включает status IN ('active', 'grace') — во время grace-периода
    конфиги остаются доступны (с пониженной скоростью), пользователь
    должен видеть их в Mini App с предупреждением.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                c.id, c.subscription_id, c.protocol, c.peer_name, c.label,
                c.status, c.config_data, c.vless_uuid, c.wg_pubkey,
                c.assigned_ip, c.rx_bytes, c.tx_bytes, c.last_seen,
                s.plan, s.expires_at, s.status AS sub_status,
                srv.name AS server_name, srv.flag, srv.city,
                srv.host AS server_host,
                ROW_NUMBER() OVER (
                    PARTITION BY c.subscription_id, c.protocol ORDER BY c.id
                ) AS slot_num
            FROM configs c
            JOIN subscriptions s ON c.subscription_id = s.id
            LEFT JOIN servers srv ON c.server_id = srv.id
            WHERE c.user_id=?
              AND s.status IN ('active', 'grace')
            ORDER BY s.created_at DESC, c.protocol DESC, c.id ASC
        """, (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def record_payment(user_id: int, subscription_id: int, method: str,
                          stars: int = 0, amount_usd: float = 0.0, tx_id: str = ""):
    async with _connect() as db:
        await db.execute("""
            INSERT INTO payments (user_id, subscription_id, method, stars, amount_usd, tx_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, subscription_id, method, stars, amount_usd, tx_id))
        await db.commit()


async def get_configs_by_server(server_id: int) -> list[dict]:
    """Все активные конфиги на сервере (для suspend-all при истечении)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.id, c.wg_pubkey, c.vless_uuid, c.protocol,
                   c.subscription_id, c.user_id
            FROM configs c
            WHERE c.server_id=? AND c.status='active'
        """, (server_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_referral_bonus(referrer_id: int, days: int):
    """Начисляет дни бонуса рефереру и продлевает активную подписку."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET ref_bonus_days=ref_bonus_days+? WHERE id=?",
            (days, referrer_id),
        )
        modifier = f"+{days} days"
        await db.execute(
            "UPDATE subscriptions SET expires_at=datetime(expires_at, ?) WHERE user_id=? AND status='active'",
            (modifier, referrer_id),
        )
        await db.commit()


async def try_award_referral_bonus(user_id: int, days: int, paid_sub_id: int | None = None) -> int | None:
    """Если у юзера был приглашающий И это его первая ПЛАТНАЯ подписка —
    начисляет рефереру `days` дней бонуса и возвращает referrer_id.
    Иначе None.

    Триал (`plan='vpn_trial'`) не считается «первой покупкой».

    Если `paid_sub_id` передан — записываем в subscriptions.ref_bonus_awarded_to
    referrer_id, чтобы при refund можно было откатить через rollback_referral_bonus().
    """
    async with _connect() as db:
        async with db.execute(
            "SELECT referred_by FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        referrer_id = row[0] if row and row[0] else None
        if not referrer_id:
            return None

        # paid_count считает только реально оплаченные подписки (не refunded),
        # чтобы после refund + повторной покупки бонус всё-таки начислился.
        # Используем refunded_at как single source of truth (status='refunded'
        # выставляется одновременно через mark_subscription_refunded).
        async with db.execute(
            """SELECT COUNT(*) FROM subscriptions
               WHERE user_id=? AND plan!='vpn_trial' AND refunded_at IS NULL""",
            (user_id,),
        ) as cur:
            paid_count = (await cur.fetchone())[0]

        if paid_count != 1:
            return None

        # Double-award guard: если ранее уже начисляли бонус И он НЕ был
        # откатан (поле очищено при rollback_referral_bonus) — пропускаем.
        async with db.execute(
            """SELECT COUNT(*) FROM subscriptions
               WHERE user_id=? AND ref_bonus_awarded_to IS NOT NULL""",
            (user_id,),
        ) as cur:
            ever_awarded = (await cur.fetchone())[0]
        if ever_awarded > 0:
            logger.info(
                "referral skip: user %d уже получал бонус ранее (%d sub'ов)",
                user_id, ever_awarded,
            )
            return None

        # Bonus + extend в одной транзакции
        await db.execute(
            "UPDATE users SET ref_bonus_days=ref_bonus_days+? WHERE id=?",
            (days, referrer_id),
        )
        await db.execute(
            "UPDATE subscriptions SET expires_at=datetime(expires_at, ?) WHERE user_id=? AND status='active'",
            (f"+{days} days", referrer_id),
        )
        # Tracking — кому был начислен бонус, чтобы можно было откатить при refund
        if paid_sub_id is not None:
            await db.execute(
                """UPDATE subscriptions
                   SET ref_bonus_awarded_to=?, ref_bonus_days_awarded=?
                   WHERE id=?""",
                (referrer_id, days, paid_sub_id),
            )
        await db.commit()
        return referrer_id


async def rollback_referral_bonus(refunded_sub_id: int) -> tuple[int, int] | None:
    """Откатывает реферальный бонус для подписки, которая была refund-нута.

    Возвращает (referrer_id, days) если откат прошёл, None если бонус не был начислен.
    Использовать когда юзер вернул деньги через support — рефер получил +7 дней
    за пустой платёж, надо вычесть.

    Атомарность: claim-first pattern. Сначала UPDATE с WHERE ... IS NOT NULL
    очищает поля; если rowcount=0 — кто-то другой уже откатил, return None.
    Защита от двух одновременных /refund_ref вызовов.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        # Шаг 1: читаем awarded поля
        async with db.execute(
            """SELECT ref_bonus_awarded_to, ref_bonus_days_awarded
               FROM subscriptions WHERE id=?""",
            (refunded_sub_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["ref_bonus_awarded_to"]:
            return None

        referrer_id = row["ref_bonus_awarded_to"]
        days = row["ref_bonus_days_awarded"] or 0
        if days == 0:
            return None

        # Шаг 2: ATOMIC CLAIM — обнуляем поля при условии что они не NULL.
        # Если параллельный запрос уже сделал то же — rowcount=0, выходим
        # без двойного списания дней.
        claim_cur = await db.execute(
            """UPDATE subscriptions
               SET ref_bonus_awarded_to=NULL, ref_bonus_days_awarded=0
               WHERE id=? AND ref_bonus_awarded_to IS NOT NULL""",
            (refunded_sub_id,),
        )
        if claim_cur.rowcount == 0:
            await db.commit()
            return None  # race: другой запрос откатил первым

        # Шаг 3: claim успешен, делаем reverse
        await db.execute(
            """UPDATE users SET ref_bonus_days=MAX(0, ref_bonus_days-?)
               WHERE id=?""",
            (days, referrer_id),
        )
        await db.execute(
            """UPDATE subscriptions SET expires_at=datetime(expires_at, ?)
               WHERE user_id=? AND status='active'""",
            (f"-{days} days", referrer_id),
        )
        await db.commit()
        return referrer_id, days


# ── eSIM profiles ─────────────────────────────────────────────────────────────

async def create_esim_profile(user_id: int, order_id: int, tx_id: str,
                                package_code: str, package_name: str,
                                location_code: str, wholesale_price: int) -> int:
    """Создаёт запись eSIM-профиля сразу после place_order. Статус='pending'."""
    async with _connect() as db:
        cur = await db.execute(
            """INSERT INTO esim_profiles
               (user_id, order_id, order_no, tx_id, package_code, package_name,
                location_code, wholesale_price, status)
               VALUES (?, ?, '', ?, ?, ?, ?, ?, 'pending')""",
            (user_id, order_id, tx_id, package_code, package_name, location_code, wholesale_price),
        )
        await db.commit()
        return cur.lastrowid


async def set_esim_order_no(profile_id: int, order_no: str):
    async with _connect() as db:
        await db.execute(
            "UPDATE esim_profiles SET order_no=? WHERE id=?",
            (order_no, profile_id),
        )
        await db.commit()


async def fulfill_esim_profile(profile_id: int, esim_data: dict) -> bool:
    """Заполняет профиль данными от esimaccess (после получения /esim/query результата).
    Идемпотентно: если уже fulfilled — возвращает False."""
    ac          = esim_data.get("ac") or ""
    if not ac:
        return False
    # Парсим AC: "LPA:1$smdp.example.com$MATCHING_ID"
    smdp_addr, matching_id = "", ""
    parts = ac.split("$")
    if len(parts) == 3:
        smdp_addr, matching_id = parts[1], parts[2]

    async with _connect() as db:
        cur = await db.execute(
            """UPDATE esim_profiles SET
                   esim_tran_no=?, iccid=?, ac=?, qr_url=?, short_url=?,
                   smdp_address=?, matching_id=?, apn=?,
                   smdp_status=?, esim_status=?,
                   total_volume=?, expire_at=?,
                   status='ready'
               WHERE id=? AND status='pending'""",
            (
                esim_data.get("esimTranNo"),
                esim_data.get("iccid"),
                ac,
                esim_data.get("qrCodeUrl"),
                esim_data.get("shortUrl"),
                smdp_addr,
                matching_id,
                esim_data.get("apn"),
                esim_data.get("smdpStatus"),
                esim_data.get("esimStatus"),
                esim_data.get("totalVolume"),
                esim_data.get("expiredTime"),
                profile_id,
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def mark_esim_refunded_by_order(order_id: int):
    """Помечает eSIM-профиль как refunded по order_id из таблицы orders.
    Используется в _esim_refund_and_notify для трекинга refund'ов."""
    async with _connect() as db:
        await db.execute(
            "UPDATE esim_profiles SET status='refunded' WHERE order_id=?",
            (order_id,),
        )
        await db.commit()


async def mark_esim_failed(profile_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE esim_profiles SET status='failed' WHERE id=?", (profile_id,)
        )
        await db.commit()


async def get_esim_profile(profile_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM esim_profiles WHERE id=?", (profile_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_esim_by_order_no(order_no: str) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM esim_profiles WHERE order_no=? LIMIT 1", (order_no,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_esim_by_tran_no(esim_tran_no: str) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM esim_profiles WHERE esim_tran_no=? LIMIT 1",
            (esim_tran_no,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_esim_profiles(user_id: int) -> list[dict]:
    """Все eSIM-профили пользователя (новые сверху, не failed)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM esim_profiles
               WHERE user_id=? AND status != 'failed'
               ORDER BY created_at DESC""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_esim_profiles_for_usage_sync(limit: int = 200) -> list[dict]:
    """Активные профили с esim_tran_no — для batch-синка юзеджа."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, esim_tran_no, total_volume, used_volume FROM esim_profiles
               WHERE status='ready' AND esim_tran_no IS NOT NULL
                 AND (expire_at IS NULL OR expire_at > datetime('now'))
               ORDER BY last_sync_at IS NULL DESC, last_sync_at ASC
               LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_esim_usage(esim_tran_no: str, used_bytes: int):
    async with _connect() as db:
        await db.execute(
            """UPDATE esim_profiles
               SET used_volume=?, last_sync_at=CURRENT_TIMESTAMP
               WHERE esim_tran_no=?""",
            (used_bytes, esim_tran_no),
        )
        await db.commit()
