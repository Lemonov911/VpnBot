from __future__ import annotations
"""
SQLite через aiosqlite.

Жизненный цикл слота конфига:
  empty   → куплен, конфиг не создан
  active  → конфиг создан, работает
  (revoked удалён — отзыв сбрасывает слот в empty, слот не исчезает)
"""

import asyncio
import logging

import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "bot.db"


class _Sentinel:
    """Singleton-marker используемый функциями где нужен явный «параметр
    не задан» отдельно от None. Например в `schedule_plan_change(...,
    expected_previous=...)` — None означает «CAS against empty pending_plan»,
    а sentinel значит «без CAS, legacy fire-and-forget»."""
    __slots__ = ()
    def __repr__(self) -> str:  # pragma: no cover
        return "<UNSET>"


_SENTINEL = _Sentinel()


@asynccontextmanager
async def _connect():
    """aiosqlite.connect + busy_timeout 5s + foreign_keys=ON.

    `journal_mode=WAL` ставится один раз в `init_db()`. busy_timeout +
    foreign_keys = per-connection, ставятся здесь на каждом подключении.

    foreign_keys=ON enforce'ит ТОЛЬКО те FK, что объявлены в CREATE TABLE.
    Колонки добавленные через ALTER TABLE (users.referred_by, configs.server_id,
    кое-что в payments) не могут получить FK в SQLite — ALTER TABLE ADD COLUMN
    не поддерживает FOREIGN KEY. Эти колонки остаются plain INTEGER без
    cascade/restrict. Проверки runtime (e.g. SELECT exists) — единственный
    способ найти орфанные ссылки. См. _reconcile_orphans в scheduler.py.

    ⚠️ Direct `aiosqlite.connect(DB_PATH)` (вне _connect) НЕ получает
    busy_timeout/foreign_keys PRAGMA — health.py, trial.py etc. так делают
    осознанно (read-only or with их собственным PRAGMA).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


async def _pre_migrate_snapshot():
    """Snapshot БД перед миграциями. Если ALTER TABLE упадёт (constraints,
    disk space, etc), бот не стартует — но юзер может вручную восстановить
    snapshot из /opt/vpnbot/.snapshots/pre-migrate-*.db и откатить commit.

    Использует sqlite3.Connection.backup() вместо shutil.copy2 — корректно
    работает в WAL-режиме (shutil не копирует .db-wal/.db-shm и может дать
    inconsistent snapshot если есть незачекпоинченные транзакции).
    """
    import sqlite3 as _sqlite3
    if not DB_PATH.exists():
        return  # первая инициализация, нет что снапшотить
    snap_dir = DB_PATH.parent / ".snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap_path = snap_dir / f"pre-migrate-{int(datetime.utcnow().timestamp())}.db"

    def _backup_sync():
        src = _sqlite3.connect(str(DB_PATH))
        try:
            dst = _sqlite3.connect(str(snap_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _backup_sync)
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
        ("wg_pubkey",    "TEXT"),
        ("assigned_ip",  "TEXT"),
        ("rx_bytes",     "INTEGER NOT NULL DEFAULT 0"),
        ("tx_bytes",     "INTEGER NOT NULL DEFAULT 0"),
        ("last_seen",    "TIMESTAMP"),
        ("label",        "TEXT"),
        ("activated_at", "DATETIME"),
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
    # Admin-grant tracking: какой админ выдал бесплатную подписку этой строкой.
    # NULL для обычных платежей. is_free_grant=1 — это запись «подарка», amount=0
    # и метод='admin_grant'. Используется в admin/payments-вьюхе как маркер 🎁
    # и в аналитике (исключаем grant'ы из MRR/выручки).
    if "granted_by_admin_id" not in cols:
        await db.execute("ALTER TABLE payments ADD COLUMN granted_by_admin_id INTEGER")
    if "is_free_grant" not in cols:
        await db.execute("ALTER TABLE payments ADD COLUMN is_free_grant INTEGER NOT NULL DEFAULT 0")
    # UNIQUE на `tx_id` (partial — игнорит пустые/NULL) — payment-level
    # идемпотентность.  Audit 17.05 #1: без неё Lava/CryptoBot webhook retry
    # дважды записывал payments-row, а Stars/Lava recurring без проверки
    # `payments.tx_id` экстендили sub на 60 дней за 1 списание.
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_tx_id "
        "ON payments(tx_id) WHERE tx_id != '' AND tx_id IS NOT NULL"
    )
    # Индекс для admin/payments-вьюхи: фильтр grant'ов по конкретному админу
    # (показать «какие подписки выдал именно я») идёт без полного скана.
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_granted_admin "
        "ON payments(granted_by_admin_id) WHERE granted_by_admin_id IS NOT NULL"
    )

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
    # ref_bonus_redeemed_at — timestamp активации бонуса юзером (manual redeem
    # в Mini App «Мои бонусы»). Используется в rollback_referral_bonus чтобы
    # отличить «бонус ещё в банке» (NULL) vs «уже применён к expires_at» (не-NULL).
    # NULL = bonus pending в users.ref_bonus_days; refund вычитает оттуда.
    # NOT NULL = bonus уже сидит в sub.expires_at реферрера; refund -= sub.expires_at.
    if "ref_bonus_redeemed_at" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN ref_bonus_redeemed_at TIMESTAMP")
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
    # One-time migration: семантика поля ref_bonus_days меняется с «lifetime
    # earned (auto-applied)» на «pending bank, manual redeem». Существующие
    # значения УЖЕ были применены к sub.expires_at в старой логике — если
    # оставим как есть, юзер сможет «redeem'нуть» уже использованные дни →
    # double-dip. Сбрасываем флагом-маркером (создаём ref_bonus_v2_migrated
    # стeлс-столбец в schema_state — если ещё не сбрасывали, ставим 0).
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_state ("
        " key TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    # SELECT-then-UPDATE-then-INSERT-marker: если UPDATE упадёт (crash mid-flight),
    # маркер не ставится и миграция повторится на следующем старте. INSERT-first
    # вариант (старый) ставил маркер до UPDATE — partial failure → marker есть,
    # данные не мигрированы, никогда не отдиагностируешь.
    import sqlite3 as _sqlite
    try:
        async with db.execute(
            "SELECT 1 FROM schema_state WHERE key='ref_bonus_redeem_migration_v1'"
        ) as cur:
            already_done = await cur.fetchone() is not None
        if not already_done:
            await db.execute("UPDATE users SET ref_bonus_days=0 WHERE ref_bonus_days > 0")
            await db.execute(
                "INSERT INTO schema_state (key) VALUES ('ref_bonus_redeem_migration_v1')"
            )
            logger.info("ref_bonus_days reset to 0 (manual redeem semantics, one-time migration)")
    except _sqlite.OperationalError:
        pass  # schema_state может отсутствовать на очень старых БД
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
    # users.lang — Telegram language_code (например 'ru', 'en', 'en-US').
    # Заполняется из message.from_user.language_code при /start. NULL/неизвестный
    # → бот шлёт RU. См. services/i18n_bot.py.
    if "lang" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN lang TEXT")
    # traffic_source — first-touch attribution: utm:<code>, referral:<id>, direct.
    # Заполняется один раз на первый /start, дальше не перезаписывается. Источник
    # правды для метрик «откуда платящий пришёл» в админке /attribution.
    # Формат хранения с двоеточием (utm:tg_pythonist_jul) — namespace + payload,
    # позволяет различать каналы без отдельной колонки и сортировать по type.
    if "traffic_source" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN traffic_source TEXT")
    if "traffic_source_at" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN traffic_source_at TIMESTAMP")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_traffic_source ON users(traffic_source)"
    )
    # One-time backfill: ставим traffic_source='referral:<referrer_id>' для уже
    # зарегистрированных юзеров с referred_by IS NOT NULL — это retroactive
    # first-touch (раньше source не записывался, но факт что юзер пришёл по
    # реф-ссылке мы знаем из referred_by). Без этого первые недели после
    # деплоя весь старый трафик в админке отметится как 'direct' что искажает
    # картину "реф vs реклама".
    try:
        await db.execute(
            "INSERT INTO schema_state (key) VALUES ('traffic_source_backfill_v1')"
        )
        await db.execute(
            "UPDATE users SET traffic_source = 'referral:' || referred_by, "
            "                  traffic_source_at = created_at "
            "WHERE referred_by IS NOT NULL AND traffic_source IS NULL"
        )
        logger.info("traffic_source backfilled for users with referred_by (one-time)")
    except _sqlite.IntegrityError:
        pass  # уже backfilled раньше

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
    # auto_renew_disabled_at — timestamp когда auto_renew был отключён.
    # Нужен фронту для гейтинга «VPN работает до X (без автопродления)»-баннера:
    # для Stars one-time покупок payment_provider='stars' и auto_renew=0, но баннер
    # показывать не надо. Этот столбец = «был ли когда-то включён auto-renew».
    # NULL → юзер никогда не имел recurring; non-NULL → отменён в указанный момент.
    if "auto_renew_disabled_at" not in sub_cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN auto_renew_disabled_at TIMESTAMP")
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
    # winback_sent — флаг что win-back сообщение уже отправлено. Один раз на
    # истёкшую подписку чтобы не спамить. 0 = не отправлено, 1 = отправлено.
    if "winback_sent" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN winback_sent INTEGER NOT NULL DEFAULT 0"
        )
    # trial_nudge_sent — флаг engagement-сообщения на 2й день триала.
    # Шлётся через ~24-48ч после активации: «как VPN? Если не работает — поможем».
    # Момент пиковой мотивации — юзер уже попользовался, но ещё не решил продлевать.
    if "trial_nudge_sent" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN trial_nudge_sent INTEGER NOT NULL DEFAULT 0"
        )
    # trial_rolled_back — отличает «триал был провизионен но провижининг
    # упал → откатили» от «триал отработал и истёк по сроку».  Cooldown
    # 30 дней применяем только для второго случая: если provision_trial
    # упал на полпути, юзер должен иметь возможность сразу повторить.
    # Без этого флага после Fix #1 (scheduler помечает trial expired)
    # cooldown-фильтр `status != 'expired'` пропускал и rollback-случай
    # (вернуть сразу) и нормальное истечение (заблокировать на 30 дней).
    if "trial_rolled_back" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN trial_rolled_back INTEGER NOT NULL DEFAULT 0"
        )
    # reminded_quota_throttled — флаг что юзеру уже отправили уведомление о
    # throttling'е (квота исчерпана, скорость снижена). Нужен чтобы:
    # 1) не спамить при каждом тике scheduler'а (drift между rows одного sub'а
    #    мог приводить к повторным notify);
    # 2) знать что нужно отправить un-throttle сообщение когда квота обновится
    #    (новый расчётный месяц) — без флага мы бы не знали, отправлял ли мы
    #    уведомление в первый раз и стоит ли отправить «скорость восстановлена».
    if "reminded_quota_throttled" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN reminded_quota_throttled INTEGER NOT NULL DEFAULT 0"
        )
    # ref_bonus_applied_to_sub_id — id подписки реферрера, которой были
    # начислены бонусные дни при redeem_referral_bonus. Раньше rollback
    # реф-бонуса (audit R6) использовал subquery «найди newest active/grace
    # sub реферрера» — это могло вычесть дни из НЕ той подписки, или из
    # уже-другого тарифа реферрера. Теперь записываем точный target_sub_id
    # в момент redeem'а и rollback вычитает только из него.
    # NULL = legacy row (бонус редимнут до этого fix'a) ИЛИ бонус ещё не
    # редимнут (тогда дни в users.ref_bonus_days, см. rollback логику).
    if "ref_bonus_applied_to_sub_id" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN ref_bonus_applied_to_sub_id INTEGER"
        )
    # last_charge_failed_at — timestamp последнего failed recurring charge.
    # Выставляется webhook'ом subscription.recurring.payment.failed (Lava),
    # сбрасывается в NULL при успешном extend (см. extend_subscription_expires_at).
    # Фронт показывает yellow warning banner на /vpn пока флаг non-NULL
    # и auto_renew=1 — юзер успевает поменять карту до следующего retry.
    if "last_charge_failed_at" not in sub_cols:
        await db.execute(
            "ALTER TABLE subscriptions ADD COLUMN last_charge_failed_at TIMESTAMP NULL"
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

    # ── VLESS dynamic-subscription refactor (2026-05-21) ─────────────────────
    # Цель: уйти от модели "1 строка в configs = 1 peer на 1 сервере" для VLESS.
    # Раньше при добавлении нового сервера приходилось INSERT-ить N строк (по
    # одной на каждого юзера) + по N HTTP-вызовов на провижининг UUID — это
    # порождало баги (label-collision на admin'е 20.05) и hairy cleanup при
    # отказе одного из шагов.
    #
    # Новая модель:
    #   • users.vless_uuid — canonical credential юзера (один UUID на юзера).
    #   • servers.xray_* — параметры Reality каждого VLESS-сервера, по которым
    #     /sub/{token} строит URL'ы на лету (UUID юзера × все active VLESS
    #     серверы × tier из его подписки).
    #   • configs-rows для VLESS теперь нужны только для slot-учёта в UI
    #     (отображение "сколько слотов используется"); сами URL генерируются
    #     динамически.
    #
    # Полная архитектура — см. memory/mts-dpi.md и комментарии в
    # handle_user_subscription.

    # servers — параметры Reality, нужные для построения vless:// URL.
    # При первой раскатке колонки NULL — это сигнал "сервер ещё не настроен под
    # dynamic subscription, используем старый путь через config_data".
    async with db.execute("PRAGMA table_info(servers)") as cur:
        srv_cols = {row[1] for row in await cur.fetchall()}
    for col, defn in [
        ("xray_pubkey",         "TEXT"),
        ("xray_short_id",       "TEXT"),
        ("xray_sni",            "TEXT"),
        ("xray_dest",           "TEXT"),
        ("xray_fingerprint",    "TEXT NOT NULL DEFAULT 'chrome'"),
        ("xray_port_base",      "INTEGER"),
        ("xray_port_max",       "INTEGER"),
        ("xray_port_base_slow", "INTEGER"),
        ("xray_port_max_slow",  "INTEGER"),
        ("xray_port_grace",     "INTEGER"),
    ]:
        if col not in srv_cols:
            await db.execute(f"ALTER TABLE servers ADD COLUMN {col} {defn}")

    # servers.backfilled — флаг "новый VLESS-сервер прошёл multi-location
    # backfill". До backfill'а сервер выдавал vless:// URL в подписку, но
    # агент про существующие UUID не знал → Happ показывал «нет серверов».
    # Теперь URL отдаются только если backfilled=1.
    #
    # Новые сервера, добавленные через raw INSERT/админку, по умолчанию
    # получат backfilled=0 — admin обязан запустить POST /api/admin/
    # servers/{id}/backfill-vless ПЕРЕД тем как сервер появится в /sub/.
    # Существующие сервера на момент миграции считаем уже-задеплоенными
    # (UPDATE ниже устанавливает им backfilled=1, иначе живой прод сломался бы).
    if "backfilled" not in srv_cols:
        await db.execute("ALTER TABLE servers ADD COLUMN backfilled INTEGER NOT NULL DEFAULT 0")
        await db.execute("UPDATE servers SET backfilled=1")

    # users.vless_uuid — canonical Reality credential. UNIQUE partial index
    # (ignores NULL) защищает от двух юзеров с одинаковым UUID при гонке
    # ensure_user_vless_uuid(). NULL = юзер ещё не использовал VLESS / pre-refactor.
    if "vless_uuid" not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN vless_uuid TEXT")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vless_uuid "
        "ON users(vless_uuid) WHERE vless_uuid IS NOT NULL"
    )

    # One-time backfill: subscriptions with NULL expires_at from legacy code
    # paths. NULL означает «never expires» по текущей семантике WHERE — таких
    # sub'ы не подбирает scheduler reaper, юзер пользуется бесплатно вечно
    # (или коннект ломается из-за NULL в datetime math). Маркируем expired
    # — caller'у нужно понимать что данные не восстановишь, юзер давно ушёл
    # или плохо аккаунтили. trial — отдельная история (там NULL означает
    # ещё-не-активирован, не трогаем).
    try:
        async with db.execute(
            "SELECT 1 FROM schema_state WHERE key='null_expires_backfill_v1'"
        ) as cur:
            already_done = await cur.fetchone() is not None
        if not already_done:
            async with db.execute(
                """SELECT COUNT(*) FROM subscriptions
                   WHERE expires_at IS NULL AND plan != 'vpn_trial'
                     AND status NOT IN ('expired', 'refunded')"""
            ) as cur:
                row = await cur.fetchone()
                null_count = row[0] if row else 0
            if null_count > 0:
                logger.warning(
                    "Migration: marking %d NULL-expires_at subscriptions as expired",
                    null_count,
                )
                await db.execute(
                    "UPDATE subscriptions SET status='expired' "
                    "WHERE expires_at IS NULL AND plan != 'vpn_trial' "
                    "AND status NOT IN ('expired', 'refunded')"
                )
            await db.execute(
                "INSERT INTO schema_state (key) VALUES ('null_expires_backfill_v1')"
            )
    except _sqlite.OperationalError:
        pass  # schema_state может отсутствовать на очень старых БД


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

async def upsert_user(
    user_id: int,
    username: str | None,
    first_name: str,
    traffic_source: str | None = None,
    lang: str | None = None,
):
    """Создаёт user-row или no-op если уже есть.

    `traffic_source` (first-touch attribution): сохраняется ТОЛЬКО при первом
    INSERT. Повторный /start с другим utm-кодом не перезатрёт исходный
    источник — иначе реклама бы крала зачёт у другого канала при повторном
    клике юзера.

    `lang`: Telegram language_code. В отличие от traffic_source — обновляется
    при каждом /start (юзер может сменить язык в Telegram-клиенте, мы должны
    подхватить). NULL значения не перезатирают существующий язык.
    """
    async with _connect() as db:
        if traffic_source is not None:
            await db.execute(
                "INSERT INTO users (id, username, first_name, "
                "traffic_source, traffic_source_at, lang) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  lang = COALESCE(excluded.lang, users.lang)",
                (user_id, username, first_name, traffic_source, lang),
            )
        else:
            await db.execute(
                "INSERT INTO users (id, username, first_name, lang) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  lang = COALESCE(excluded.lang, users.lang)",
                (user_id, username, first_name, lang),
            )
        await db.commit()


async def get_user_lang(user_id: int) -> str | None:
    """Возвращает users.lang (Telegram language_code) или None если не задан.

    Используется в services/i18n_bot.py:t() — там же None → fallback 'ru'.
    """
    async with _connect() as db:
        async with db.execute("SELECT lang FROM users WHERE id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


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
            WHERE status='active' AND plan != 'vpn_trial'
              AND expires_at IS NOT NULL AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now')
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_expired_trials() -> list[dict]:
    """Trial subscriptions past expires_at — for cleanup by scheduler.
    Separate from get_expired_subscriptions because trials skip grace entirely
    (they're free; no incentive to retain via slow tier — just remove peers)."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, user_id, plan, expires_at FROM subscriptions
            WHERE status='active' AND plan='vpn_trial'
              AND expires_at IS NOT NULL
              AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now')
        """) as cur:
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


async def get_partial_refunds() -> list[dict]:
    """Подписки, у которых refund-каскад начался, но не доехал до конца.

    Caused by bot crash mid-refund-cascade. Telegram money refund сам по
    себе необратим (Stars / CryptoBot всё уже вернули юзеру), но если бот
    упал между этим и обновлением БД / revoke configs — состояние
    inconsistent. Этот helper находит такие подписки для catch-up'а
    scheduler'ом.

    Возвращает sub'ы в двух inconsistent-сценариях:

      A) `refunded_at IS NOT NULL` (деньги вернули) AND
         `status NOT IN ('refunded', 'expired')` (но БД не пометила refunded).

      B) `status='refunded'` AND есть configs с status IN ('active','activating')
         (БД помечена refunded, но пиры на агенте остались — юзер до сих пор
         пользуется VPN бесплатно).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT s.id, s.user_id, s.plan, s.status, s.refunded_at, s.payment_id,
                   s.parent_contract_id, s.payment_provider, s.auto_renew,
                   (SELECT COUNT(*) FROM configs c WHERE c.subscription_id=s.id
                    AND c.status IN ('active', 'activating')) AS active_config_count
            FROM subscriptions s
            WHERE
                -- Case A: money refunded but DB not marked refunded
                (s.refunded_at IS NOT NULL AND s.status NOT IN ('refunded', 'expired'))
                OR
                -- Case B: DB marked refunded but configs still active
                (s.status = 'refunded' AND EXISTS (
                    SELECT 1 FROM configs c2
                    WHERE c2.subscription_id=s.id
                      AND c2.status IN ('active', 'activating')
                ))
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_subscription_trial_rolled_back(subscription_id: int):
    """Помечает trial-sub как expired И ставит trial_rolled_back=1.
    Используется в rollback-пути provision_trial: если выдача пиров упала
    после create_subscription, ставим этот маркер чтобы cooldown 30 дней
    НЕ применялся (юзер должен иметь возможность сразу повторить попытку).
    Атомарно в одной транзакции."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions "
            "SET status='expired', pending_plan=NULL, trial_rolled_back=1 "
            "WHERE id=?",
            (subscription_id,),
        )
        await db.commit()


async def mark_subscription_expired_from_grace(subscription_id: int) -> bool:
    """Atomic: переводит ТОЛЬКО `grace` → `expired`.  Если sub уже active
    (race с renew-from-grace) — no-op, возвращает False, caller skip'ает
    destructive op'ы.  Audit 17.05 #2 — scheduler грейс-loop revoke'ал
    конфиги юзера который только что заплатил.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE subscriptions SET status='expired', pending_plan=NULL "
            "WHERE id=? AND status='grace'",
            (subscription_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def mark_subscription_refunded(subscription_id: int):
    """Помечает подписку как возвращённую. Используется при Stars refund /
    manual CryptoBot refund. Отличается от 'expired' тем что MRR-аналитика
    и referral logic не должны считать refunded как реальный доход.

    Также чистит «висящие» поля:
    - grace_until: refunded sub не должна показываться в admin «в grace до X»
    - auto_renew, parent_contract_id: даже если Lava cancel API упадёт,
      локально считаем sub отвязанной — следующий webhook не должен её
      воскресить через extend
    - reminder-флаги: не отправлять напоминания про закрытую sub
    """
    async with _connect() as db:
        await db.execute(
            """UPDATE subscriptions
               SET status='refunded',
                   refunded_at=CURRENT_TIMESTAMP,
                   pending_plan=NULL,
                   grace_until=NULL,
                   auto_renew=0,
                   parent_contract_id=NULL,
                   reminded_3d=0,
                   reminded_1d=0,
                   reminded_grace_3d=0,
                   reminded_renewal_3d=0,
                   reminded_quota_throttled=0
               WHERE id=?""",
            (subscription_id,),
        )
        await db.commit()


async def extend_subscription(subscription_id: int, days: int) -> dict | None:
    """Добавляет `days` дней к expires_at.  Если sub была в grace — возвращает
    status='active', очищает grace_until.  Возвращает обновлённую запись (id,
    user_id, plan, status, expires_at) или None если sub не найдена ИЛИ её статус
    не подходит для extend (expired/refunded).

    Status guard: UPDATE срабатывает только если status IN ('active','grace').
    Без этого admin extend мог «воскресить» refunded/expired sub'у. Caller
    (handle_admin_sub_extend / admin_grant_subscription) должен трактовать
    None как «sub недоступна» и сообщить 404/409.

    Используется админкой для compensation gifts.  Atomic в одной транзакции,
    чтобы scheduler не успел grace-expire-нуть подписку посередине.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, status FROM subscriptions WHERE id=?", (subscription_id,)
        ) as cur:
            pre = await cur.fetchone()
        if not pre:
            return None
        was_grace = pre["status"] == "grace"
        cur_upd = await db.execute(
            """UPDATE subscriptions
               SET expires_at = CASE
                       WHEN expires_at IS NULL OR datetime(expires_at) < datetime('now')
                       THEN datetime('now', ?)
                       ELSE datetime(expires_at, ?)
                   END,
                   status            = CASE WHEN status='grace' THEN 'active' ELSE status END,
                   grace_until       = CASE WHEN status='grace' THEN NULL ELSE grace_until END,
                   reminded_3d       = 0,
                   reminded_1d       = 0,
                   reminded_renewal_3d = 0,
                   reminded_grace_3d   = 0
               WHERE id=? AND status IN ('active', 'grace')""",
            (f"+{days} days", f"+{days} days", subscription_id),
        )
        if cur_upd.rowcount == 0:
            # Sub существует, но status не в ('active','grace') — expired или
            # refunded. Возвращаем None чтобы caller отрапортовал «sub not
            # eligible for extend» вместо тихого ничего-не-делания.
            logger.warning(
                "extend_subscription: sub=%d status=%s not eligible for extend",
                subscription_id, pre["status"],
            )
            await db.commit()
            return None
        await db.commit()
        async with db.execute(
            "SELECT id, user_id, plan, status, expires_at FROM subscriptions WHERE id=?",
            (subscription_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            result = dict(row)
            result["_was_grace"] = was_grace  # Internal flag for callers (unthrottle)
            return result


class AdminGrantConflict(Exception):
    """admin_grant_subscription отказался: у юзера активная sub другого плана.

    Чтобы выдать новый план — админ должен сперва refund'ить существующую
    sub (handle_admin_sub_refund) или сменить план через change-plan
    endpoint. Без этого создавалась бы parallel sub, configs которой
    остаются жить навсегда (get_active_subscription LIMIT 1 их скрывает).
    """
    def __init__(self, existing_sub: dict, requested_plan: str):
        self.existing_sub = existing_sub
        self.requested_plan = requested_plan
        super().__init__(
            f"user has active sub id={existing_sub.get('id')} "
            f"plan={existing_sub.get('plan')} status={existing_sub.get('status')}; "
            f"refusing grant of different plan={requested_plan}"
        )


async def admin_grant_subscription(
    admin_id: int,
    target_user_id: int,
    plan_key: str,
    days: int,
    reason: str | None = None,
    target_username: str | None = None,
) -> dict:
    """Выдаёт бесплатную VPN-подписку юзеру по telegram_id.  Создаёт user-row
    если её нет (юзер мог ещё не запускать бота — например, дал ник в чате).
    Если у юзера уже есть активная sub того же плана — extend на `days`.
    Если активная sub ДРУГОГО плана — raise `AdminGrantConflict` (раньше
    создавалась parallel sub, configs которой leak'ались навсегда — L3 bug).
    Если активной нет — создаёт новую подписку и пустые config-слоты.

    Слоты — пустые: пиры на сервере НЕ провизятся здесь. Юзер активирует
    их сам через Mini App «Мои конфиги» (так же как при обычной покупке).

    Все grant'ы пишутся в `payments` с `is_free_grant=1` + `granted_by_admin_id`
    (для биллинг-вьюхи) и в `audit_log` (для compliance).

    Возвращает {ok, subscription_id, expires_at, action, was_grace} где
    action — 'extended' или 'created', was_grace=True если extend перевёл
    sub из grace в active (caller должен снять throttle на агентах).

    Raises:
        ValueError: bad plan_key / days
        AdminGrantConflict: юзер уже на другом активном плане
    """
    import json as _json
    from services.plans import VPN_PLANS

    plan = VPN_PLANS.get(plan_key)
    if not plan:
        raise ValueError(f"unknown plan_key: {plan_key}")
    if not isinstance(days, int) or not (1 <= days <= 365):
        raise ValueError(f"days must be int in [1, 365], got {days}")

    # 1) Ensure user exists. Если юзер ещё не /start'нул бота — username из
    # admin-формы попадёт как first_name (для отображения «кому выдано»).
    display = target_username or f"grant_{target_user_id}"
    await upsert_user(target_user_id, target_username, display)

    # 2) Проверяем активную/grace подписку. Extend срабатывает только для
    # того же plan_key. Если другой план — REFUSE (не создаём parallel sub).
    active = await get_active_subscription(target_user_id)
    action = "created"
    sub_id: int | None = None
    expires_at_str: str | None = None
    was_grace = False  # True если extend поднял sub из grace — caller сделает unthrottle

    if active and active.get("status") in ("active", "grace"):
        active_plan = active.get("plan") or ""
        # vpn_trial не блокирует grant — триал считается «pre-paid», поверх
        # него админ нормально выдаёт paid план.
        if active_plan == plan_key:
            extended = await extend_subscription(active["id"], days)
            if extended:
                sub_id = extended["id"]
                expires_at_str = extended["expires_at"]
                action = "extended"
                # extend_subscription stash'ит _was_grace во внутреннем поле —
                # извлекаем чтобы caller (handle_admin_grant_subscription)
                # мог запустить unthrottle. Без этого extend из grace оставлял
                # пиров на 256 кбит/с — sub active, но peers throttle'нутые.
                was_grace = bool(extended.get("_was_grace", False))
        elif active_plan and active_plan != "vpn_trial":
            logger.warning(
                "admin_grant_subscription: user=%d has active sub=%d plan=%s, "
                "refusing grant of different plan=%s (admin must refund first)",
                target_user_id, active["id"], active_plan, plan_key,
            )
            raise AdminGrantConflict(active, plan_key)
    if sub_id is None:
        # Создаём новую sub. payment_id уникален (UNIQUE-constraint защитит от
        # случайного дубль-вызова из admin UI при двойном click'е).
        from datetime import datetime as _dt, timedelta as _td
        import secrets as _secrets
        payment_id = f"admin_grant_{admin_id}_{int(_dt.utcnow().timestamp())}_{_secrets.token_hex(4)}"
        expires_at_dt = _dt.utcnow() + _td(days=days)
        sub_id = await create_subscription(
            user_id=target_user_id,
            plan=plan_key,
            payment_id=payment_id,
            stars_paid=0,
            amount_rub=0,
            expires_at=expires_at_dt,
            payment_provider="gift",
        )
        if sub_id is None:
            raise RuntimeError("create_subscription returned None (UNIQUE collision?)")
        expires_at_str = expires_at_dt.isoformat()

        # Пустые слоты по тарифу. provision_peer НЕ вызываем — юзер сам
        # активирует через Mini App.
        for _ in range(plan.get("awg_slots", 0)):
            await create_config_record(sub_id, target_user_id, protocol="awg")
        for _ in range(plan.get("vless_slots", 0)):
            await create_config_record(sub_id, target_user_id, protocol="vless")
        for _ in range(plan.get("wg_slots", 0)):
            await create_config_record(sub_id, target_user_id, protocol="wg")

    # 3) payments-row: amount=0, is_free_grant=1, granted_by_admin_id.
    # tx_id уникален; partial-index idx_payments_tx_id защитит от случайного
    # дубля при retry.
    import secrets as _secrets2
    from datetime import datetime as _dt2
    tx_id = f"grant_{admin_id}_{sub_id}_{int(_dt2.utcnow().timestamp())}_{_secrets2.token_hex(3)}"
    async with _connect() as db:
        try:
            await db.execute(
                """INSERT INTO payments
                   (user_id, subscription_id, method, amount_usd, stars, tx_id,
                    status, granted_by_admin_id, is_free_grant)
                   VALUES (?, ?, 'admin_grant', 0, 0, ?, 'completed', ?, 1)""",
                (target_user_id, sub_id, tx_id, admin_id),
            )
            await db.commit()
        except Exception as e:
            logger.warning("payments-row write failed for grant: %s", e, exc_info=True)

    # 4) audit_log — compliance. details — JSON: что выдали, кому, почему,
    # extend vs create. Если запись провалится — операция уже сделана, не откатываем.
    await audit_log_record(
        admin_id=admin_id,
        action="grant_subscription",
        target=str(target_user_id),
        details=_json.dumps({
            "plan": plan_key,
            "days": days,
            "reason": reason,
            "subscription_id": sub_id,
            "action": action,
            "target_username": target_username,
        }, ensure_ascii=False),
    )

    return {
        "ok": True,
        "subscription_id": sub_id,
        "expires_at": expires_at_str,
        "action": action,
        "was_grace": was_grace,
    }


async def list_admin_grants(limit: int = 50) -> list[dict]:
    """Последние grant-записи из payments (is_free_grant=1), JOIN'ятся с
    subscriptions и users для отображения в admin /grant странице.
    Сортировка DESC по created_at; limit clamp'ится в caller'е.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.id, p.user_id, p.subscription_id, p.granted_by_admin_id,
                      p.tx_id, p.created_at,
                      s.plan AS sub_plan, s.status AS sub_status,
                      s.expires_at AS sub_expires_at,
                      u.username AS user_username
               FROM payments p
               LEFT JOIN subscriptions s ON s.id = p.subscription_id
               LEFT JOIN users u ON u.id = p.user_id
               WHERE p.is_free_grant = 1
               ORDER BY p.created_at DESC
               LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


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


async def mark_subscription_grace(subscription_id: int, grace_until: str) -> bool:
    """Переводит подписку в grace-period (14 дней низкой скорости).

    Возвращает True если переход состоялся; False если строка уже не active
    ИЛИ expires_at уже отодвинут в будущее (admin extend / recurring renew
    мог отработать пока scheduler throttle'ил пиров). Без проверки expires_at
    sub оказалась бы в противоречивом состоянии: status='grace',
    grace_until=now+14d, но expires_at в будущем.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE subscriptions SET status='grace', grace_until=? "
            "WHERE id=? AND status='active' "
            "AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now')",
            (grace_until, subscription_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


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
            "UPDATE configs SET status='activating', activated_at=datetime('now') WHERE id=? AND status='empty'",
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


async def apply_pending_plan_change(sub_id: int, new_plan: str) -> str | None:
    """Переключает plan подписки на pending_plan и сбрасывает pending_plan.

    Вызывается scheduler'ом когда подписка истекает а у юзера был
    запланирован downgrade (vpn_max → vpn_base). После этого следующая
    покупка/продление будет за новый тариф.

    Возвращает `new_plan` если UPDATE применился (1 row changed), иначе None
    (race: юзер сделал upgrade параллельно → pending_plan уже NULL).
    Caller'у нужно это значение чтобы решить надо ли revoke'ить лишние
    configs (если new < old slots count).
    """
    async with _connect() as db:
        # WHERE pending_plan=? — guard от race: если юзер успел сделать
        # upgrade параллельно, pending_plan уже NULL → UPDATE no-op, не
        # перезаписываем актуальный plan. apply_pending_plan_change
        # вызывается из scheduler на момент истечения подписки.
        cur = await db.execute(
            "UPDATE subscriptions SET plan=?, pending_plan=NULL "
            "WHERE id=? AND pending_plan=?",
            (new_plan, sub_id, new_plan),
        )
        await db.commit()
        return new_plan if cur.rowcount > 0 else None


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


async def extend_subscription_expires_at(
    sub_id: int, days: int, *, reset_status: bool = True
) -> bool | None:
    """Продлевает подписку на `days` дней от max(expires_at, now). Атомарно.

    РАНЬШЕ: caller сам вычислял `new_expires = max(cur, now) + days` и
    передавал isoformat. При двух concurrent renewal-webhook'ах их max'ы
    основывались на одном и том же stale `expires_at` → один из них
    overwrite'ил другой меньшим значением (lost update).

    ТЕПЕРЬ: вся арифметика max()/+days в SQL. Два параллельных вызова дадут
    одинаковый итог только если они оба сработали с одним базовым expires_at;
    последовательный — каждый раз даст на `days` больше предыдущего.

    Сбрасывает status='active' если он был 'grace' — recurring деньги пришли
    вовремя или с задержкой, значит юзер хочет оставаться на VPN. Сбрасываются
    все reminded_* флаги.

    Возвращает True если sub была в 'grace' (caller должен снять throttle),
    False если была active, None если sub уже expired или не найдена —
    UPDATE не выполнен (zombie-guard: не воскрешаем expired sub).
    """
    days_int = int(days)  # defensive cast: f-string injection — safe только если int
    async with _connect() as db:
        async with db.execute(
            "SELECT status, auto_renew, auto_renew_disabled_at FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] not in ("active", "grace"):
            return None  # sub expired или not found — не воскрешаем
        # FFF3: юзер отменил автопродление, провайдер не получил cancel-API → но
        # recurring charge всё-таки пришёл. Не extend'им против воли юзера.
        # Caller должен refund'нуть платёж + retry cancel.
        if row[2] is not None and not row[1]:
            return None
        was_grace = row[0] == "grace"
        # FFF3 (financial): если юзер отменил автопродление (auto_renew_disabled_at IS NOT NULL)
        # и Lava cancel API провалился ранее → recurring charge всё равно прилетает.
        # Без guard'а ниже мы extend'или бы sub против воли юзера — financial loss.
        # Расширяем ТОЛЬКО если disabled_at is NULL (никогда не отменялся) ИЛИ
        # auto_renew=1 (юзер заново включил автопродление, disabled_at — историческое).
        # Также сбрасываем auto_renew_disabled_at (FFF6) — после extend'а юзер
        # фактически «не отменён».
        if reset_status:
            cur_ = await db.execute(
                f"""UPDATE subscriptions
                    SET expires_at = datetime(
                        CASE
                            WHEN datetime(REPLACE(expires_at, 'T', ' ')) > datetime('now')
                            THEN REPLACE(expires_at, 'T', ' ')
                            ELSE datetime('now')
                        END,
                        '+{days_int} days'
                    ),
                    status='active',
                    grace_until=NULL,
                    reminded_3d=0, reminded_1d=0,
                    reminded_renewal_3d=0, reminded_grace_3d=0,
                    auto_renew_disabled_at=NULL,
                    last_charge_failed_at=NULL
                    WHERE id=? AND status IN ('active', 'grace')
                      AND (auto_renew_disabled_at IS NULL OR auto_renew = 1)""",
                (sub_id,),
            )
        else:
            cur_ = await db.execute(
                f"""UPDATE subscriptions
                    SET expires_at = datetime(
                        CASE
                            WHEN datetime(REPLACE(expires_at, 'T', ' ')) > datetime('now')
                            THEN REPLACE(expires_at, 'T', ' ')
                            ELSE datetime('now')
                        END,
                        '+{days_int} days'
                    ),
                    reminded_renewal_3d=0,
                    auto_renew_disabled_at=NULL,
                    last_charge_failed_at=NULL
                    WHERE id=?
                      AND (auto_renew_disabled_at IS NULL OR auto_renew = 1)""",
                (sub_id,),
            )
        if (cur_.rowcount or 0) == 0:
            # Sub изменился между SELECT и UPDATE (race с refund/expire) —
            # не воскрешаем, возвращаем None как при изначально expired sub.
            return None
        await db.commit()
    return was_grace


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


async def mark_renewal_reminded(sub_id: int) -> bool:
    """Ставит reminded_renewal_3d=1 только если sub всё ещё active/grace с
    auto_renew=1 и expires_at в окне напоминания (3 дня).

    Race: между `get_recurring_renewal_due_soon` и mark'ом юзер мог отменить
    auto-renew или Lava recurring мог продлить sub. extend_subscription и
    extend_subscription_expires_at сбрасывают reminded_renewal_3d=0 — если
    мы безусловно поставим обратно =1, reminder для следующего auto-charge
    не сработает.
    """
    async with _connect() as db:
        cur = await db.execute(
            """UPDATE subscriptions SET reminded_renewal_3d=1
               WHERE id=? AND auto_renew=1 AND status IN ('active','grace')
                 AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now', '+3 days')""",
            (sub_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def disable_auto_renew(sub_id: int) -> bool:
    """Юзер отменил автопродление (из нашего UI или Lava-кабинета).
    Сама подписка дослужит до expires_at.

    Atomic CAS: UPDATE срабатывает только если auto_renew был установлен.
    Возвращает True если только что выключили, False если уже было выключено
    (или sub_id не существует). Caller'ы которым return не нужен — игнорируют
    его (legacy fire-and-forget продолжает работать).

    Применение: предотвращает двойной вызов Lava cancel API при гонке
    двух устройств — второе устройство получает False и пропускает вызов
    к внешнему API, который иначе вернёт 404 и заспамит admin-алерт.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE subscriptions SET auto_renew=0, "
            "auto_renew_disabled_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND auto_renew=1",
            (sub_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def set_quota_throttled_flag(sub_id: int, flagged: bool) -> None:
    """Помечает что юзер уже был уведомлён о throttling'е (квота исчерпана).
    Используется scheduler'ом для дедупликации notify + триггера обратного
    «скорость восстановлена» сообщения при un-throttle."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET reminded_quota_throttled=? WHERE id=?",
            (1 if flagged else 0, sub_id),
        )
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
                 AND (activated_at IS NULL OR activated_at < datetime('now', '-5 minutes'))"""
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


async def get_configs_for_subscription_by_protocol(
    subscription_id: int, protocol: str,
) -> list[dict]:
    """Все configs (любой status) для sub+protocol, упорядоченные так чтобы
    empty шли первыми, потом active по возрастанию id.

    Используется в `revoke_excess_configs_on_downgrade` (revoke.py):
    при downgrade тарифа нужно убрать excess-слоты — empty освобождаем
    в первую очередь (без обращения к агенту), оставшиеся active — самые
    старые (юзер уже привык к ним меньше всего, новые конфиги обычно
    «нагружают» юзера сильнее).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, peer_name, protocol, server_id, assigned_ip, "
            "vless_uuid, config_data, status FROM configs "
            "WHERE subscription_id=? AND protocol=? "
            "ORDER BY CASE WHEN status='empty' THEN 0 ELSE 1 END, id ASC",
            (subscription_id, protocol),
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
    user_id при апгрейде (sec audit H6).

    Поля `auto_renew`, `payment_provider`, `parent_contract_id` и
    `refunded_at` нужны refund-каскаду в `handle_admin_sub_refund`
    (Lava-cancel ветка) и `handle_admin_user_ban` (L9). Без них
    `sub.get(...)` тихо возвращал None и Lava-recurring оставалась
    активной после refund'а.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, plan, status, expires_at, grace_until, pending_plan, "
            "auto_renew, payment_provider, parent_contract_id, refunded_at "
            "FROM subscriptions WHERE id=?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_user_subscriptions_by_plan(
    user_id: int, plan: str,
    status: str | tuple[str, ...] = "active",
) -> list[dict]:
    """Список подписок юзера определённого плана и статуса (или нескольких).

    Используется чтобы найти trial когда юзер платит — `status=("active","grace")`
    закрывает и активные, и в-grace trial'ы (раньше grace trials оставались
    висеть, плодя дубль-подписок и orphan'ные VLESS peers — audit 17.05 #C1).
    """
    statuses = (status,) if isinstance(status, str) else tuple(status)
    if not statuses:
        return []
    placeholders = ",".join("?" * len(statuses))
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT id, plan, status, expires_at, created_at FROM subscriptions
                WHERE user_id=? AND plan=? AND status IN ({placeholders})
                ORDER BY created_at DESC""",
            (user_id, plan, *statuses),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_active_subscription(user_id: int) -> dict | None:
    """Возвращает активную подписку пользователя или None.

    Включает status IN ('active', 'grace') — grace это «истекла, но 14 дней
    на 256 кбит/с»; для UX и provisioning это всё ещё валидная подписка с
    живыми пирами. UI отличает по `grace_until IS NOT NULL`.

    Defensive logging: LIMIT 1 + ORDER BY created_at DESC раньше прятал
    parallel subs (например если `admin_grant_subscription` некогда создавал
    sub того же юзера параллельно с уже-активной другого тарифа — L3 bug).
    Если такие подписки существуют — логируем warning со списком id чтобы
    админ заметил и refund'ил/закрыл лишние вручную. Backward-compat: всё
    равно возвращаем newest (как раньше).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, plan, stars_paid, status, expires_at, pending_plan, created_at, grace_until,
                   auto_renew, payment_provider, parent_contract_id
            FROM subscriptions
            WHERE user_id=? AND status IN ('active', 'grace')
            ORDER BY created_at DESC
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
            if not rows:
                return None
            if len(rows) > 1:
                # Игнорим trial — vpn_trial легитимно может сосуществовать
                # с paid sub в редких race-сценариях, но не должен.
                non_trial = [r for r in rows if (r["plan"] or "") != "vpn_trial"]
                if len(non_trial) > 1:
                    logger.warning(
                        "get_active_subscription: user=%d has %d active non-trial subs: %s "
                        "— returning newest (admin should reconcile)",
                        user_id, len(non_trial),
                        [(r["id"], r["plan"], r["status"]) for r in non_trial],
                    )
            return dict(rows[0])


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
                                    wg_delta: int = 0,
                                    duration_days: int = 30,
                                    new_payment_id: str | None = None) -> bool:
    """
    Немедленно меняет план подписки (апгрейд).
    Добавляет новые пустые слоты если awg_delta/vless_delta/wg_delta > 0.
    Снимает pending_plan если он был.

    Если подписка в grace (256 кбит/с после истечения) — возвращает её в active
    с продлением expires_at на duration_days дней. Иначе апгрейд из grace
    оставил бы юзера на 256 кбит/с с новым планом — UI «Plan: Max», фактически
    throttle.

    Status guard: UPDATE срабатывает только если sub.status IN ('active','grace').
    Без этого CryptoBot webhook race (или admin refund mid-flight) могли «воскресить»
    refunded/expired sub'у, выставив status='active'. Возвращает True если row был
    обновлён, False если sub не нашлась или была в неподходящем статусе — caller
    должен залогировать и НЕ вставлять новые слоты.

    Sec/edge audit C4 (15.05): caller'у (handlers/vpn.py:_apply_plan_upgrade)
    после этого вызова нужно отдельно вызвать unthrottle на vpnctl_client
    чтобы вернуть AWG-пиры с tc, и переместить VLESS из vless-grace inbound
    обратно в vless-base/max — это делается там, потому что требует agent_url.
    """
    async with _connect() as db:
        # Если был в grace — продлеваем active на полные duration_days от now
        cur = await db.execute(
            """UPDATE subscriptions
               SET plan=?, pending_plan=NULL,
                   status='active',
                   grace_until=NULL,
                   expires_at = CASE
                       WHEN status='grace' THEN datetime('now', ?)
                       ELSE expires_at
                   END
               WHERE id=? AND status IN ('active', 'grace')""",
            (new_plan, f"+{duration_days} days", sub_id),
        )
        if cur.rowcount == 0:
            # Sub либо не существует, либо уже expired/refunded — caller должен
            # пропустить вставку слотов + alert admin (CryptoBot money taken,
            # subscription state diverged).
            logger.warning(
                "change_subscription_plan: sub=%d not eligible (rowcount=0) — "
                "status guard rejected, slot insertion skipped",
                sub_id,
            )
            await db.commit()
            return False
        if new_payment_id:
            # Upgrade doplata — update payment_id so refund targets the latest charge.
            # Без этого refund на upgrade-sub'е попадает в первоначальный charge,
            # а deltaт (Stars/RUB за апгрейд) повисает без возможности вернуть из admin UI.
            await db.execute(
                "UPDATE subscriptions SET payment_id=? WHERE id=?",
                (new_payment_id, sub_id),
            )
        # Idempotent slot insertion: вместо «add delta» используем «reach target
        # count from VPN_PLANS». При retry/race повторный вызов не создаёт
        # дубликат слотов — то же target_count → to_create=0. delta-логика
        # ниже остаётся для warning о downgrade (когда target < existing,
        # revoke_excess_configs_on_downgrade должен быть вызван caller'ом — см.
        # PS3 round 10).
        from services.plans import VPN_PLANS
        new_plan_def = VPN_PLANS.get(new_plan, {})
        target_counts = {
            "awg":   new_plan_def.get("awg_slots", 0),
            "vless": new_plan_def.get("vless_slots", 0),
            "wg":    new_plan_def.get("wg_slots", 0),
        }
        for proto, delta in (("awg", awg_delta), ("vless", vless_delta), ("wg", wg_delta)):
            if delta < 0:
                logger.warning(
                    "change_subscription_plan: negative %s_delta=%d for sub=%d plan=%s — "
                    "caller should call revoke_excess_configs_on_downgrade to revoke surplus",
                    proto, delta, sub_id, new_plan,
                )
            target = target_counts[proto]
            if target == 0:
                continue
            async with db.execute(
                "SELECT COUNT(*) FROM configs WHERE subscription_id=? AND protocol=?",
                (sub_id, proto),
            ) as cur2:
                row = await cur2.fetchone()
                existing = row[0] if row else 0
            to_create = max(0, target - existing)
            if to_create == 0:
                continue
            for _ in range(to_create):
                await db.execute(
                    "INSERT INTO configs (subscription_id, user_id, protocol, status) "
                    "VALUES (?, ?, ?, 'empty')",
                    (sub_id, user_id, proto),
                )
        await db.commit()
        return True


async def schedule_plan_change(
    sub_id: int,
    pending_plan: str | None,
    expected_previous: str | None | _Sentinel = _SENTINEL,
) -> bool:
    """
    Ставит (или снимает) запланированный даунгрейд на следующий месяц.
    pending_plan=None — отменить запланированное изменение.

    `expected_previous` (optional CAS):
        — `_SENTINEL` (default): legacy fire-and-forget, всегда True.
        — `None` / `""`: CAS «текущее значение pending_plan пустое».
        — строка `"vpn_base"`: CAS «текущее значение pending_plan совпадает».

    Возвращает False если параллельная сессия успела изменить значение —
    caller должен сообщить 409 или перечитать состояние.
    """
    async with _connect() as db:
        if expected_previous is _SENTINEL:
            # Legacy path — без CAS, не проверяем предыдущее значение.
            cur = await db.execute(
                "UPDATE subscriptions SET pending_plan=? WHERE id=?",
                (pending_plan, sub_id),
            )
        else:
            # CAS: матчим COALESCE(pending_plan,'') == expected_previous_norm.
            # Нормализуем None→'' чтобы строковое сравнение в SQL работало.
            expected_norm = expected_previous or ""
            cur = await db.execute(
                "UPDATE subscriptions SET pending_plan=? "
                "WHERE id=? AND COALESCE(pending_plan,'') = ?",
                (pending_plan, sub_id, expected_norm),
            )
        await db.commit()
        return (cur.rowcount or 0) > 0


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


async def close_ticket(ticket_id: int) -> bool:
    """Закрывает тикет — простой UPDATE статуса. Идемпотентно (close-on-closed = ok).

    Возвращает True если запись была изменена (тикет существует), False если
    ничего не сматчилось — caller'у нужно отбить 404 чтобы не маскировать
    опечатки в ticket_id и не мусорить в audit log.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE support_tickets SET status='closed' WHERE id=?",
            (ticket_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


# ── expiry reminders ───────────────────────────────────────────────────────────

async def get_subscriptions_expiring_soon(days: int) -> list[dict]:
    """Возвращает активные подписки, истекающие через `days` дней (±12 ч).

    `datetime(expires_at)` нормализует формат: aiosqlite сохраняет datetime
    как ISO с 'T'-разделителем (`2026-05-19T00:42:00.x`), а `datetime('now', …)`
    возвращает с пробелом (`2026-05-19 00:42:00`).  Без normalization строковое
    сравнение ломалось на дне-граничном времени (T=0x54 > space=0x20).

    F3 fix: для 1-дневного reminder верхняя граница окна расширена до 30ч
    (вместо 24ч). При hourly tick это гарантирует, что юзер увидит уведомление
    минимум за 24ч до истечения. Без этого worst case был ~14 мин — юзер
    пересекал порог сразу после tick и ждал следующего часа.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        col = "reminded_3d" if days >= 2 else "reminded_1d"
        # Верхняя граница окна (в часах). Для 1d — 30ч вместо 24ч.
        upper_hours = days * 24 + (6 if days == 1 else 0)
        # Нижняя граница: предыдущий «слой» — для 1d это 0ч (всё что ближе
        # 30ч но ещё активно), для 3d — 2 дня (как раньше).
        lower_modifier = "0 hours" if days == 1 else f"+{days-1} days"
        async with db.execute(
            f"""SELECT * FROM subscriptions
                WHERE status='active'
                AND {col}=0
                AND datetime(expires_at) > datetime('now', '{lower_modifier}')
                AND datetime(expires_at) < datetime('now', '+{int(upper_hours)} hours')""",
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_reminded(sub_id: int, days: int) -> bool:
    """Ставит reminded_3d/reminded_1d=1 только если sub всё ещё в окне напоминания.

    Race: между `get_subscriptions_expiring_soon` и отправкой reminder'а админ
    мог extend'нуть sub (extend_subscription сбрасывает reminded_*=0). Без
    проверки expires_at этот UPDATE поставил бы reminded_3d=1 на свежепродлённой
    sub → legitimate reminder для NEXT cycle подавился бы.

    Returns: True если строка обновлена, False если sub больше не в окне
    (caller может проигнорировать — reminder следующего цикла теперь сработает).
    """
    col = "reminded_3d" if days >= 2 else "reminded_1d"
    # F3: верхняя граница должна совпадать с get_subscriptions_expiring_soon —
    # для 1d это 30ч, для 3d — 3 дня.
    upper_hours = days * 24 + (6 if days == 1 else 0)
    modifier = f"+{int(upper_hours)} hours"  # int() — защита от SQL-injection
    async with _connect() as db:
        cur = await db.execute(
            f"""UPDATE subscriptions SET {col}=1
                WHERE id=? AND status='active'
                  AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now', '{modifier}')""",
            (sub_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


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


async def mark_grace_reminded(sub_id: int) -> bool:
    """Ставит reminded_grace_3d=1 только если sub всё ещё в grace (admin не
    extend'нул её обратно в active между select'ом и mark'ом). Иначе
    extend сбросил reminded_grace_3d=0, а мы бы заглушили reminder следующего
    grace-цикла.
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE subscriptions SET reminded_grace_3d=1 WHERE id=? AND status='grace'",
            (sub_id,),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def renew_subscription_from_grace(
    sub_id: int, days: int = 30, *,
    auto_renew: bool = False, payment_provider: str | None = None,
) -> dict | None:
    """Продление из grace-состояния: status→active, grace_until=NULL,
    expires_at = now + days.  Сбрасывает reminded-флаги (новый период,
    новые напоминания).

    Atomic: UPDATE с гардом `WHERE id=? AND status='grace'`.  Возвращает
    None если sub не найдена ИЛИ уже не grace (race с
    `_process_grace_expired_subscriptions`-scheduler-таском который мог
    перевести её в expired между check и renew).

    auto_renew / payment_provider — проставляются при Stars recurring:
    без них `get_recurring_sub_for_renewal` не найдёт sub на следующем
    цикле и fallback создаст вторую подписку.

    Caller (`services/grace.py`) после этого должен вызвать
    unthrottle/move через vpnctl_client — мы только меняем БД, агент
    отдельно (см. _apply_plan_upgrade для образца).
    """
    extra_set: list[str] = []
    extra_params: list = []
    if auto_renew:
        extra_set.append("auto_renew=1")
        # FFF6: re-enable auto_renew → historical disabled_at стирается, иначе
        # extend_subscription_expires_at guard на следующем renewal заблокирует.
        extra_set.append("auto_renew_disabled_at=NULL")
    if payment_provider is not None:
        extra_set.append("payment_provider=?")
        extra_params.append(payment_provider)
    extra_clause = (", " + ", ".join(extra_set)) if extra_set else ""

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""UPDATE subscriptions
                SET status='active',
                    grace_until=NULL,
                    expires_at=datetime('now', '+{days} days'),
                    pending_plan=NULL,
                    reminded_3d=0,
                    reminded_1d=0,
                    reminded_grace_3d=0,
                    reminded_renewal_3d=0
                    {extra_clause}
                WHERE id=? AND status='grace'""",
            extra_params + [sub_id],
        )
        await db.commit()
        if cur.rowcount == 0:
            # Либо sub не существует, либо уже не grace (scheduler выпил
            # её первым). Caller получит None и fallback'нется на create.
            return None
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
            "SELECT COUNT(*) FROM users WHERE referred_by=? AND COALESCE(is_banned, 0) = 0",
            (referrer_id,),
        ) as cur:
            invited = (await cur.fetchone())[0]
        async with db.execute(
            """SELECT COUNT(DISTINCT u.id) FROM users u
               JOIN subscriptions s ON s.user_id=u.id
               WHERE u.referred_by=? AND s.status IN ('active','expired','grace')
                 AND s.plan != 'vpn_trial'
                 AND s.refunded_at IS NULL""",
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
    """Сервер с наименьшей загрузкой для данного протокола.

    capacity > 0 фильтр: иначе деление на 0 → NULL, NULL сортируется первым,
    и misconfigured-сервер с capacity=0 «выигрывает» каждый load-balance.

    Protocol mapping явный: awg/wg/vless различимы. Раньше было
    `proto_field = "awg" if protocol == "awg" else "vless"` — wg сваливался
    в vless, что заставляло wg-конфиги провижиниться на VLESS-сервера.
    """
    if protocol == "awg":
        proto_field = "awg"
    elif protocol == "wg":
        proto_field = "wg"
    elif protocol in ("vless", "vless-reality"):
        proto_field = "vless"
    else:
        logger.warning("get_best_server: unknown protocol=%r", protocol)
        return None
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM servers
            WHERE protocol=? AND is_active=1 AND agent_url IS NOT NULL
              AND capacity > 0
            ORDER BY (CAST(active_peers AS REAL) / capacity) ASC
            LIMIT 1
        """, (proto_field,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_active_servers(protocol: str) -> list[dict]:
    """Все active сервера для протокола с настроенным agent_url.
    Используется для multi-location VLESS provisioning: один UUID
    реплицируется на каждый сервер, юзер видит N локаций в sub-URL.

    capacity > 0 — тот же guard, что в get_best_server: misconfigured-сервера
    с capacity=0 даю́т NULL в сортировке и встают первыми. Лучше скрыть
    полностью, чем гнать туда трафик.

    Protocol mapping явный — см. комментарий в get_best_server.
    """
    if protocol == "awg":
        proto_field = "awg"
    elif protocol == "wg":
        proto_field = "wg"
    elif protocol in ("vless", "vless-reality"):
        proto_field = "vless"
    else:
        logger.warning("get_all_active_servers: unknown protocol=%r", protocol)
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM servers
            WHERE protocol=? AND is_active=1 AND agent_url IS NOT NULL
              AND capacity > 0
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


async def reconcile_active_peers_counters() -> list[dict]:
    """Recompute servers.active_peers from actual configs counts. Returns list of fixes applied.

    Drift accumulates over time when increment/decrement calls are missed (bugs,
    crashes, edge cases). This recompute is the authoritative correction.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                s.id AS server_id,
                s.name,
                s.active_peers AS stored,
                COALESCE(c.actual_count, 0) AS actual
            FROM servers s
            LEFT JOIN (
                SELECT server_id, COUNT(*) AS actual_count
                FROM configs
                WHERE status IN ('active', 'activating') AND server_id IS NOT NULL
                GROUP BY server_id
            ) c ON c.server_id = s.id
            WHERE s.active_peers != COALESCE(c.actual_count, 0)
        """) as cur:
            drifted = [dict(r) for r in await cur.fetchall()]

        fixes: list[dict] = []
        for row in drifted:
            await db.execute(
                "UPDATE servers SET active_peers=? WHERE id=?",
                (row["actual"], row["server_id"]),
            )
            fixes.append({
                "server_id": row["server_id"],
                "name": row["name"],
                "before": row["stored"],
                "after": row["actual"],
                "delta": row["actual"] - row["stored"],
            })
        await db.commit()
        return fixes


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


async def get_config_id_by_vless_uuid_and_server(vless_uuid: str, server_id: int) -> int | None:
    """Like get_config_id_by_vless_uuid but for a specific server.

    Multi-location: один UUID реплицируется на N серверов → N configs rows.
    Прежний `get_config_id_by_vless_uuid` возвращал первую попавшуюся → stats
    одного сервера затирали другого. Используем server-specific lookup.
    """
    async with _connect() as db:
        async with db.execute(
            "SELECT id FROM configs WHERE vless_uuid=? AND server_id=? LIMIT 1",
            (vless_uuid, server_id),
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
               WHERE c.server_id=? AND c.protocol='vless' AND c.status IN ('active', 'activating')
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
                      c.rx_bytes, c.tx_bytes, s.plan AS plan_key, s.id AS subscription_id,
                      s.reminded_quota_throttled
               FROM configs c
               JOIN subscriptions s ON c.subscription_id = s.id
               WHERE c.protocol='vless' AND c.status='active'
                 AND c.vless_uuid IS NOT NULL AND c.vless_uuid != ''
                 AND s.status='active'"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_config_data(config_id: int, config_data: str) -> bool:
    """Обновляет config_data только если слот ещё active.

    Защита от race: refund/revoke reset'нул слот в status='empty' с
    config_data=NULL ПОКА вызывающий код (например _apply_quota_throttle или
    grace move) держит локальную копию старого config_data и вычисляет новый.
    Без AND status='active' этот UPDATE воскресил бы пустой слот с устаревшим
    config_data, оставив зомби-запись в БД.

    Returns: True если строка обновлена, False если слот уже не active
    (caller может проигнорировать — обычно это значит refund/revoke прошёл).
    """
    async with _connect() as db:
        cur = await db.execute(
            "UPDATE configs SET config_data=? WHERE id=? AND status='active'",
            (config_data, config_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def get_vless_slots_missing_from_server(server_id: int) -> list[dict]:
    """Multi-location backfill: возвращает активные VLESS-слоты которые ещё
    не реплицированы на `server_id`. Для каждого выдаёт одну строку с
    (subscription_id, user_id, vless_uuid, plan, sub_status) — этого хватает
    провижить пир с тем же UUID на новом сервере.

    Trial-подписки (`plan='vpn_trial'`) исключены: длятся 3 дня, выдают 1 VLESS,
    bаckfill на них = трата capacity (через ~2 дня сами истечут).
    Платные тарифы — backfill'им.

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
                 AND s.plan != 'vpn_trial'
                 AND c.vless_uuid NOT IN (
                     SELECT vless_uuid FROM configs
                     WHERE server_id=? AND protocol='vless' AND status='active'
                       AND vless_uuid IS NOT NULL AND vless_uuid != ''
                 )""",
            (server_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
#  Dynamic VLESS subscription (2026-05-21 refactor)
#
#  Раньше: `/sub/{token}` отдавал base64 список URL'ов, взятых из
#  `configs.config_data`. Каждая строка == peer на конкретном сервере, поэтому
#  при добавлении нового VLESS-сервера приходилось вручную INSERT-ить строки
#  для каждого юзера + провижионить UUID на новом агенте.
#
#  Теперь: subscription для VLESS строится на лету.
#    URLs = users.vless_uuid × {active VLESS servers} × {tier из plan/status}
#  Добавление нового сервера = INSERT в `servers` + один скрипт-проход по
#  users.vless_uuid с POST к новому агенту. DB-строки в `configs` для VLESS не
#  трогаем (они нужны только для legacy fallback пока backfill не прошёл).
# ──────────────────────────────────────────────────────────────────────────────


async def active_vless_servers() -> list[dict]:
    """VLESS-серверы, готовые отдавать URL в подписке.

    Фильтрация:
      • protocol='vless' + is_active=1 — базовые маркеры live-сервера.
      • xray_pubkey IS NOT NULL — backfill параметров Reality прошёл; пока
        NULL, сервер невидим для dynamic-режима, чтобы случайно не отдать
        URL без нужных pbk/sid.
      • backfilled=1 — multi-location UUID-backfill прошёл (см. POST
        /api/admin/servers/{id}/backfill-vless). До этого агент про
        существующие UUID юзеров не знает, и Happ покажет «нет серверов»
        для всех пиров на новой ноде. Существующие сервера получают
        backfilled=1 автоматически при миграции.
    Сортируем по id для детерминированного порядка в подписке.

    NB: ранее тут стоял фильтр `xray_sni IS NOT NULL`, который ломал legacy
    серверы где SNI хранился ТОЛЬКО в `/opt/vpnctl/.env` агента (XRAY_SNI),
    а в БД был NULL. Существующие юзеры внезапно получали пустой
    subscription URL в Happ. Теперь фильтр снят — `_resolve_vless_urls`
    применяет fallback на дефолтный SNI (`www.microsoft.com` или env).

    NB: ordering by `id` is stable in steady state but jumps if a server is
    DELETEd and re-INSERTed (new id == max+1, jumps to end of subscription
    list — Happ shows it as a "new" server). A proper fix would be a
    `display_order` column. Until then: in prod prefer `update_server(...)`
    over delete+create to keep server order stable for users.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, host, flag, city, name,
                   xray_pubkey, xray_short_id, xray_sni, xray_fingerprint,
                   xray_port_base, xray_port_max,
                   xray_port_base_slow, xray_port_max_slow, xray_port_grace
            FROM servers
            WHERE protocol='vless' AND is_active=1
              AND xray_pubkey IS NOT NULL AND xray_pubkey != ''
              AND backfilled=1
            ORDER BY id
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def ensure_user_vless_uuid(user_id: int) -> str:
    """Atomically allocate a canonical Reality UUID for the user.

    Если уже есть — возвращает существующий. Иначе — UPDATE ... WHERE
    vless_uuid IS NULL фиксирует значение в одной транзакции (защита от
    race между двумя одновременными вызовами).
    """
    import uuid as _uuid
    async with _connect() as db:
        async with db.execute(
            "SELECT vless_uuid FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            return row[0]
        new_uuid = str(_uuid.uuid4())
        # CAS: ставим только если ещё NULL. rowcount==0 → между SELECT и UPDATE
        # уже другой воркер записал свой UUID; в этом случае дочитываем готовое
        # значение.
        cur = await db.execute(
            "UPDATE users SET vless_uuid=? WHERE id=? AND vless_uuid IS NULL",
            (new_uuid, user_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            async with db.execute(
                "SELECT vless_uuid FROM users WHERE id=?", (user_id,)
            ) as cur2:
                row2 = await cur2.fetchone()
                return row2[0] if row2 else new_uuid
        return new_uuid


async def get_relevant_vless_subscription(user_id: int) -> dict | None:
    """Самая релевантная подписка юзера для VLESS-доступа.

    Возвращает {id, plan, status, expires_at} или None если у юзера нет
    ни одной active/grace подписки.

    Приоритет: active > grace (внутри одного статуса — самая поздняя по
    expires_at, обычно у юзера так и так одна active за раз).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT id, plan, status, expires_at
            FROM subscriptions
            WHERE user_id=? AND status IN ('active', 'grace')
            ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,
                     expires_at DESC
            LIMIT 1
        """, (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# Маппинг tier-имени сервиса на префикс колонки в servers.xray_port_*.
# vless-base-slow / vless-max-slow / vless-grace — throttle/grace инбаунды;
# legacy 'vless' попадает на base-порт для совместимости со старыми планами.
_VLESS_TIER_PORT_COL = {
    "vless-base":      "xray_port_base",
    "vless-max":       "xray_port_max",
    "vless-base-slow": "xray_port_base_slow",
    "vless-max-slow":  "xray_port_max_slow",
    "vless-grace":     "xray_port_grace",
    "vless":           "xray_port_base",
}


def vless_port_column(tier: str) -> str | None:
    """Колонка в `servers`, в которой лежит порт нужного VLESS tier'а."""
    return _VLESS_TIER_PORT_COL.get(tier)


async def get_or_create_sub_token(user_id: int) -> str:
    """Returns existing sub_token or atomically creates one. Race-safe.

    SELECT-then-UPDATE race: два параллельных вызова видели NULL и оба
    делали UPDATE — второй перетирал первого, а если в это время кто-то
    уже использовал первый токен (sub URL), он становился невалидным.
    Решаем CAS-pattern: UPDATE ... WHERE sub_token IS NULL — loser of
    race получает rowcount=0 и дочитывает значение победителя.
    """
    import secrets
    async with _connect() as db:
        # Fast-path: token already exists
        async with db.execute(
            "SELECT sub_token FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                return row[0]

        # Generate candidate token
        token = secrets.token_urlsafe(24)
        # Atomic CAS: only update if still NULL (loser of race re-reads winner)
        cur2 = await db.execute(
            "UPDATE users SET sub_token=? WHERE id=? AND sub_token IS NULL",
            (token, user_id),
        )
        await db.commit()
        if cur2.rowcount == 0:
            # Race lost. Re-read to get the winning token.
            async with db.execute(
                "SELECT sub_token FROM users WHERE id=?", (user_id,)
            ) as cur3:
                row2 = await cur3.fetchone()
                if row2 and row2[0]:
                    return row2[0]
            # Genuinely no user row? shouldn't happen but be defensive
            raise RuntimeError(f"get_or_create_sub_token: user {user_id} disappeared")
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
                srv.is_active AS server_active,
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


async def record_payment(user_id: int, subscription_id: int | None, method: str,
                          stars: int = 0, amount_usd: float = 0.0, tx_id: str = "") -> bool:
    """Records a payment. Returns True if NEW row inserted, False if dedup
    (duplicate tx_id — webhook retry, recurring re-fire).  Caller must handle
    False as «already processed» — don't extend sub, don't bonus referrer.

    UNIQUE(payments.tx_id) гарантирует idempotency на уровне БД.  Без
    защиты раньше Stars/Lava recurring дважды экстендили sub на 30 дней
    при retry webhook'а (audit 17.05 #1).
    """
    if not tx_id:
        # Legacy free/admin path без tx_id — пропускаем dedup, как было.
        async with _connect() as db:
            await db.execute("""
                INSERT INTO payments (user_id, subscription_id, method, stars, amount_usd, tx_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, subscription_id, method, stars, amount_usd, tx_id))
            await db.commit()
        return True

    async with _connect() as db:
        try:
            await db.execute("""
                INSERT INTO payments (user_id, subscription_id, method, stars, amount_usd, tx_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, subscription_id, method, stars, amount_usd, tx_id))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            # tx_id уже в БД — duplicate webhook / recurring retry.
            return False


async def is_payment_recorded(tx_id: str) -> bool:
    """True если payment с таким tx_id уже был записан.  Используется как
    gate перед extend_subscription/etc — не чтобы вернуть ответ юзеру,
    а чтобы не дублировать side-effects."""
    if not tx_id:
        return False
    async with _connect() as db:
        async with db.execute(
            "SELECT 1 FROM payments WHERE tx_id=? LIMIT 1", (tx_id,)
        ) as cur:
            return await cur.fetchone() is not None


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


async def get_active_configs_for_migration(server_id: int) -> list[dict]:
    """Активные конфиги на сервере с полными полями для миграции."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT c.id, c.wg_pubkey, c.vless_uuid, c.protocol,
                   c.subscription_id, c.user_id, c.peer_name, c.assigned_ip
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
        # NULL guard: datetime(NULL, '+7 days') = NULL → write обнулит expires_at
        # → подписка станет бесконечной. Используем тот же CASE-паттерн, что
        # в extend_subscription: если expires_at NULL или в прошлом — считаем
        # от now, иначе — от текущего expires_at.
        await db.execute(
            """UPDATE subscriptions
               SET expires_at = CASE
                       WHEN expires_at IS NULL
                            OR datetime(REPLACE(expires_at, 'T', ' ')) < datetime('now')
                       THEN datetime('now', ?)
                       ELSE datetime(expires_at, ?)
                   END
               WHERE user_id=? AND status='active'""",
            (modifier, modifier, referrer_id),
        )
        await db.commit()


async def try_award_referral_bonus(user_id: int, days: int, paid_sub_id: int | None = None) -> int | None:
    """Если у юзера был приглашающий И это его первая ПЛАТНАЯ подписка —
    КЛАДЁТ `days` дней в банк реферрера (users.ref_bonus_days) и возвращает
    referrer_id. Иначе None.

    Manual-redeem семантика: бонус НЕ применяется к expires_at автоматически.
    Реферрер должен сам нажать «Активировать» в Mini App «Мои бонусы».

    Race-safe через ATOMIC CLAIM на subscriptions.ref_bonus_awarded_to:
    если параллельные платежи реферала прилетят одновременно (теоретически),
    только первый UPDATE с WHERE ref_bonus_awarded_to IS NULL пройдёт.
    Остальные увидят rowcount=0 → return None без double-award.

    Условия:
    - Триал (`plan='vpn_trial'`) не считается «первой покупкой»
    - Реферрер должен быть PAID юзером (когда-то имел не-trial подписку)
    - Конкретный sub юзера ещё не использован для начисления бонуса
    """
    if paid_sub_id is None:
        # Без tracking-sub'а атомарный CLAIM не построить → не начисляем.
        # Старая логика поддерживала это, новая — нет (для refund integrity).
        logger.warning("try_award_referral_bonus called without paid_sub_id — skipping")
        return None

    async with _connect() as db:
        async with db.execute(
            "SELECT referred_by FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        referrer_id = row[0] if row and row[0] else None
        if not referrer_id:
            return None

        # Реферрер должен быть paid юзером (когда-то купил, неважно active ли сейчас).
        async with db.execute(
            """SELECT COUNT(*) FROM subscriptions
               WHERE user_id=? AND plan!='vpn_trial'""",
            (referrer_id,),
        ) as cur:
            referrer_paid_subs = (await cur.fetchone())[0]
        if referrer_paid_subs == 0:
            logger.info("referral skip: referrer %d не paid юзер", referrer_id)
            return None

        # paid_count реферала — реально оплаченные (не refunded).
        async with db.execute(
            """SELECT COUNT(*) FROM subscriptions
               WHERE user_id=? AND plan!='vpn_trial' AND refunded_at IS NULL""",
            (user_id,),
        ) as cur:
            paid_count = (await cur.fetchone())[0]
        if paid_count != 1:
            return None

        # Double-award guard: проверяем что НИ ОДИН из его sub'ов ещё не
        # использован для начисления бонуса (включая текущий — он мог уже
        # быть обработан параллельным запросом).
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

        # ATOMIC CLAIM: пытаемся пометить sub. Условие
        # `ref_bonus_awarded_to IS NULL` — защита от race: если параллельный
        # webhook-handler уже начислил, наш UPDATE затронет 0 строк.
        claim_cur = await db.execute(
            """UPDATE subscriptions
               SET ref_bonus_awarded_to=?, ref_bonus_days_awarded=?
               WHERE id=? AND ref_bonus_awarded_to IS NULL""",
            (referrer_id, days, paid_sub_id),
        )
        if claim_cur.rowcount == 0:
            logger.info("referral skip: sub %d уже помечен (race)", paid_sub_id)
            await db.commit()
            return None

        # Claim успешен → bank +days
        await db.execute(
            "UPDATE users SET ref_bonus_days=ref_bonus_days+? WHERE id=?",
            (days, referrer_id),
        )
        await db.commit()
        return referrer_id


async def redeem_referral_bonus(user_id: int) -> dict | None:
    """Манульная активация bonus-дней реферрера → продлевает active/grace sub.

    Условия:
    - У юзера есть active или grace sub (grace = заплатил, но истекло — всё ещё «платный»)
    - users.ref_bonus_days > 0

    Возвращает {days, new_expires_at} если успешно, None если нет sub
    или нет бонуса. Caller (API endpoint) должен показать юзеру нужный error.

    Race-safe через ATOMIC CLAIM:
    1. Сначала ищем sub (если нет — выходим без trying-claim, иначе банк
       мог бы обнулиться даже без extend'a)
    2. CAS-claim `UPDATE users SET ref_bonus_days=0 WHERE id=? AND ref_bonus_days=?`
       — rowcount=0 если кто-то параллельно уже редимнул
    3. Если claim успешен — extend sub.expires_at + ставим redeemed_at
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        # Шаг 0: читаем bank для CAS-сравнения
        async with db.execute(
            "SELECT ref_bonus_days FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        bank = (row["ref_bonus_days"] if row else 0) or 0
        if bank <= 0:
            return None

        # Шаг 1: ищем active или grace sub (grace = тоже «платный»)
        async with db.execute(
            """SELECT id, expires_at FROM subscriptions
               WHERE user_id=? AND status IN ('active', 'grace')
               ORDER BY id DESC LIMIT 1""",
            (user_id,),
        ) as cur:
            sub = await cur.fetchone()
        if not sub:
            return None  # нет sub — caller покажет «Купи подписку»

        # Шаг 2: ATOMIC CLAIM — обнуляем bank только если значение совпало с
        # прочитанным. Если параллельный redeem уже обнулил — rowcount=0,
        # выходим без double-extend.
        claim_cur = await db.execute(
            "UPDATE users SET ref_bonus_days=0 WHERE id=? AND ref_bonus_days=?",
            (user_id, bank),
        )
        if claim_cur.rowcount == 0:
            await db.commit()
            return None  # race: параллельный redeem забрал bank первым

        # Шаг 3: claim успешен — extend sub и ставим redeemed_at на tracking.
        # Для grace-подписки: сбрасываем статус в active и очищаем grace_until,
        # иначе sub остаётся throttled и scheduler заэкспайрит её по grace_until
        # несмотря на только что продлённый expires_at.
        # NULL guard: если expires_at NULL (legacy row), datetime(NULL, '+N days')
        # вернёт NULL и затрёт колонку → подписка станет бессрочной. Используем
        # тот же CASE-паттерн, что в extend_subscription.
        await db.execute(
            """UPDATE subscriptions
               SET expires_at  = CASE
                       WHEN expires_at IS NULL
                            OR datetime(REPLACE(expires_at, 'T', ' ')) < datetime('now')
                       THEN datetime('now', ?)
                       ELSE datetime(expires_at, ?)
                   END,
                   status      = CASE WHEN status = 'grace' THEN 'active' ELSE status END,
                   grace_until = CASE WHEN status = 'grace' THEN NULL ELSE grace_until END
               WHERE id = ?""",
            (f"+{bank} days", f"+{bank} days", sub["id"]),
        )
        # Ставим redeemed_at на все subs где этот юзер был реферрером и бонус
        # ещё не помечен redeemed. Для rollback логики — отличить bank vs sub.
        # Параллельно сохраняем КУДА именно ушли дни (sub["id"] реферрера) —
        # rollback_referral_bonus вычитает дни именно из этой подписки, без
        # subquery «newest active/grace» который мог промахнуться (audit R6).
        await db.execute(
            """UPDATE subscriptions
               SET ref_bonus_redeemed_at=CURRENT_TIMESTAMP,
                   ref_bonus_applied_to_sub_id=?
               WHERE ref_bonus_awarded_to=? AND ref_bonus_redeemed_at IS NULL""",
            (sub["id"], user_id),
        )
        # Достаём новый expires_at для возврата UI
        async with db.execute(
            "SELECT expires_at FROM subscriptions WHERE id=?", (sub["id"],)
        ) as cur:
            new_row = await cur.fetchone()
        await db.commit()
        return {
            "days": bank,
            "sub_id": sub["id"],
            "new_expires_at": new_row["expires_at"] if new_row else sub["expires_at"],
        }


# ── Helpers для referral pre-check (start handler + API endpoints) ────────────

async def has_any_subscription(user_id: int) -> bool:
    """True если у юзера есть ЛЮБАЯ подписка (trial / paid / expired / refunded).
    Используется в /start ref_<id> handler чтобы блокировать поздний referral."""
    async with _connect() as db:
        async with db.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? LIMIT 1", (user_id,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def has_active_paid_sub(user_id: int) -> bool:
    """True если у юзера есть текущая active или grace платная (не trial) подписка.
    Grace тоже считается «платным юзером» — он заплатил, просто истёк, всё ещё
    под throttle-обслуживанием. Используется для проверки «может ли юзер делиться
    реферальной ссылкой» и для redeem_referral_bonus."""
    async with _connect() as db:
        async with db.execute(
            """SELECT 1 FROM subscriptions
               WHERE user_id=? AND status IN ('active', 'grace') AND plan!='vpn_trial'
               LIMIT 1""",
            (user_id,),
        ) as cur:
            return (await cur.fetchone()) is not None


async def get_referred_by(user_id: int) -> int | None:
    """Возвращает referrer_id если юзер пришёл по реферальной ссылке, иначе None.
    Используется в trial.py для определения 7-day vs 3-day триала."""
    async with _connect() as db:
        async with db.execute(
            "SELECT referred_by FROM users WHERE id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


async def rollback_referral_bonus(refunded_sub_id: int) -> tuple[int, int] | None:
    """Откатывает реферальный бонус для подписки, которая была refund-нута.

    Возвращает (referrer_id, days) если откат прошёл, None если бонус не был начислен.

    Manual-redeem-aware logic:
    - Если ref_bonus_redeemed_at IS NULL → бонус ещё в банке, вычитаем из
      users.ref_bonus_days (без трогания active sub).
    - Если ref_bonus_redeemed_at IS NOT NULL → бонус уже сидит в sub.expires_at
      реферрера, вычитаем оттуда (банк пустой, не трогаем).

    Это избегает double-discount (раньше мы вычитали И из bank, И из active sub).

    Атомарность: claim-first pattern. Сначала UPDATE с WHERE ... IS NOT NULL
    очищает поля; если rowcount=0 — кто-то другой уже откатил, return None.
    Защита от двух одновременных /refund_ref вызовов.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        # Шаг 1: читаем awarded поля + redeemed_at статус
        async with db.execute(
            """SELECT ref_bonus_awarded_to, ref_bonus_days_awarded, ref_bonus_redeemed_at
               FROM subscriptions WHERE id=?""",
            (refunded_sub_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not row["ref_bonus_awarded_to"]:
            return None

        referrer_id = row["ref_bonus_awarded_to"]
        days = row["ref_bonus_days_awarded"] or 0
        was_redeemed = row["ref_bonus_redeemed_at"] is not None
        if days == 0:
            return None

        # Шаг 2: ATOMIC CLAIM — обнуляем поля при условии что они не NULL.
        # Если параллельный запрос уже сделал то же — rowcount=0, выходим
        # без двойного списания дней.
        claim_cur = await db.execute(
            """UPDATE subscriptions
               SET ref_bonus_awarded_to=NULL, ref_bonus_days_awarded=0,
                   ref_bonus_redeemed_at=NULL
               WHERE id=? AND ref_bonus_awarded_to IS NOT NULL""",
            (refunded_sub_id,),
        )
        if claim_cur.rowcount == 0:
            await db.commit()
            return None  # race: другой запрос откатил первым

        # Шаг 3: claim успешен, делаем reverse — куда именно зависит от redeemed_at
        if was_redeemed:
            # Бонус был активирован → сидит в sub.expires_at реферрера.
            # Используем сохранённый ref_bonus_applied_to_sub_id (audit R6),
            # а не subquery «newest active/grace» — раньше это могло вычесть
            # дни из НЕ той подписки реферрера (e.g. если он купил новый план
            # после redeem'а).
            async with db.execute(
                "SELECT ref_bonus_applied_to_sub_id FROM subscriptions WHERE id=?",
                (refunded_sub_id,),
            ) as cur:
                target_row = await cur.fetchone()
            target_sub_id = target_row["ref_bonus_applied_to_sub_id"] if target_row else None
            if target_sub_id is None:
                # Legacy row: redeemed_at стоит, но ref_bonus_applied_to_sub_id
                # не записан (бонус редимнут до R6 migration). Скипаем
                # decrement — guess'ить какую sub шортить рискованно, проще
                # дать рефереру оставить эти дни (это меньше зло чем урезать
                # не ту подписку). Логируем чтобы админ видел.
                logger.warning(
                    "rollback_referral_bonus sub=%d: legacy row, "
                    "ref_bonus_applied_to_sub_id IS NULL but redeemed_at set — "
                    "skipping expires_at decrement for referrer=%d days=%d",
                    refunded_sub_id, referrer_id, days,
                )
            else:
                # Decrement только если target sub ещё active/grace.
                # Если target expired/refunded — дни уже «сгорели», ничего не делаем.
                # NULL guard: datetime(NULL, '-N days') = NULL → write обнулит
                # expires_at, превратив sub в бессрочную. Добавляем
                # `expires_at IS NOT NULL` в WHERE — если legacy-row без даты,
                # вычитать просто нечего.
                await db.execute(
                    "UPDATE subscriptions SET expires_at=datetime(expires_at, ?) "
                    "WHERE id=? AND status IN ('active', 'grace') "
                    "AND expires_at IS NOT NULL",
                    (f"-{days} days", target_sub_id),
                )
        else:
            # Бонус в банке pending → вычитаем оттуда
            await db.execute(
                """UPDATE users SET ref_bonus_days=MAX(0, ref_bonus_days-?)
                   WHERE id=?""",
                (days, referrer_id),
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


async def get_winback_candidates(days_min: int = 7, days_max: int = 14) -> list[dict]:
    """Возвращает подписки, чей статус стал 'expired' от days_min до days_max дней назад,
    winback_sent=0, пользователь не купил новую active/grace подписку после истечения.

    Семантика: ищем пользователей, которые ушли и не вернулись в окне [7, 14] дней.
    За пределами 14 дней слать уже бессмысленно (холодный контакт).
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        # Audit 21.05 #7: было `datetime(s.updated_at)` — колонки updated_at в
        # subscriptions нет → OperationalError на каждом scheduler-проходе,
        # winback не работает с момента ввода фичи.  Правильное поле — expires_at:
        # «когда подписка истекла» = реальная отметка ухода юзера.
        async with db.execute(
            """
            SELECT s.id, s.user_id, s.plan
            FROM subscriptions s
            WHERE s.status = 'expired'
              AND s.winback_sent = 0
              AND s.plan != 'vpn_trial'
              AND datetime(s.expires_at) <= datetime('now', ?||' days')
              AND datetime(s.expires_at) >= datetime('now', ?||' days')
              AND NOT EXISTS (
                  SELECT 1 FROM subscriptions s2
                  WHERE s2.user_id = s.user_id
                    AND s2.status IN ('active', 'grace')
              )
            """,
            (f"-{days_min}", f"-{days_max}"),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_winback_sent(sub_id: int):
    """Помечает подписку как получившую win-back сообщение."""
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET winback_sent=1 WHERE id=?",
            (sub_id,),
        )
        await db.commit()


async def get_trial_nudge_candidates() -> list[dict]:
    """Триальные подписки, активированные 20-48 часов назад, nudge ещё не отправлен.
    Окно 20-48ч: юзер успел попробовать VPN, но до конца триала ещё есть время.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id FROM subscriptions
            WHERE plan = 'vpn_trial'
              AND status = 'active'
              AND trial_nudge_sent = 0
              AND datetime(created_at) <= datetime('now', '-20 hours')
              AND datetime(created_at) >= datetime('now', '-48 hours')
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_trial_nudge_sent(sub_id: int):
    async with _connect() as db:
        await db.execute(
            "UPDATE subscriptions SET trial_nudge_sent=1 WHERE id=?",
            (sub_id,),
        )
        await db.commit()
