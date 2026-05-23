import Database from 'better-sqlite3'
import path from 'path'

// DB path: лучше задать абсолютно через env (BOT_DB_PATH), потому что
// Next.js standalone build делает `process.chdir(__dirname)` → cwd
// больше не равен systemd WorkingDirectory. Раньше относительный путь
// `../bot/bot.db` ломался: cwd становился /opt/vpnbot/admin/.next/standalone/
// и резолв давал /opt/vpnbot/admin/.next/bot/bot.db (не существует) → 500.
const DB_PATH = process.env.BOT_DB_PATH
  ?? path.resolve(process.cwd(), '../bot/bot.db')

let _db: Database.Database | null = null

export function db(): Database.Database {
  if (!_db) {
    _db = new Database(DB_PATH, { readonly: true })
  }
  return _db
}

/**
 * Список admin-юзеров — исключаются из метрик выручки/конверсии/LTV.
 * Без этого тестовые покупки админа летят в дашборд и портят статистику.
 *
 * Источник: ADMIN_IDS (CSV) + fallback на ADMIN_ID. Тот же env что для auth —
 * один источник правды, новые админы автоматически исключаются.
 */
const EXCLUDED_USER_IDS: number[] = (() => {
  const csv = (process.env.ADMIN_IDS ?? process.env.ADMIN_ID ?? '')
  return csv.split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isFinite(n) && n > 0)
})()

/** Готовая SQL-инъекция-безопасная часть `AND <col> NOT IN (...)`. */
function excludeAdminsClause(col: string = 'user_id'): string {
  if (EXCLUDED_USER_IDS.length === 0) return ''
  return ` AND ${col} NOT IN (${EXCLUDED_USER_IDS.join(',')}) `
}

export function stats() {
  const d = db()
  // Все продажные метрики исключают тестовые покупки админов.
  // users — счётчик регистраций (его не фильтруем; видимость общего трафика).
  // activeSubs / totalStars — деньги, фильтруем.
  const exclSubs = excludeAdminsClause('user_id')
  const users        = (d.prepare('SELECT COUNT(*) as n FROM users').get() as { n: number }).n
  // active + grace — оба «платящие» состояния. Раньше grace падал из счётчика
  // и Dashboard показывал «нет подписок» когда у них всех уже throttle. Чтобы
  // визуально различать — отдельный grace счётчик.
  const activeSubs   = (d.prepare(`SELECT COUNT(*) as n FROM subscriptions WHERE status IN ('active','grace') ${exclSubs}`).get() as { n: number }).n
  const graceSubs    = (d.prepare(`SELECT COUNT(*) as n FROM subscriptions WHERE status='grace' ${exclSubs}`).get() as { n: number }).n
  // refunded_at IS NULL — иначе возвращённые суммы продолжают красоваться
  // в «всего заработано». Фильтр статусов оставлен (active/expired) чтобы
  // не считать недозавершённые pending sub'ы.
  const totalStars   = (d.prepare(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE status IN ('active','expired','grace') AND refunded_at IS NULL ${exclSubs}`).get() as { n: number }).n
  const openTickets  = (d.prepare("SELECT COUNT(*) as n FROM support_tickets WHERE status='open'").get() as { n: number }).n
  return { users, activeSubs, graceSubs, totalStars, openTickets }
}

export function recentPayments(limit = 20) {
  const excl = excludeAdminsClause('s.user_id')
  // 1=1 — корректное условие, добавляет AND-фильтр без логического влияния
  // если фильтра нет (excl="") или если есть.
  return db().prepare(`
    SELECT s.id, s.plan, s.stars_paid, s.amount_rub, s.payment_id, s.status,
           s.payment_provider,
           s.created_at, s.expires_at,
           u.username, u.first_name
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE 1=1 ${excl}
    ORDER BY s.created_at DESC LIMIT ?
  `).all(limit)
}

export type PaymentRow = {
  id: number
  user_id: number
  plan: string
  stars_paid: number
  amount_rub: number
  payment_id: string | null
  status: string
  refunded_at: string | null
  created_at: string
  expires_at: string | null
  method: string  // 'stars' | 'crypto' | 'free' | 'admin_grant'
  username: string | null
  first_name: string | null
  granted_by_admin_id: number | null
}

export function allPayments(filters: {
  method?: 'stars' | 'crypto' | 'oxapay' | 'lavatop' | 'free' | 'admin_grant' | 'trial'
  plan?: string
  days?: number          // last N days
  includeRefunds?: boolean
  limit?: number
} = {}) {
  const { method, plan, days, includeRefunds = true, limit = 500 } = filters
  const excl = excludeAdminsClause('s.user_id')

  // Метод определяем по payment_id префиксу:
  //   "crypto_*"      → CryptoBot
  //   "free_*"        → legacy trial reward
  //   "admin_grant_*" → подарок от админа через /grant (с granted_by_admin_id)
  //   иначе           → Telegram Stars (sha256 charge_id или legacy формат)
  const where: string[] = ['1=1']
  const params: unknown[] = []

  if (method === 'crypto')           where.push("s.payment_id LIKE 'crypto_%'")
  else if (method === 'oxapay')      where.push("s.payment_id LIKE 'oxapay_%'")
  else if (method === 'lavatop')     where.push("s.payment_id LIKE 'lavatop_%'")
  else if (method === 'free')        where.push("s.payment_id LIKE 'free_%'")
  else if (method === 'admin_grant') where.push("s.payment_id LIKE 'admin_grant_%'")
  else if (method === 'trial')       where.push("s.payment_id LIKE 'trial_%'")
  // Stars — единственный provider без префикса. Исключаем все известные
  // non-Stars префиксы (раньше учитывались только 3 → oxapay/lavatop/trial
  // попадали в Stars-фильтр).
  else if (method === 'stars')       where.push(
    "s.payment_id NOT LIKE 'crypto_%' "
    + "AND s.payment_id NOT LIKE 'oxapay_%' "
    + "AND s.payment_id NOT LIKE 'lavatop_%' "
    + "AND s.payment_id NOT LIKE 'free_%' "
    + "AND s.payment_id NOT LIKE 'admin_grant_%' "
    + "AND s.payment_id NOT LIKE 'trial_%' "
    + "AND s.payment_id IS NOT NULL",
  )

  if (plan) { where.push('s.plan = ?'); params.push(plan) }
  const daysNum = Math.floor(Number(days))
  if (days && Number.isFinite(daysNum) && daysNum > 0) {
    where.push(`s.created_at > datetime('now', '-${daysNum} days')`)
  }
  if (!includeRefunds) where.push('s.refunded_at IS NULL')

  // granted_by_admin_id — JOIN с payments по subscription_id. Для одной подписки
  // в payments может быть несколько строк (refund-marker и т.п.) — берём
  // последний free_grant если он есть. Подзапрос быстрый: idx_payments_granted_admin
  // отсеивает обычные платежи, и subscription_id в payments редко повторяется.
  const sql = `
    SELECT s.id, s.user_id, s.plan, s.stars_paid, s.amount_rub, s.payment_id,
           s.status, s.refunded_at, s.created_at, s.expires_at,
           COALESCE(s.payment_provider,
                    CASE
                      WHEN s.payment_id LIKE 'crypto_%'      THEN 'cryptobot'
                      WHEN s.payment_id LIKE 'oxapay_%'      THEN 'oxapay'
                      WHEN s.payment_id LIKE 'lavatop_%'     THEN 'lavatop'
                      WHEN s.payment_id LIKE 'admin_grant_%' THEN 'gift'
                      WHEN s.payment_id LIKE 'trial_%'       THEN 'trial'
                      WHEN s.payment_id LIKE 'free_%'        THEN 'free'
                      ELSE 'stars'
                    END) as method,
           u.username, u.first_name,
           (SELECT p.granted_by_admin_id
              FROM payments p
              WHERE p.subscription_id = s.id AND p.is_free_grant = 1
              ORDER BY p.id DESC LIMIT 1) AS granted_by_admin_id
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE ${where.join(' AND ')} ${excl}
    ORDER BY s.created_at DESC LIMIT ?
  `
  params.push(limit)
  return db().prepare(sql).all(...params) as PaymentRow[]
}

export function allTickets(status = 'open') {
  return db().prepare(`
    SELECT t.id, t.category, t.message, t.status, t.created_at,
           u.username, u.first_name, u.id as user_id
    FROM support_tickets t
    JOIN users u ON u.id = t.user_id
    WHERE t.status = ?
    ORDER BY t.created_at DESC
  `).all(status)
}

export type UserRow = {
  id: number
  username: string | null
  first_name: string | null
  created_at: string
  referred_by: number | null
  ref_bonus_days: number
  is_banned: number   // 0/1; SQLite has no bool
  banned_at: string | null
  banned_reason: string | null
}

export type SubRow = {
  id: number
  user_id: number
  plan: string
  payment_id: string | null
  stars_paid: number
  amount_rub: number
  status: string
  expires_at: string | null
  grace_until: string | null
  pending_plan: string | null
  refunded_at: string | null
  created_at: string
}

export type UserTicketRow = {
  id: number
  user_id: number
  category: string
  message: string
  status: string
  created_at: string
}

export function userFull(userId: number) {
  const d = db()
  const user    = d.prepare('SELECT * FROM users WHERE id = ?').get(userId) as UserRow | undefined
  const subs    = d.prepare('SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC').all(userId) as SubRow[]
  const tickets = d.prepare('SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT 5').all(userId) as UserTicketRow[]
  // Активные конфиги (для каждой подписки) — чтобы понять есть ли реальные пиры
  const configCount = (d.prepare(
    "SELECT COUNT(*) as n FROM configs WHERE user_id = ? AND status='active'"
  ).get(userId) as { n: number }).n
  return { user, subs, tickets, configCount }
}

export function allServers() {
  // Note: колонки `status` в `servers` нет (не в migration). is_active — это
  // тот флаг что нам нужен. status оставлен для совместимости в типе если кто
  // ещё дергает — но из SELECT убран.
  return db().prepare(`
    SELECT id, name, flag, city, host, agent_url, protocol,
           capacity, active_peers, is_active, created_at,
           wg_pubkey
    FROM servers ORDER BY created_at DESC
  `).all()
}

export function searchUsers(query: string) {
  const q = `%${query}%`
  return db().prepare(`
    SELECT u.*,
      (SELECT COUNT(*) FROM subscriptions WHERE user_id = u.id AND status='active') as active_subs
    FROM users u
    WHERE u.username LIKE ? OR u.first_name LIKE ? OR CAST(u.id AS TEXT) LIKE ?
    LIMIT 20
  `).all(q, q, q)
}

// ── Analytics ─────────────────────────────────────────────────────────────────
// Lightweight read-only queries для админ-дашборда. Никаких внешних трекеров —
// всё считается из SQLite на лету.

export function analyticsSummary() {
  const d = db()
  const r = (sql: string, ...params: unknown[]) => (d.prepare(sql).get(...params) as { n: number }).n
  const excl = excludeAdminsClause('user_id')
  return {
    users_total:          r('SELECT COUNT(*) as n FROM users'),
    users_30d:            r("SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-30 days')"),
    users_7d:             r("SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-7 days')"),
    // active + grace — оба «платящие» состояния (grace = throttle 256kbps в течение 14д
    // после expires_at, юзер ещё считается клиентом). Совпадает с activePayingCount().
    subs_active:          r(`SELECT COUNT(*) as n FROM subscriptions WHERE status IN ('active','grace') ${excl}`),
    subs_paid_30d:        r(`SELECT COUNT(*) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-30 days') ${excl}`),
    subs_trial_30d:       r(`SELECT COUNT(*) as n FROM subscriptions WHERE plan='vpn_trial' AND created_at > datetime('now','-30 days') ${excl}`),
    revenue_stars_30d:    r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-30 days') ${excl}`),
    revenue_stars_7d:     r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-7 days') ${excl}`),
    expired_30d:          r(`SELECT COUNT(*) as n FROM subscriptions WHERE status='expired' AND datetime(REPLACE(expires_at, 'T', ' ')) > datetime('now','-30 days') ${excl}`),
  }
}

export function dailyRevenueLast30() {
  const excl = excludeAdminsClause('user_id')
  return db().prepare(`
    SELECT date(created_at) as day,
           COUNT(*)                    as paid_subs,
           COALESCE(SUM(stars_paid),0) as stars
    FROM subscriptions
    WHERE plan != 'vpn_trial'
      AND refunded_at IS NULL
      AND created_at > datetime('now','-30 days') ${excl}
    GROUP BY day
    ORDER BY day ASC
  `).all() as Array<{ day: string; paid_subs: number; stars: number }>
}

export function planMix30d() {
  const excl = excludeAdminsClause('user_id')
  return db().prepare(`
    SELECT plan,
           COUNT(*)                    as count,
           COALESCE(SUM(stars_paid),0) as stars,
           COALESCE(SUM(amount_rub),0) as amount_rub
    FROM subscriptions
    WHERE created_at > datetime('now','-30 days') ${excl}
    GROUP BY plan
    ORDER BY count DESC
  `).all() as Array<{ plan: string; count: number; stars: number; amount_rub: number }>
}

export function trialFunnel30d() {
  // Воронка: сколько юзеров пришло → взяли триал → купили платный после триала.
  const d = db()
  const excl   = excludeAdminsClause('user_id')
  const exclT  = excludeAdminsClause('t.user_id')
  const exclS  = excludeAdminsClause('s.user_id')
  const new_users = (d.prepare(
    "SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-30 days')"
  ).get() as { n: number }).n

  const trial_users = (d.prepare(
    `SELECT COUNT(DISTINCT user_id) as n FROM subscriptions
     WHERE plan='vpn_trial' AND created_at > datetime('now','-30 days') ${excl}`
  ).get() as { n: number }).n

  // Юзеры, у которых был триал И ПОТОМ платная подписка
  const trial_then_paid = (d.prepare(`
    SELECT COUNT(DISTINCT t.user_id) as n FROM subscriptions t
    JOIN subscriptions p ON p.user_id = t.user_id
                         AND p.plan != 'vpn_trial'
                         AND p.created_at > t.created_at
    WHERE t.plan = 'vpn_trial'
      AND t.created_at > datetime('now','-30 days') ${exclT}
  `).get() as { n: number }).n

  // Платные юзеры, у которых триала не было
  const direct_paid = (d.prepare(`
    SELECT COUNT(DISTINCT user_id) as n FROM subscriptions s
    WHERE s.plan != 'vpn_trial'
      AND s.created_at > datetime('now','-30 days') ${exclS}
      AND NOT EXISTS (
        SELECT 1 FROM subscriptions t
        WHERE t.user_id = s.user_id AND t.plan = 'vpn_trial'
      )
  `).get() as { n: number }).n

  return {
    new_users,
    trial_users,
    trial_then_paid,
    direct_paid,
    trial_conversion: trial_users > 0 ? Math.round((trial_then_paid / trial_users) * 100) : 0,
    register_to_paid: new_users > 0 ? Math.round(((trial_then_paid + direct_paid) / new_users) * 100) : 0,
  }
}

// ── Clients / Money page ──────────────────────────────────────────────────────

export function topClients(limit = 50) {
  // Топ юзеров по сумме потраченных stars (LTV-прокси).
  // Триалы исключены — это не выручка. Админы тоже исключены — тест-данные.
  const excl = excludeAdminsClause('u.id')
  // AD-F16: surface total_rub alongside total_stars so the Clients page can
  // distinguish Stars-only payers from RUB-channel payers. HAVING gate now
  // accepts either non-zero (so CryptoBot/OxaPay/Lava-only customers show up).
  return db().prepare(`
    SELECT u.id, u.username, u.first_name, u.created_at as joined_at,
           COUNT(s.id) FILTER (WHERE s.plan != 'vpn_trial' AND s.refunded_at IS NULL)                       as paid_subs,
           COUNT(s.id) FILTER (WHERE s.plan = 'vpn_trial')                        as trial_subs,
           COALESCE(SUM(CASE WHEN s.plan != 'vpn_trial' AND s.refunded_at IS NULL THEN s.stars_paid END), 0) as total_stars,
           COALESCE(SUM(CASE WHEN s.plan != 'vpn_trial' AND s.refunded_at IS NULL THEN s.amount_rub END), 0) as total_rub,
           -- active + grace: оба считаем «текущим планом». current_plan_status
           -- даёт UI'у возможность пометить grace отдельно («подписка истекает»).
           MAX(CASE WHEN s.status IN ('active','grace') THEN s.plan END)          as current_plan,
           MAX(CASE WHEN s.status IN ('active','grace') THEN s.status END)        as current_plan_status,
           MAX(s.created_at)                                                       as last_purchase,
           MAX(CASE WHEN s.status IN ('active','grace') THEN s.expires_at END)    as active_until
    FROM users u
    LEFT JOIN subscriptions s ON s.user_id = u.id
    WHERE 1=1 ${excl}
    GROUP BY u.id
    HAVING total_stars > 0 OR total_rub > 0
    ORDER BY total_stars DESC, total_rub DESC, last_purchase DESC
    LIMIT ?
  `).all(limit) as Array<{
    id: number
    username: string | null
    first_name: string | null
    joined_at: string
    paid_subs: number
    trial_subs: number
    total_stars: number
    total_rub: number
    current_plan: string | null
    current_plan_status: string | null
    last_purchase: string | null
    active_until: string | null
  }>
}

export function moneyTotals() {
  const d = db()
  const r = (sql: string) => (d.prepare(sql).get() as { n: number }).n
  const excl = excludeAdminsClause('user_id')
  return {
    total_revenue_stars: r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL ${excl}`),
    total_revenue_rub:   r(`SELECT COALESCE(SUM(amount_rub),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL ${excl}`),
    paying_users:        r(`SELECT COUNT(DISTINCT user_id) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND (stars_paid > 0 OR amount_rub > 0) ${excl}`),
    avg_revenue_per_payer: 0,
    avg_ltv_stars:       0,
    revenue_7d:          r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-7 days') ${excl}`),
    revenue_30d:         r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-30 days') ${excl}`),
    revenue_90d:         r(`SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-90 days') ${excl}`),
    revenue_rub_7d:      r(`SELECT COALESCE(SUM(amount_rub),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-7 days') ${excl}`),
    revenue_rub_30d:     r(`SELECT COALESCE(SUM(amount_rub),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-30 days') ${excl}`),
    revenue_rub_90d:     r(`SELECT COALESCE(SUM(amount_rub),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND refunded_at IS NULL AND created_at > datetime('now','-90 days') ${excl}`),
    repeat_buyers:       r(`SELECT COUNT(*) as n FROM (
      SELECT user_id FROM subscriptions
      WHERE plan!='vpn_trial' AND refunded_at IS NULL AND (stars_paid > 0 OR amount_rub > 0) ${excl}
      GROUP BY user_id HAVING COUNT(*) > 1
    )`),
  }
}

// ── Monitoring ────────────────────────────────────────────────────────────────

export function monitoringSnapshot() {
  const d = db()
  const r = (sql: string) => (d.prepare(sql).get() as { n: number }).n

  // Сервера + последняя проба из server_health_log + uptime 24h. Раньше
  // monitoring был «снэпшот из БД, реальный live смотри на /status».
  // Теперь админка сразу показывает кто живой/мёртвый по последнему probe.
  // `status` колонки в `servers` нет в migration — убрана из SELECT.
  // last_probe_* подтягиваем из server_health_log (создаётся health-probe
  // scheduler'ом в боте).
  const servers = d.prepare(`
    SELECT s.id, s.name, s.flag, s.city, s.host, s.protocol,
           s.active_peers, s.capacity, s.is_active, s.agent_url, s.created_at,
           (SELECT status FROM server_health_log
             WHERE server_id=s.id ORDER BY id DESC LIMIT 1) as last_probe_status,
           (SELECT latency_ms FROM server_health_log
             WHERE server_id=s.id ORDER BY id DESC LIMIT 1) as last_probe_latency,
           (SELECT checked_at FROM server_health_log
             WHERE server_id=s.id ORDER BY id DESC LIMIT 1) as last_probe_at
    FROM servers s ORDER BY s.is_active DESC, s.protocol, s.id
  `).all() as Array<{
    id: number; name: string; flag: string | null; city: string | null;
    host: string; protocol: string;
    active_peers: number; capacity: number;
    is_active: number; agent_url: string | null;
    created_at: string;
    last_probe_status: 'up' | 'down' | 'unknown' | null;
    last_probe_latency: number | null;
    last_probe_at: string | null;
  }>

  // Uptime 24h из health log для каждого активного сервера
  const uptimeRows = d.prepare(`
    SELECT server_id,
           SUM(CASE WHEN status='up'   THEN 1 ELSE 0 END) as up_n,
           SUM(CASE WHEN status='down' THEN 1 ELSE 0 END) as down_n
    FROM server_health_log
    WHERE checked_at > datetime('now','-24 hours')
    GROUP BY server_id
  `).all() as Array<{ server_id: number; up_n: number; down_n: number }>

  const uptimeMap: Record<number, number | null> = {}
  uptimeRows.forEach(r => {
    const total = r.up_n + r.down_n
    uptimeMap[r.server_id] = total > 0 ? Math.round((r.up_n / total) * 1000) / 10 : null
  })

  const serversWithUptime = servers.map(s => ({
    ...s,
    uptime_24h_pct: uptimeMap[s.id] ?? null,
  }))

  return {
    servers: serversWithUptime,
    active_configs:     r("SELECT COUNT(*) as n FROM configs WHERE status='active'"),
    empty_slots:        r("SELECT COUNT(*) as n FROM configs WHERE status='empty'"),
    revoked_configs:    r("SELECT COUNT(*) as n FROM configs WHERE status='revoked'"),
    open_tickets:       r("SELECT COUNT(*) as n FROM support_tickets WHERE status='open'"),
    closed_tickets:     r("SELECT COUNT(*) as n FROM support_tickets WHERE status='closed'"),
    expiring_3d:        r("SELECT COUNT(*) as n FROM subscriptions WHERE status='active' AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now','+3 days')"),
    expiring_1d:        r("SELECT COUNT(*) as n FROM subscriptions WHERE status='active' AND datetime(REPLACE(expires_at, 'T', ' ')) <= datetime('now','+1 day')"),
  }
}

// ── Goal progress ─────────────────────────────────────────────────────────────

const GOAL = 1000

export function payingUsersGrowth() {
  const d = db()
  const excl = excludeAdminsClause('user_id')
  const rows = d.prepare(`
    SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as new_paid
    FROM subscriptions
    WHERE plan NOT IN ('vpn_trial') AND refunded_at IS NULL AND (stars_paid > 0 OR amount_rub > 0) ${excl}
    GROUP BY month
    ORDER BY month
  `).all() as Array<{ month: string; new_paid: number }>

  // Running cumulative + project forward
  let cum = 0
  const actual = rows.map(r => {
    cum += r.new_paid
    return { month: r.month, new_paid: r.new_paid, cumulative: cum, projected: false }
  })

  // Project forward from avg of last 2 real months
  const last2 = rows.slice(-2)
  const avgNew = last2.length > 0 ? last2.reduce((a, b) => a + b.new_paid, 0) / last2.length : 5
  const projMonths: typeof actual = []
  let projCum = cum
  let lastMonth = actual.at(-1)?.month ?? new Date().toISOString().slice(0, 7)

  while (projCum < GOAL && projMonths.length < 24) {
    const [y, m] = lastMonth.split('-').map(Number)
    const next = m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`
    projCum = Math.round(projCum + avgNew)
    projMonths.push({ month: next, new_paid: 0, cumulative: projCum, projected: true })
    lastMonth = next
  }

  return { points: [...actual, ...projMonths], goal: GOAL, currentCum: cum, avgNewPerMonth: Math.round(avgNew) }
}

export function activePayingCount() {
  const d = db()
  const excl = excludeAdminsClause('user_id')
  const row = d.prepare(`
    SELECT COUNT(DISTINCT user_id) as n FROM subscriptions
    WHERE status IN ('active', 'grace') AND plan != 'vpn_trial'
      AND (stars_paid > 0 OR amount_rub > 0) ${excl}
  `).get() as { n: number }
  return row.n
}

// ── Monitoring charts ─────────────────────────────────────────────────────────

export function newSubsPerDay(days = 14) {
  const d = db()
  const safeDays = Math.max(1, Math.min(3650, Math.floor(days)))
  return d.prepare(`
    SELECT date(created_at) as day,
      SUM(CASE WHEN plan = 'vpn_trial' THEN 1 ELSE 0 END) as trials,
      SUM(CASE WHEN plan != 'vpn_trial' THEN 1 ELSE 0 END) as paid
    FROM subscriptions
    WHERE created_at > datetime('now', '-' || ? || ' days')
    GROUP BY day
    ORDER BY day
  `).all(safeDays) as Array<{ day: string; trials: number; paid: number }>
}

export function latencyHistory24h() {
  const d = db()
  return d.prepare(`
    SELECT s.id as server_id, s.name, s.flag,
      strftime('%Y-%m-%dT%H:00:00', h.checked_at) as ts,
      CAST(ROUND(AVG(CASE WHEN h.status='up' THEN h.latency_ms END)) AS INTEGER) as avg_ms,
      CAST(100.0 * SUM(CASE WHEN h.status='up' THEN 1 ELSE 0 END) / COUNT(*) AS INTEGER) as uptime_pct
    FROM servers s
    JOIN server_health_log h ON h.server_id = s.id
    WHERE h.checked_at > datetime('now', '-24 hours')
    GROUP BY s.id, strftime('%Y-%m-%dT%H', h.checked_at)
    ORDER BY s.id, h.checked_at
  `).all() as Array<{
    server_id: number; name: string; flag: string | null;
    ts: string; avg_ms: number | null; uptime_pct: number
  }>
}

export function uptimeStrip24h() {
  const d = db()
  return d.prepare(`
    SELECT server_id,
      CAST((julianday(checked_at) - julianday(datetime('now', '-24 hours'))) * 48 AS INTEGER) as bucket,
      SUM(CASE WHEN status='up' THEN 1 ELSE 0 END) as up_n,
      COUNT(*) as total_n
    FROM server_health_log
    WHERE checked_at > datetime('now', '-24 hours')
    GROUP BY server_id, bucket
    ORDER BY server_id, bucket
  `).all() as Array<{ server_id: number; bucket: number; up_n: number; total_n: number }>
}

export function activeSubsByPlan() {
  const d = db()
  return d.prepare(`
    SELECT plan, COUNT(*) as n
    FROM subscriptions
    WHERE status IN ('active', 'grace')
    GROUP BY plan
    ORDER BY n DESC
  `).all() as Array<{ plan: string; n: number }>
}

// ── Tickets page ──────────────────────────────────────────────────────────────

export function allTicketsWithUser(limit = 100, statusFilter?: string) {
  const d = db()
  if (statusFilter) {
    return d.prepare(`
      SELECT t.id, t.category, t.message, t.status, t.created_at, t.admin_msg_id,
             u.username, u.first_name, u.id as user_id
      FROM support_tickets t
      JOIN users u ON u.id = t.user_id
      WHERE t.status = ?
      ORDER BY t.created_at DESC
      LIMIT ?
    `).all(statusFilter, limit) as Array<TicketRow>
  }
  return d.prepare(`
    SELECT t.id, t.category, t.message, t.status, t.created_at, t.admin_msg_id,
           u.username, u.first_name, u.id as user_id
    FROM support_tickets t
    JOIN users u ON u.id = t.user_id
    ORDER BY t.created_at DESC
    LIMIT ?
  `).all(limit) as Array<TicketRow>
}

type TicketRow = {
  id: number
  category: string
  message: string
  status: string
  created_at: string
  admin_msg_id: number | null
  username: string | null
  first_name: string | null
  user_id: number
}

export function topReferrers(limit = 10) {
  const excl = excludeAdminsClause('u.id')
  return db().prepare(`
    SELECT u.id, u.username, u.first_name,
           COUNT(r.id) as invited,
           COALESCE(SUM(CASE WHEN s.plan != 'vpn_trial' THEN 1 ELSE 0 END), 0) as invited_paid
    FROM users u
    JOIN users r       ON r.referred_by = u.id
    LEFT JOIN subscriptions s ON s.user_id = r.id
    WHERE u.id IN (SELECT DISTINCT referred_by FROM users WHERE referred_by IS NOT NULL) ${excl}
    GROUP BY u.id
    ORDER BY invited_paid DESC, invited DESC
    LIMIT ?
  `).all(limit) as Array<{ id: number; username: string | null; first_name: string | null; invited: number; invited_paid: number }>
}

// ── Attribution / UTM tracking ────────────────────────────────────────────────

export type AttributionRow = {
  source: string         // 'utm:tg_pythonist_jul' | 'referral:123' | 'direct' | ...
  starts: number         // регистраций (users.created_at попало в окно)
  trial_users: number    // взяли триал
  paying_users: number   // купили платный план хотя бы раз
  revenue_stars: number  // суммарно ⭐ за период
  revenue_rub: number    // суммарно ₽ (CryptoBot/Lava/Cryptomus)
  // ── для source='referral:USER_ID' резолвим реферера в users:
  // username (для t.me/{username} линка) + first_name (для отображения).
  // Для не-referral строк оба null.
  referrer_username: string | null
  referrer_first_name: string | null
}

/**
 * Группировка new юзеров по traffic_source с метриками воронки и выручки.
 *
 * Окно: фильтр по `users.created_at`. Это first-touch — юзер считается «принёс
 * X» только если зарегистрирован в окне (его исходный источник). Если пришёл
 * по другой ссылке позже — source НЕ перетирается (иначе реклама бы крала
 * зачёт у канала, который привёл оригинал).
 *
 * Выручка считается по всем платным подпискам этих юзеров за всё время —
 * чтоб LTV-картинка по источнику росла с возрастом когорты.
 *
 * `days`:  null → всё время, иначе последние N дней по users.created_at.
 */
export function attributionByPeriod(days: number | null = 30): AttributionRow[] {
  const d = db()
  const exclU = excludeAdminsClause('u.id')

  let dateWhere = ''
  if (days !== null) {
    const n = Math.max(1, Math.min(3650, Math.floor(days)))
    dateWhere = `AND u.created_at > datetime('now','-${n} days')`
  }

  // Inner subquery: на каждого юзера в окне считаем флаги и суммы. Внешний
  // SELECT агрегирует по source. Один SQL — один full-scan users в окне.
  // На admin'ах: фильтруем во внешнем WHERE (u.id NOT IN admins) — этого
  // достаточно, в subscriptions ходим уже за non-admin user_id.
  return d.prepare(`
    SELECT
      source,
      COUNT(*)                              as starts,
      SUM(had_trial)                        as trial_users,
      SUM(had_paid)                         as paying_users,
      SUM(stars_total)                      as revenue_stars,
      SUM(rub_total)                        as revenue_rub,
      -- referrer-данные одинаковы для всех строк с одним source (referrer_id
      -- запечён в строке source), поэтому MAX() — просто способ aggregate'нуть
      -- без GROUP BY. NULL пройдёт NULL для не-referral source'ов.
      MAX(referrer_username)                as referrer_username,
      MAX(referrer_first_name)              as referrer_first_name
    FROM (
      SELECT
        COALESCE(u.traffic_source, 'direct') as source,
        CASE WHEN EXISTS (
          SELECT 1 FROM subscriptions s
          WHERE s.user_id = u.id AND s.plan = 'vpn_trial'
        ) THEN 1 ELSE 0 END                  as had_trial,
        CASE WHEN EXISTS (
          SELECT 1 FROM subscriptions s
          WHERE s.user_id = u.id
            AND s.plan != 'vpn_trial'
            AND s.refunded_at IS NULL
            AND (s.stars_paid > 0 OR s.amount_rub > 0)
        ) THEN 1 ELSE 0 END                  as had_paid,
        COALESCE((
          SELECT SUM(s.stars_paid) FROM subscriptions s
          WHERE s.user_id = u.id AND s.plan != 'vpn_trial' AND s.refunded_at IS NULL
        ), 0)                                 as stars_total,
        COALESCE((
          SELECT SUM(s.amount_rub) FROM subscriptions s
          WHERE s.user_id = u.id AND s.plan != 'vpn_trial' AND s.refunded_at IS NULL
        ), 0)                                 as rub_total,
        -- Резолв реферера: если source = 'referral:USER_ID', тянем username и
        -- first_name. Subquery'и быстрые (PK lookup), но выполняются per-user;
        -- alternatively можно делать post-aggregation map, но dataset небольшой.
        CASE
          WHEN u.traffic_source LIKE 'referral:%'
          THEN (SELECT ru.username FROM users ru
                WHERE ru.id = CAST(substr(u.traffic_source, 10) AS INTEGER))
          ELSE NULL
        END                                   as referrer_username,
        CASE
          WHEN u.traffic_source LIKE 'referral:%'
          THEN (SELECT ru.first_name FROM users ru
                WHERE ru.id = CAST(substr(u.traffic_source, 10) AS INTEGER))
          ELSE NULL
        END                                   as referrer_first_name
      FROM users u
      WHERE 1=1 ${dateWhere} ${exclU}
    ) per_user
    GROUP BY source
    ORDER BY paying_users DESC, starts DESC
  `).all() as AttributionRow[]
}

/**
 * Последние использованные UTM-коды (только префикс utm:) — для блока
 * «Недавние кампании» в админке. Сортировка по числу платящих.
 *
 * Без time-фильтра: помогает помнить «как я называл эту кампанию полгода назад».
 */
export function recentUtmCodes(limit = 20): Array<{ code: string; paying: number; starts: number }> {
  const d = db()
  const excl = excludeAdminsClause('u.id')
  return d.prepare(`
    SELECT
      substr(u.traffic_source, 5) as code,
      COUNT(*)                     as starts,
      SUM(CASE WHEN EXISTS (
        SELECT 1 FROM subscriptions s
        WHERE s.user_id = u.id
          AND s.plan != 'vpn_trial'
          AND (s.stars_paid > 0 OR s.amount_rub > 0)
      ) THEN 1 ELSE 0 END)         as paying
    FROM users u
    WHERE u.traffic_source LIKE 'utm:%' ${excl}
    GROUP BY code
    ORDER BY paying DESC, starts DESC
    LIMIT ?
  `).all(limit) as Array<{ code: string; paying: number; starts: number }>
}
// ─── Track A: revenue & stuck payments ────────────────────────────────────────
// Метрики выручки строятся по `subscriptions` (а не `payments`), потому что
// именно там лежат `stars_paid` + `amount_rub` + `refunded_at` per-purchase.
// `payments` table — log транзакций для idempotency (tx_id UNIQUE) и хранит
// `method` (stars/crypto/oxapay/lavatop), но суммы выручки в RUB кешируются
// именно в subscriptions.amount_rub. Поэтому revenue-метрики живут на subs,
// а method-breakdown тащим через JOIN с payments (group by payments.method).

const STARS_TO_RUB = 1.4

/**
 * Сводные revenue-метрики для верхнего блока главной dashboard.
 *
 * Все суммы — RUB-эквивалент (stars * 1.4 + amount_rub). Stars-курс
 * прибит к 1.4₽ как в page.tsx — реальная выплата от Telegram идёт по
 * их курсу, но для дашбордов унифицирован.
 *
 * `today`/`wtd`/`mtd` — относительно даты UTC: today = с 00:00 UTC;
 * wtd = с понедельника 00:00 UTC; mtd = с 1-го числа 00:00 UTC.
 * SQLite не имеет ISO week, но `strftime('%w')` даёт day-of-week
 * (0=вс, 1=пн…6=сб) → используем `date('now','-' || ((6+%w)%7) || ' days')`
 * для понедельника. Same-day прошлой недели/месяца — для delta.
 *
 * Refund-фильтр: refunded_at IS NULL — учитываем только не-возвращённые.
 * Триал-фильтр: plan!='vpn_trial' — он не выручка.
 * Админы: excludeAdminsClause.
 */
export function revenueWindows() {
  const d = db()
  const excl = excludeAdminsClause('user_id')

  // Шаблон суммы RUB-эквивалента (для краткости).
  // COALESCE на NULL'ы (бывает если sub без stars/rub — admin grant, free).
  const RUB_EXPR = `COALESCE(SUM(stars_paid),0) * ${STARS_TO_RUB} + COALESCE(SUM(amount_rub),0)`

  const sum = (where: string): number => {
    const row = d.prepare(`
      SELECT ${RUB_EXPR} as n
      FROM subscriptions
      WHERE plan != 'vpn_trial' AND refunded_at IS NULL AND ${where} ${excl}
    `).get() as { n: number }
    return Math.round(row.n || 0)
  }

  // ── Today (UTC) ──
  const today      = sum(`date(created_at) = date('now')`)
  // «Сегодня неделю назад» = ровно тот же день недели прошлой недели; для
  // подневного сравнения берём промежуток того же часа на 7 дней назад.
  // Упрощаем: «прошлая неделя на сейчас» = всё что между -7d и -7d+now;
  // здесь делаем просто same-day-last-week = date('now','-7 days').
  const todayPrev  = sum(`date(created_at) = date('now','-7 days')`)

  // ── WTD: с понедельника 00:00 UTC ──
  // `strftime('%w','now')` возвращает 0=вс..6=сб; для понедельника
  // нужно (`%w` - 1 + 7) % 7 = days_back_to_monday.
  // SQLite позволяет нам сделать date('now', 'weekday 0', '-7 days', '+1 day'),
  // но проще через CASE: cast int, расчёт offset.
  const monOffsetExpr = `((CAST(strftime('%w','now') AS INTEGER) + 6) % 7)`
  const wtd = sum(`date(created_at) >= date('now','-' || ${monOffsetExpr} || ' days')`)
  // То же окно «7 дней назад»: с прошлого понедельника по same-day-of-week.
  // Сдвигаем оба конца на -7 days: start = понедельник прошлой недели,
  // end = same-day-of-week прошлой недели (= date('now','-7 days')).
  const wtdPrev = sum(`
    date(created_at) >= date('now','-' || (${monOffsetExpr} + 7) || ' days')
    AND date(created_at) <= date('now','-7 days')
  `)

  // ── MTD: с 1-го числа текущего месяца ──
  const mtd = sum(`date(created_at) >= date('now','start of month')`)
  // Прошлый месяц с 1-го до same-day-of-month. day-of-month — `strftime('%d')`.
  // Тонкий момент: если сегодня 31-е, а в прошлом месяце 30 дней — окно будет
  // полным прошлым месяцем (date(now,-1 month,start of month) - date(now,start of month,-1 day)).
  // Для дельты OK — это даёт максимум возможной выручки прошлого месяца к этой дате.
  const mtdPrev = sum(`
    date(created_at) >= date('now','start of month','-1 month')
    AND date(created_at) <= date('now','-1 month')
  `)

  // Stars-breakdown отдельно для бейджа «⭐ N» под суммой.
  // (Stars-only — без RUB-эквивалента, чтоб увидеть «фактический» Telegram
  // оборот в звёздах; видимая разница vs всего total = выручка через CryptoBot/Lava/OxaPay.)
  const stars = (where: string): number => {
    const row = d.prepare(`
      SELECT COALESCE(SUM(stars_paid),0) as n
      FROM subscriptions
      WHERE plan != 'vpn_trial' AND refunded_at IS NULL AND ${where} ${excl}
    `).get() as { n: number }
    return row.n || 0
  }

  return {
    today:        { rub: today, stars: stars(`date(created_at) = date('now')`) },
    today_prev:   todayPrev,
    wtd:          { rub: wtd },
    wtd_prev:     wtdPrev,
    mtd:          { rub: mtd },
    mtd_prev:     mtdPrev,
  }
}

/**
 * Средний чек за 30 дней + 7-day conversion rate.
 *
 * avg_check: SUM(rub-эквивалент)/COUNT(paid subs за 30д). Триалы исключены.
 * conversion_7d: % новых юзеров за 7 дней, у которых уже есть платная подписка
 *   (любой статус кроме refunded). Прокси для onboarding-конверсии — высокое
 *   значение = быстрый purchase в первую неделю.
 */
export function avgCheckAndConversion() {
  const d = db()
  const excl = excludeAdminsClause('user_id')

  const row30 = d.prepare(`
    SELECT
      COUNT(*) as n,
      COALESCE(SUM(stars_paid),0) * ${STARS_TO_RUB} + COALESCE(SUM(amount_rub),0) as rub
    FROM subscriptions
    WHERE plan != 'vpn_trial' AND refunded_at IS NULL
      AND created_at > datetime('now','-30 days') ${excl}
  `).get() as { n: number; rub: number }

  const avg_check_rub = row30.n > 0 ? Math.round(row30.rub / row30.n) : 0

  // 7d conversion: NEW users за 7d (users.created_at > -7d), сколько из них
  // уже оплатили хотя бы один платный план (любая sub с stars_paid OR amount_rub > 0,
  // не refunded). Админы исключены и из числителя, и из знаменателя.
  const exclU = excludeAdminsClause('u.id')
  const new7 = (d.prepare(`
    SELECT COUNT(*) as n FROM users u
    WHERE u.created_at > datetime('now','-7 days') ${exclU}
  `).get() as { n: number }).n

  const new7Paid = (d.prepare(`
    SELECT COUNT(DISTINCT u.id) as n FROM users u
    WHERE u.created_at > datetime('now','-7 days') ${exclU}
      AND EXISTS (
        SELECT 1 FROM subscriptions s
        WHERE s.user_id = u.id
          AND s.plan != 'vpn_trial'
          AND s.refunded_at IS NULL
          AND (s.stars_paid > 0 OR s.amount_rub > 0)
      )
  `).get() as { n: number }).n

  return {
    avg_check_rub,
    paid_subs_30d: row30.n,
    conversion_7d_pct: new7 > 0 ? Math.round((new7Paid / new7) * 1000) / 10 : 0,
    new_users_7d: new7,
    new_users_7d_paid: new7Paid,
  }
}

/**
 * Refund-rate за 30 дней.
 *
 * Числитель: SUM возвращённых RUB-эквивалент за 30 дней (refunded_at в окне).
 * Знаменатель: SUM(всех paid за 30 дней — включая те, что потом вернули).
 *
 * Tone-rules в дашборде:
 *   < 2%  → positive (норма)
 *   > 5%  → negative (тревога — провёрка fraud/provision-fail-cascade)
 *   else  → default
 */
export function refundRate30d() {
  const d = db()
  const excl = excludeAdminsClause('user_id')
  const RUB_EXPR = `COALESCE(SUM(stars_paid),0) * ${STARS_TO_RUB} + COALESCE(SUM(amount_rub),0)`

  // Всего платежей (включая refunded'ные — refunded_at IS NULL фильтр НЕ ставим)
  // за 30 дней.
  const total = (d.prepare(`
    SELECT ${RUB_EXPR} as n
    FROM subscriptions
    WHERE plan != 'vpn_trial' AND created_at > datetime('now','-30 days') ${excl}
  `).get() as { n: number }).n

  // Возвращённые: refunded_at в окне 30 дней (а не created_at — иначе refund'ы
  // старых платежей не попадут в текущий месяц).
  const refunded = (d.prepare(`
    SELECT ${RUB_EXPR} as n
    FROM subscriptions
    WHERE plan != 'vpn_trial' AND refunded_at IS NOT NULL
      AND refunded_at > datetime('now','-30 days') ${excl}
  `).get() as { n: number }).n

  return {
    refunded_rub: Math.round(refunded || 0),
    total_rub: Math.round(total || 0),
    rate_pct: total > 0 ? Math.round((refunded / total) * 1000) / 10 : 0,
  }
}

/**
 * Method breakdown за 30 дней (для stacked bar chart в analytics).
 *
 * Источник — `payments` (где есть колонка method), JOIN с subscriptions
 * для RUB-эквивалента. На каждой дате — четыре числа: stars/crypto/oxapay/lavatop.
 *
 * Если payments записи нет (legacy subs без entry в payments — раннее
 * legacy free/admin path), fallback: всё считается как 'stars'. Это
 * graceful-degrade: одна-двухцветная стопка вместо пустоты.
 *
 * stars в payments хранятся в `payments.stars` (raw stars), а amount_rub
 * нет — для RUB-чанков (crypto/oxapay/lavatop) надо тянуть amount_rub из
 * subscriptions через JOIN. Поэтому возвращаем структуру { day, stars,
 * crypto_rub, oxapay_rub, lavatop_rub } — front соберёт stacked bars.
 */
export function methodBreakdown30d() {
  const d = db()
  const excl = excludeAdminsClause('p.user_id')

  // Защита от отсутствия payments-таблицы или колонки method (на всякий —
  // если в future миграция переименует, return пустой массив → UI покажет
  // «нет данных» и не упадёт).
  try {
    return d.prepare(`
      SELECT date(p.created_at) as day,
             SUM(CASE WHEN p.method='stars'   THEN p.stars                   ELSE 0 END) as stars,
             SUM(CASE WHEN p.method='crypto'  THEN COALESCE(s.amount_rub,0) ELSE 0 END) as crypto_rub,
             SUM(CASE WHEN p.method='oxapay'  THEN COALESCE(s.amount_rub,0) ELSE 0 END) as oxapay_rub,
             SUM(CASE WHEN p.method='lavatop' THEN COALESCE(s.amount_rub,0) ELSE 0 END) as lavatop_rub
      FROM payments p
      LEFT JOIN subscriptions s ON s.id = p.subscription_id
      WHERE p.created_at > datetime('now','-30 days')
        AND p.refunded_at IS NULL
        AND COALESCE(p.is_free_grant, 0) = 0     -- админ-гранты не выручка
        ${excl}
      GROUP BY day
      ORDER BY day ASC
    `).all() as Array<{
      day: string
      stars: number
      crypto_rub: number
      oxapay_rub: number
      lavatop_rub: number
    }>
  } catch {
    // Schema mismatch (e.g. tests без payments-таблицы) → fallback на
    // subscriptions-only stars-серию. Front увидит «one-colour stack».
    return d.prepare(`
      SELECT date(s.created_at) as day,
             COALESCE(SUM(s.stars_paid), 0) as stars,
             0 as crypto_rub,
             0 as oxapay_rub,
             0 as lavatop_rub
      FROM subscriptions s
      WHERE s.plan != 'vpn_trial' AND s.refunded_at IS NULL
        AND s.created_at > datetime('now','-30 days')
        ${excludeAdminsClause('s.user_id')}
      GROUP BY day
      ORDER BY day ASC
    `).all() as Array<{
      day: string
      stars: number
      crypto_rub: number
      oxapay_rub: number
      lavatop_rub: number
    }>
  }
}

export type StuckPayment = {
  payment_id: number
  subscription_id: number | null
  user_id: number
  username: string | null
  first_name: string | null
  method: string
  stars: number | null
  amount_rub: number | null
  created_at: string
  sub_status: string | null  // null если нет sub'а вообще (sub_id was null)
  reason: string             // 'no_sub' | 'expired' | 'refunded' — для UI-бейджа
}

/**
 * Race-rejection платежи: payment записан как completed/paid, но
 * subscription либо отсутствует (subscription_id IS NULL), либо
 * в статусе expired/refunded.
 *
 * Это сигнал багов в provision-cascade (fcfe3ee, bfaab08): юзер
 * заплатил, но из-за race condition его подписка не активировалась
 * или была откатана. Нужен admin-triage (refund или manual grant).
 *
 * Фильтр:
 *  - payments.status IN ('completed','paid') AND refunded_at IS NULL
 *  - is_free_grant = 0 (грантовые/триальные не считаем — они не «оплаченные»)
 *  - method != 'free' / 'admin_grant' / 'trial' (defense in depth)
 *  - subscription отсутствует ИЛИ status в expired/refunded
 *
 * Сортировка: новые → старые.
 */
export function stuckPayments(limit = 50): StuckPayment[] {
  const d = db()
  const excl = excludeAdminsClause('p.user_id')

  try {
    return d.prepare(`
      SELECT p.id                            as payment_id,
             p.subscription_id               as subscription_id,
             p.user_id                       as user_id,
             u.username                      as username,
             u.first_name                    as first_name,
             p.method                        as method,
             p.stars                         as stars,
             s.amount_rub                    as amount_rub,
             p.created_at                    as created_at,
             s.status                        as sub_status,
             CASE
               WHEN p.subscription_id IS NULL                  THEN 'no_sub'
               WHEN s.id IS NULL                               THEN 'no_sub'
               WHEN s.status = 'refunded'                      THEN 'refunded'
               WHEN s.status = 'expired'                       THEN 'expired'
               ELSE 'unknown'
             END                             as reason
      FROM payments p
      LEFT JOIN subscriptions s ON s.id = p.subscription_id
      LEFT JOIN users u         ON u.id = p.user_id
      WHERE p.status IN ('completed','paid')
        AND p.refunded_at IS NULL
        AND COALESCE(p.is_free_grant, 0) = 0
        AND p.method NOT IN ('free','admin_grant','trial')
        AND (
              p.subscription_id IS NULL
           OR s.id IS NULL
           OR s.status IN ('expired','refunded')
        )
        ${excl}
      ORDER BY p.created_at DESC
      LIMIT ?
    `).all(limit) as StuckPayment[]
  } catch {
    // Schema mismatch — возвращаем пустой массив, UI покажет «нет stuck».
    return []
  }
}

// ─── Track B: payments ────────────────────────────────────────────────────────
// Search/detail/export helpers, добавлены для /payments search bar и
// /payments/[id]. Метрики (Track A) живут выше — здесь только row-level
// доступы: «найди эту строку», «дай мне детали этого платежа», «выгрузи».

/**
 * Расширение `allPayments` с поддержкой свободного поиска.
 *
 * Логика поиска (одна строка q, ищется в любом из полей):
 *  - q = чистое число → user_id (точно) ИЛИ subscription_id (точно), OR-склейка
 *    (юзер мог ввести и то и другое; показываем что нашли)
 *  - q = строка → username LIKE %q% OR payment_id LIKE q% (prefix-match для
 *    tx_id, потому что charge_id длинный и юзер копирует начало). first_name
 *    тоже добавляем — UI показывает first_name, не username, в основной колонке.
 *
 * Эскейпим LIKE-wildcards (`%`, `_`) чтобы юзер мог искать tx_id содержащий
 * подчёркивание (напр. `crypto_abc`) — без эскейпа `_` матчит любой символ.
 */
function escapeLike(s: string): string {
  // SQLite LIKE: `_` = любой символ, `%` = любая строка. Используем
  // ESCAPE '\' в SQL — здесь экранируем `\`, `%`, `_`.
  return s.replace(/\\/g, '\\\\').replace(/[%_]/g, c => '\\' + c)
}

export function searchPayments(filters: {
  method?: 'stars' | 'crypto' | 'oxapay' | 'lavatop' | 'free' | 'admin_grant' | 'trial'
  plan?: string
  days?: number
  includeRefunds?: boolean
  q?: string
  limit?: number
} = {}) {
  const { method, plan, days, includeRefunds = true, q, limit = 500 } = filters
  const excl = excludeAdminsClause('s.user_id')

  const where: string[] = ['1=1']
  const params: unknown[] = []

  if (method === 'crypto')           where.push("s.payment_id LIKE 'crypto_%'")
  else if (method === 'oxapay')      where.push("s.payment_id LIKE 'oxapay_%'")
  else if (method === 'lavatop')     where.push("s.payment_id LIKE 'lavatop_%'")
  else if (method === 'free')        where.push("s.payment_id LIKE 'free_%'")
  else if (method === 'admin_grant') where.push("s.payment_id LIKE 'admin_grant_%'")
  else if (method === 'trial')       where.push("s.payment_id LIKE 'trial_%'")
  else if (method === 'stars')       where.push(
    "s.payment_id NOT LIKE 'crypto_%' "
    + "AND s.payment_id NOT LIKE 'oxapay_%' "
    + "AND s.payment_id NOT LIKE 'lavatop_%' "
    + "AND s.payment_id NOT LIKE 'free_%' "
    + "AND s.payment_id NOT LIKE 'admin_grant_%' "
    + "AND s.payment_id NOT LIKE 'trial_%' "
    + "AND s.payment_id IS NOT NULL",
  )

  if (plan) { where.push('s.plan = ?'); params.push(plan) }
  const daysNum = Math.floor(Number(days))
  if (days && Number.isFinite(daysNum) && daysNum > 0) {
    where.push(`s.created_at > datetime('now', '-${daysNum} days')`)
  }
  if (!includeRefunds) where.push('s.refunded_at IS NULL')

  // Свободный поиск — последним фильтром (после всех остальных). LIKE-параметры
  // с эскейпом + ESCAPE '\\' в SQL.
  if (q && q.trim()) {
    const qTrim = q.trim()
    // numeric → дублим как user_id OR sub_id; иначе строковый OR (username/first_name/payment_id)
    if (/^\d+$/.test(qTrim)) {
      const n = parseInt(qTrim, 10)
      where.push('(s.user_id = ? OR s.id = ?)')
      params.push(n, n)
    } else {
      const like = escapeLike(qTrim)
      where.push(
        "(u.username LIKE ? ESCAPE '\\' "
        + "OR u.first_name LIKE ? ESCAPE '\\' "
        + "OR s.payment_id LIKE ? ESCAPE '\\')",
      )
      params.push(`%${like}%`, `%${like}%`, `${like}%`)
    }
  }

  const sql = `
    SELECT s.id, s.user_id, s.plan, s.stars_paid, s.amount_rub, s.payment_id,
           s.status, s.refunded_at, s.created_at, s.expires_at,
           COALESCE(s.payment_provider,
                    CASE
                      WHEN s.payment_id LIKE 'crypto_%'      THEN 'cryptobot'
                      WHEN s.payment_id LIKE 'oxapay_%'      THEN 'oxapay'
                      WHEN s.payment_id LIKE 'lavatop_%'     THEN 'lavatop'
                      WHEN s.payment_id LIKE 'admin_grant_%' THEN 'gift'
                      WHEN s.payment_id LIKE 'trial_%'       THEN 'trial'
                      WHEN s.payment_id LIKE 'free_%'        THEN 'free'
                      ELSE 'stars'
                    END) as method,
           u.username, u.first_name,
           (SELECT p.granted_by_admin_id
              FROM payments p
              WHERE p.subscription_id = s.id AND p.is_free_grant = 1
              ORDER BY p.id DESC LIMIT 1) AS granted_by_admin_id
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE ${where.join(' AND ')} ${excl}
    ORDER BY s.created_at DESC LIMIT ?
  `
  params.push(limit)
  return db().prepare(sql).all(...params) as PaymentRow[]
}

/**
 * Детальная вьюха одного платежа.
 *
 * Возвращает sub-row (та же базовая структура что и в `allPayments`), плюс:
 *  - связанный user (id, username, first_name, is_banned)
 *  - все `payments` строки этой подписки (для multi-charge upgrade-сценариев)
 *
 * Timeline на frontend'е собирается из:
 *   created_at  — `subscriptions.created_at` (момент покупки)
 *   paid_at     — то же что created_at (наша БД фиксирует только успешную
 *                 покупку, нет отдельного `paid_at`-события)
 *   applied_at  — `subscriptions.created_at` если sub перешла в active/grace/
 *                 expired/refunded (~всё кроме pending)
 *   refunded_at — `subscriptions.refunded_at`
 */
export type PaymentDetailRow = PaymentRow & {
  ban_status: number | null            // users.is_banned
  payments_count: number               // сколько payments-строк связано с этой sub
  pending_plan: string | null
}

export type PaymentChargeRow = {
  id: number
  method: string
  amount_usd: number | null
  stars: number | null
  tx_id: string | null
  status: string
  refunded_at: string | null
  created_at: string
  is_free_grant: number
  granted_by_admin_id: number | null
}

export function paymentDetail(subId: number): {
  row: PaymentDetailRow | null
  charges: PaymentChargeRow[]
} {
  const d = db()
  const row = d.prepare(`
    SELECT s.id, s.user_id, s.plan, s.stars_paid, s.amount_rub, s.payment_id,
           s.status, s.refunded_at, s.created_at, s.expires_at,
           s.pending_plan,
           COALESCE(s.payment_provider,
                    CASE
                      WHEN s.payment_id LIKE 'crypto_%'      THEN 'cryptobot'
                      WHEN s.payment_id LIKE 'oxapay_%'      THEN 'oxapay'
                      WHEN s.payment_id LIKE 'lavatop_%'     THEN 'lavatop'
                      WHEN s.payment_id LIKE 'admin_grant_%' THEN 'gift'
                      WHEN s.payment_id LIKE 'trial_%'       THEN 'trial'
                      WHEN s.payment_id LIKE 'free_%'        THEN 'free'
                      ELSE 'stars'
                    END) as method,
           u.username, u.first_name, u.is_banned as ban_status,
           (SELECT p.granted_by_admin_id
              FROM payments p
              WHERE p.subscription_id = s.id AND p.is_free_grant = 1
              ORDER BY p.id DESC LIMIT 1) AS granted_by_admin_id,
           (SELECT COUNT(*) FROM payments WHERE subscription_id = s.id) AS payments_count
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE s.id = ?
  `).get(subId) as PaymentDetailRow | undefined

  if (!row) return { row: null, charges: [] }

  // Все payments-строки этой подписки. На Stars upgrade-сценарии будет 2+ строк.
  const charges = d.prepare(`
    SELECT id, method, amount_usd, stars, tx_id, status, refunded_at, created_at,
           is_free_grant, granted_by_admin_id
    FROM payments
    WHERE subscription_id = ?
    ORDER BY id ASC
  `).all(subId) as PaymentChargeRow[]

  return { row, charges }
}

// ─── Track C: tickets ────────────────────────────────────────────────────────
// Inbox-style sorting, SLA badge и inline user-context для страницы /tickets.
//
// support_tickets.created_at — единственный таймштамп на тикете (нет
// last_message_at / last_admin_reply_at колонок). Для SLA-логики опираемся на
// audit_log: каждая отправка ответа админом пишет строку с
//   action='ticket_reply', target='ticket:{id}'
// (см. bot/services/webapp_api.py::handle_admin_ticket_reply).
//
// Из этого вычисляем last_admin_reply_at — MAX(audit_log.created_at) по
// target='ticket:N'. Если NULL → админ ещё не отвечал (unread dot в UI).

export type TicketInboxRow = {
  id: number
  user_id: number
  category: string
  message: string
  status: string
  created_at: string
  admin_msg_id: number | null
  username: string | null
  first_name: string | null
  last_admin_reply_at: string | null
  // sort_at = COALESCE(last_admin_reply_at, created_at). last_message_at-style
  // прокси — последняя «активность» по тикету.
  sort_at: string
  // 1 → последнее «событие» это user message и админ ещё не отвечал
  // (показываем unread dot в списке).
  unread: 0 | 1
}

export function ticketsInbox(opts: {
  status?: 'open' | 'closed' | 'all'
  category?: string
  limit?: number
} = {}): TicketInboxRow[] {
  const { status = 'open', category, limit = 200 } = opts
  const where: string[] = ['1=1']
  const params: unknown[] = []

  if (status === 'open' || status === 'closed') {
    where.push('t.status = ?')
    params.push(status)
  }
  if (category) {
    where.push('t.category = ?')
    params.push(category)
  }

  // LEFT JOIN на агрегированный audit_log по target='ticket:N'. Используем
  // подзапрос — индекс idx_audit_action хорошо отсекает action='ticket_reply',
  // а target-фильтр работает по строковому ключу. Альтернатива (GROUP BY на
  // audit_log с JOIN) — медленнее на росте audit_log.
  const sql = `
    SELECT t.id, t.category, t.message, t.status, t.created_at, t.admin_msg_id,
           u.username, u.first_name, u.id as user_id,
           (SELECT MAX(a.created_at) FROM audit_log a
             WHERE a.action='ticket_reply' AND a.target = 'ticket:' || t.id
           ) as last_admin_reply_at
    FROM support_tickets t
    JOIN users u ON u.id = t.user_id
    WHERE ${where.join(' AND ')}
    ORDER BY COALESCE(
      (SELECT MAX(a.created_at) FROM audit_log a
         WHERE a.action='ticket_reply' AND a.target = 'ticket:' || t.id),
      t.created_at
    ) DESC
    LIMIT ?
  `
  params.push(limit)
  const rows = db().prepare(sql).all(...params) as Array<{
    id: number; category: string; message: string; status: string;
    created_at: string; admin_msg_id: number | null;
    username: string | null; first_name: string | null; user_id: number;
    last_admin_reply_at: string | null;
  }>

  return rows.map(r => ({
    ...r,
    sort_at: r.last_admin_reply_at ?? r.created_at,
    // unread = open + админ ещё не отвечал. Без ticket_messages таблицы
    // более точного сигнала нет — пока админ молчит, последняя активность
    // тикета это user-сообщение.
    unread: (r.status === 'open' && !r.last_admin_reply_at) ? 1 : 0,
  }))
}

/**
 * Single-ticket fetch — для detail-view. Возвращает тикет + last_admin_reply_at,
 * либо null если id невалиден / не найден.
 */
export function ticketById(id: number): TicketInboxRow | null {
  if (!Number.isFinite(id)) return null
  const row = db().prepare(`
    SELECT t.id, t.category, t.message, t.status, t.created_at, t.admin_msg_id,
           u.username, u.first_name, u.id as user_id,
           (SELECT MAX(a.created_at) FROM audit_log a
             WHERE a.action='ticket_reply' AND a.target = 'ticket:' || t.id
           ) as last_admin_reply_at
    FROM support_tickets t
    JOIN users u ON u.id = t.user_id
    WHERE t.id = ?
    LIMIT 1
  `).get(id) as {
    id: number; category: string; message: string; status: string;
    created_at: string; admin_msg_id: number | null;
    username: string | null; first_name: string | null; user_id: number;
    last_admin_reply_at: string | null;
  } | undefined
  if (!row) return null
  return {
    ...row,
    sort_at: row.last_admin_reply_at ?? row.created_at,
    unread: (row.status === 'open' && !row.last_admin_reply_at) ? 1 : 0,
  }
}

// ── Ticket chat-style thread ──────────────────────────────────────────────────
// Detail-view рендерит timeline сообщений (user initial + admin replies),
// а не одну bubble + reply form. Без миграции БД: initial message живёт в
// support_tickets, admin reply text хранится в audit_log.details как JSON
// (см. bot/services/webapp_api.py::handle_admin_ticket_reply).
//
// Старый формат details был `reply_chars=N close_after=bool` — без текста.
// Для backward-compat: try/catch JSON.parse, если не парсится — fallback
// bubble «(текст ответа не сохранён — старый формат)». После 2026-05-23 новые
// записи будут содержать text.
//
// Note: follow-up юзер-сообщения пока deferred — бот при повторном обращении
// создаёт новый ticket, а не append'ит в существующий. Когда сделаем
// follow-up handler, нужно будет завести ticket_messages-таблицу и
// заменить эту функцию на её query.

export type TicketThreadMessage = {
  sender: 'user' | 'admin'
  text: string
  created_at: string
  admin_id?: number | null
}

export function ticketThread(ticketId: number): TicketThreadMessage[] {
  if (!Number.isFinite(ticketId)) return []
  const d = db()

  const initial = d.prepare(
    'SELECT message, created_at FROM support_tickets WHERE id = ?',
  ).get(ticketId) as { message: string; created_at: string } | undefined

  if (!initial) return []

  const messages: TicketThreadMessage[] = [{
    sender: 'user',
    text: initial.message || '',
    created_at: initial.created_at,
  }]

  // Admin replies из audit_log. action=ticket_reply, target=ticket:N.
  // ORDER BY created_at ASC чтобы старые ответы шли первыми (chat-flow).
  // id tiebreaker — если два reply в одну секунду (миллисекундная точность
  // SQLite CURRENT_TIMESTAMP — посекундная), сохраняем порядок вставки.
  const replies = d.prepare(`
    SELECT admin_id, details, created_at
    FROM audit_log
    WHERE action = 'ticket_reply' AND target = ?
    ORDER BY created_at ASC, id ASC
  `).all(`ticket:${ticketId}`) as Array<{
    admin_id: number
    details: string | null
    created_at: string
  }>

  for (const r of replies) {
    let text = ''
    try {
      // Новый формат: JSON {text, close_after, chars}. Если details NULL
      // или не JSON (legacy «reply_chars=N…») — поймаем в catch.
      const parsed = JSON.parse(r.details ?? '') as { text?: unknown }
      if (parsed && typeof parsed.text === 'string') {
        text = parsed.text
      } else {
        text = '(текст ответа не сохранён — старый формат)'
      }
    } catch {
      text = '(текст ответа не сохранён — старый формат)'
    }
    messages.push({
      sender: 'admin',
      text,
      created_at: r.created_at,
      admin_id: r.admin_id || null,
    })
  }

  return messages
}

/**
 * SLA breach count для badge в навбаре. «Просроченный» тикет:
 *   - status='open'
 *   - админ ещё НЕ отвечал (audit_log пуст по этому ticket_id)
 *   - с момента создания тикета прошло > 2 часов
 *
 * Если у тикета есть admin reply — он уже не breach (юзер мог снова написать,
 * но без message-stream таблицы мы это не отследим; сознательно занижаем
 * counter чтобы не паниковать без оснований).
 */
export function slaBreachCount(): number {
  const row = db().prepare(`
    SELECT COUNT(*) as n
    FROM support_tickets t
    WHERE t.status = 'open'
      AND datetime(t.created_at) < datetime('now', '-2 hours')
      AND NOT EXISTS (
        SELECT 1 FROM audit_log a
        WHERE a.action = 'ticket_reply' AND a.target = 'ticket:' || t.id
      )
  `).get() as { n: number }
  return row.n
}

/**
 * Список уникальных категорий из open-тикетов — для фильтра в /tickets UI.
 */
export function ticketCategoriesOpen(): Array<{ category: string; count: number }> {
  return db().prepare(`
    SELECT category, COUNT(*) as count
    FROM support_tickets
    WHERE status='open'
    GROUP BY category
    ORDER BY count DESC
  `).all() as Array<{ category: string; count: number }>
}

export type UserContextPayload = {
  profile: {
    id: number
    username: string | null
    first_name: string | null
    created_at: string
    referred_by: number | null
    traffic_source: string | null
  } | null
  subscription: {
    plan: string
    status: string
    expires_at: string | null
    grace_until: string | null
  } | null
  recentPayments: Array<{
    id: number
    plan: string
    method: string
    stars_paid: number
    amount_rub: number
    status: string
    refunded_at: string | null
    created_at: string
  }>
  ban: {
    is_banned: number
    banned_at: string | null
    banned_reason: string | null
  } | null
}

/**
 * Данные для inline UserContextPanel в detail-view тикета. 4 запроса в одну
 * функцию — sub, top-3 payments, ban-status, profile/referral. Чтобы админ
 * не уходил на /clients/[id] ради «кто это».
 */
export function userContextForTicket(userId: number): UserContextPayload {
  const d = db()
  const profile = d.prepare(`
    SELECT id, username, first_name, created_at, referred_by, traffic_source
    FROM users WHERE id = ?
  `).get(userId) as UserContextPayload['profile'] | undefined

  // Подписка: текущая active/grace; если их нет — последняя по created_at,
  // чтобы хотя бы понимать «когда юзер последний раз платил».
  const subActive = d.prepare(`
    SELECT plan, status, expires_at, grace_until
    FROM subscriptions
    WHERE user_id = ? AND status IN ('active','grace')
    ORDER BY expires_at DESC LIMIT 1
  `).get(userId) as UserContextPayload['subscription'] | undefined

  const subFallback = subActive ? null : d.prepare(`
    SELECT plan, status, expires_at, grace_until
    FROM subscriptions
    WHERE user_id = ?
    ORDER BY created_at DESC LIMIT 1
  `).get(userId) as UserContextPayload['subscription'] | undefined

  const recentPayments = d.prepare(`
    SELECT s.id, s.plan, s.stars_paid, s.amount_rub, s.status, s.refunded_at,
           s.created_at,
           COALESCE(s.payment_provider,
                    CASE
                      WHEN s.payment_id LIKE 'crypto_%'      THEN 'cryptobot'
                      WHEN s.payment_id LIKE 'oxapay_%'      THEN 'oxapay'
                      WHEN s.payment_id LIKE 'lavatop_%'     THEN 'lavatop'
                      WHEN s.payment_id LIKE 'admin_grant_%' THEN 'gift'
                      WHEN s.payment_id LIKE 'trial_%'       THEN 'trial'
                      WHEN s.payment_id LIKE 'free_%'        THEN 'free'
                      ELSE 'stars'
                    END) as method
    FROM subscriptions s
    WHERE s.user_id = ?
    ORDER BY s.created_at DESC LIMIT 3
  `).all(userId) as UserContextPayload['recentPayments']

  const ban = d.prepare(`
    SELECT is_banned, banned_at, banned_reason
    FROM users WHERE id = ?
  `).get(userId) as UserContextPayload['ban'] | undefined

  return {
    profile: profile ?? null,
    subscription: subActive ?? subFallback ?? null,
    recentPayments,
    ban: ban ?? null,
  }
}

// ─── Track F: cohort retention ────────────────────────────────────────
// Weekly cohort retention matrix: rows = signup-week, cols = «недель с
// регистрации». Cell = % cohort с активной/grace платной подпиской в
// эту календарную неделю. Стандартный SaaS-метрик: видно, насколько
// клиенты «застревают» в продукте.
//
// Почему matrix считается в TS, а не одним SQL:
// SQLite не имеет generate_series / window-функций уровня PostgreSQL,
// и сборка 12×12 матрицы через рекурсивный CTE превращается в ad-hoc
// нечитаемое нечто. Объём данных мал (≤ 3 мес users + их subs) — full-scan
// в TS даёт O(users * 12) которое < 1мс на типичной БД. Тестируемо
// и читаемо. Если cohort window вырастет до 52 недель — пересмотреть.
//
// Week boundary: Monday 00:00 UTC. Совпадает с monOffsetExpr из
// revenueWindows() — единый стандарт по проекту (revenue-чарт уже считает
// WTD от понедельника).

export type CohortRow = {
  cohortLabel: string         // e.g. "May 12" — дата понедельника
  cohortWeekStart: string     // ISO date Monday — для дебага/тестов
  cohortSize: number          // сколько юзеров зарегалось в эту неделю
  retention: Array<{
    week: number              // 0 = сама неделя регистрации, 1 = следующая…
    active: number            // сколько cohort'а было активно в эту неделю
    pct: number               // active / cohortSize * 100, округлено до int
  }>
}

/**
 * Возвращает 12 cohort'ов за последние 12 недель (включая текущую), от
 * самой старой к самой новой. Для каждого — массив `retention[0..11]`,
 * где cell[k] = % юзеров cohort'а, у которых была активная (`active` или
 * `grace`) платная подписка хотя бы в один день недели N+k.
 *
 * «Активная подписка в неделю W» = существует sub где
 *   created_at <= week_end AND
 *   (expires_at IS NULL OR expires_at >= week_start) AND
 *   status IN ('active','grace') AND
 *   plan != 'vpn_trial' AND refunded_at IS NULL
 *
 * Триалы не считаем — это не retention платящих, а retention пользователей
 * вообще. Метрика про paid retention.
 *
 * Cells с week > current-week (т.е. в будущем относительно cohort'а)
 * фронт рендерит как «empty» — клиент не дожил ещё до этой клетки.
 * Здесь возвращаем все 12 cells всегда; фронт сам решает по
 * `week > weeks_since_cohort` показывать ли %.
 */
export function cohortRetention12w(): CohortRow[] {
  const d = db()

  const WEEKS = 12
  const MS_PER_DAY = 24 * 60 * 60 * 1000
  const MS_PER_WEEK = 7 * MS_PER_DAY

  // ── Шаг 1: определить 12 границ недель (Monday 00:00 UTC). ──
  // Берём now в UTC, отматываем до ближайшего предыдущего понедельника,
  // и формируем массив из 12 weekStart-таймстампов (от самого старого).
  const now = new Date()
  // Date.getUTCDay(): 0=вс..6=сб → понедельник = (day + 6) % 7 дней назад
  const currentMonday = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(),
  ))
  const dow = currentMonday.getUTCDay()
  const daysToMonday = (dow + 6) % 7  // 0 если уже пн, 6 если вс
  currentMonday.setUTCDate(currentMonday.getUTCDate() - daysToMonday)

  // weekStarts[0] = 11 недель назад (самый старый cohort)
  // weekStarts[11] = текущая неделя
  const weekStarts: Date[] = []
  for (let i = WEEKS - 1; i >= 0; i--) {
    weekStarts.push(new Date(currentMonday.getTime() - i * MS_PER_WEEK))
  }

  // ── Шаг 2: вытянуть всех юзеров, зарегавшихся в окне [weekStarts[0], +12 недель]. ──
  // SQLite хранит created_at в UTC ISO-like формате; сравнение строкой работает
  // для ISO-8601 (датосортируется лексикографически). Используем datetime() для
  // нормализации (CURRENT_TIMESTAMP пишет «YYYY-MM-DD HH:MM:SS» без T).
  const exclU = excludeAdminsClause('id')
  const windowStartISO = weekStarts[0].toISOString().replace('T', ' ').slice(0, 19)

  const users = d.prepare(`
    SELECT id, created_at
    FROM users
    WHERE datetime(REPLACE(created_at, 'T', ' ')) >= datetime(?) ${exclU}
  `).all(windowStartISO) as Array<{ id: number; created_at: string }>

  if (users.length === 0) {
    // Пустые cohort'ы — фронт показывает grid с нулями.
    return weekStarts.map(ws => ({
      cohortLabel: formatCohortLabel(ws),
      cohortWeekStart: ws.toISOString().slice(0, 10),
      cohortSize: 0,
      retention: Array.from({ length: WEEKS }, (_, w) => ({
        week: w,
        active: 0,
        pct: 0,
      })),
    }))
  }

  // ── Шаг 3: распределить юзеров по cohort'ам. ──
  // Для каждого юзера: какой weekStart-индекс соответствует его created_at?
  // Парсим created_at как UTC, делим на MS_PER_WEEK.
  const userIds = users.map(u => u.id)
  // userCohortIdx[user_id] = индекс в weekStarts (или -1 если вне окна)
  const userCohortIdx = new Map<number, number>()
  const cohortUserIds: Set<number>[] = weekStarts.map(() => new Set())

  for (const u of users) {
    const ts = parseSqliteTimestamp(u.created_at)
    if (ts === null) continue
    // Находим индекс недели: ts должен попасть в [weekStarts[idx], weekStarts[idx]+1week)
    const diffMs = ts - weekStarts[0].getTime()
    if (diffMs < 0) continue
    const idx = Math.floor(diffMs / MS_PER_WEEK)
    if (idx < 0 || idx >= WEEKS) continue
    userCohortIdx.set(u.id, idx)
    cohortUserIds[idx].add(u.id)
  }

  // ── Шаг 4: вытянуть подписки этих юзеров. ──
  // IN (...) с inline list — userIds может быть до пары сотен; для большего
  // объёма стоит сделать temp table, но здесь 12 weeks * ~средняя welcome rate
  // даёт малый scope.
  // Стабильность фильтра: status IN ('active','grace'), plan != trial, не возвращены.
  let subs: Array<{
    user_id: number
    created_at: string
    expires_at: string | null
  }> = []

  if (userIds.length > 0) {
    // SQLite limit: 999 параметров. Делим на чанки если надо.
    const CHUNK = 900
    // refunded_at — миграционная колонка (ALTER TABLE). На production уже есть,
    // но в очень старом dev DB её может не быть → query упадёт. Graceful detect.
    const hasRefundedAt = (() => {
      try {
        d.prepare(`SELECT refunded_at FROM subscriptions LIMIT 0`).all()
        return true
      } catch { return false }
    })()
    const refundFilter = hasRefundedAt
      ? "AND (refunded_at IS NULL OR refunded_at = '')"
      : ''
    for (let i = 0; i < userIds.length; i += CHUNK) {
      const chunk = userIds.slice(i, i + CHUNK)
      const placeholders = chunk.map(() => '?').join(',')
      const rows = d.prepare(`
        SELECT user_id, created_at, expires_at
        FROM subscriptions
        WHERE user_id IN (${placeholders})
          AND plan != 'vpn_trial'
          AND status IN ('active','grace')
          ${refundFilter}
      `).all(...chunk) as Array<{
        user_id: number
        created_at: string
        expires_at: string | null
      }>
      subs = subs.concat(rows)
    }
  }

  // ── Шаг 5: для каждого cohort'а пройтись по 12 «неделям с регистрации». ──
  // Для cohort idx и retention week w: рассматриваемая calendar-week W =
  // weekStarts[idx + w] (если idx + w < WEEKS) либо weekStarts[idx] + w*WEEK
  // (но мы не показываем будущее, т.к. WeekStart > now). w=0 всегда week
  // регистрации, в которой по определению юзер «активен» только если уже
  // купил sub до конца недели.
  //
  // Чтобы избежать O(N_subs * N_weeks) — для каждой подписки за один проход
  // получим (firstWeekActive, lastWeekActive) индексы в weekStarts; и для
  // каждого user'а соберём Set активных week-индексов.
  const userActiveWeeks = new Map<number, Set<number>>()
  for (const s of subs) {
    const subStartTs = parseSqliteTimestamp(s.created_at)
    if (subStartTs === null) continue
    // expires_at может быть null (бессрочка) — тогда «активна до сейчас».
    const expTs = s.expires_at ? parseSqliteTimestamp(s.expires_at) : null
    const subEndTs = expTs ?? Date.now()

    // Какие weekStarts[k] пересекаются с [subStartTs, subEndTs]?
    // week k активна если subStartTs < weekEnd AND subEndTs > weekStart.
    for (let k = 0; k < WEEKS; k++) {
      const wStart = weekStarts[k].getTime()
      const wEnd = wStart + MS_PER_WEEK
      if (subStartTs < wEnd && subEndTs >= wStart) {
        let set = userActiveWeeks.get(s.user_id)
        if (!set) { set = new Set(); userActiveWeeks.set(s.user_id, set) }
        set.add(k)
      }
    }
  }

  // ── Шаг 6: посчитать матрицу. ──
  const result: CohortRow[] = weekStarts.map((ws, idx) => {
    const cohortIds = cohortUserIds[idx]
    const size = cohortIds.size
    const retention: CohortRow['retention'] = []

    for (let w = 0; w < WEEKS; w++) {
      const calendarIdx = idx + w
      if (calendarIdx >= WEEKS) {
        // Эта клетка в будущем относительно cohort'а — фронт нарисует empty.
        retention.push({ week: w, active: 0, pct: 0 })
        continue
      }
      let active = 0
      for (const uid of cohortIds) {
        const aw = userActiveWeeks.get(uid)
        if (aw && aw.has(calendarIdx)) active++
      }
      const pct = size > 0 ? Math.round((active / size) * 100) : 0
      retention.push({ week: w, active, pct })
    }

    return {
      cohortLabel: formatCohortLabel(ws),
      cohortWeekStart: ws.toISOString().slice(0, 10),
      cohortSize: size,
      retention,
    }
  })

  return result
}

/**
 * Парсит timestamp из SQLite в epoch ms.
 *
 * SQLite хранит два формата: «YYYY-MM-DD HH:MM:SS» (CURRENT_TIMESTAMP)
 * и ISO-8601 «YYYY-MM-DDTHH:MM:SS» (Python datetime.isoformat()). Оба
 * считаем UTC (бот так и пишет — naive datetime в UTC).
 *
 * Возвращает null если строка пустая или unparsable — caller просто
 * пропустит запись.
 */
function parseSqliteTimestamp(s: string | null): number | null {
  if (!s) return null
  // Если уже ISO (с T) — Date умеет. Если «YYYY-MM-DD HH:MM:SS» —
  // явно прибавим 'Z' (UTC), иначе JS интерпретирует как local time.
  const iso = s.includes('T') ? s : s.replace(' ', 'T')
  const withTz = iso.endsWith('Z') ? iso : iso + 'Z'
  const ms = Date.parse(withTz)
  return Number.isFinite(ms) ? ms : null
}

/** "May 12" — короткий ярлык для cohort row. Локаль en-US, фиксированная UTC. */
function formatCohortLabel(d: Date): string {
  const month = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
  const day = d.getUTCDate()
  return `${month} ${day}`
}
