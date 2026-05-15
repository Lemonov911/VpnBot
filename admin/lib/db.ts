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
    _db.pragma('journal_mode = WAL')
  }
  return _db
}

export function stats() {
  const d = db()
  const users        = (d.prepare('SELECT COUNT(*) as n FROM users').get() as any).n
  const activeSubs   = (d.prepare("SELECT COUNT(*) as n FROM subscriptions WHERE status='active'").get() as any).n
  const totalStars   = (d.prepare("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE status IN ('active','expired')").get() as any).n
  const openTickets  = (d.prepare("SELECT COUNT(*) as n FROM support_tickets WHERE status='open'").get() as any).n
  return { users, activeSubs, totalStars, openTickets }
}

export function recentPayments(limit = 20) {
  return db().prepare(`
    SELECT s.id, s.plan, s.stars_paid, s.payment_id, s.status,
           s.created_at, s.expires_at,
           u.username, u.first_name
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    ORDER BY s.created_at DESC LIMIT ?
  `).all(limit)
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

export function userFull(userId: number) {
  const d = db()
  const user = d.prepare('SELECT * FROM users WHERE id = ?').get(userId)
  const subs = d.prepare('SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC').all(userId)
  const tickets = d.prepare('SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT 5').all(userId)
  return { user, subs, tickets }
}

export function allServers() {
  return db().prepare(`
    SELECT id, name, flag, city, host, agent_url, protocol,
           capacity, active_peers, status, is_active, created_at,
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
  return {
    users_total:          r('SELECT COUNT(*) as n FROM users'),
    users_30d:            r("SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-30 days')"),
    users_7d:             r("SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-7 days')"),
    subs_active:          r("SELECT COUNT(*) as n FROM subscriptions WHERE status='active'"),
    subs_paid_30d:        r("SELECT COUNT(*) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-30 days')"),
    subs_trial_30d:       r("SELECT COUNT(*) as n FROM subscriptions WHERE plan='vpn_trial' AND created_at > datetime('now','-30 days')"),
    revenue_stars_30d:    r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-30 days')"),
    revenue_stars_7d:     r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-7 days')"),
    expired_30d:          r("SELECT COUNT(*) as n FROM subscriptions WHERE status='expired' AND expires_at > datetime('now','-30 days')"),
  }
}

export function dailyRevenueLast30() {
  return db().prepare(`
    SELECT date(created_at) as day,
           COUNT(*)                    as paid_subs,
           COALESCE(SUM(stars_paid),0) as stars
    FROM subscriptions
    WHERE plan != 'vpn_trial'
      AND created_at > datetime('now','-30 days')
    GROUP BY day
    ORDER BY day ASC
  `).all() as Array<{ day: string; paid_subs: number; stars: number }>
}

export function planMix30d() {
  return db().prepare(`
    SELECT plan,
           COUNT(*) as count,
           COALESCE(SUM(stars_paid),0) as stars
    FROM subscriptions
    WHERE created_at > datetime('now','-30 days')
    GROUP BY plan
    ORDER BY count DESC
  `).all() as Array<{ plan: string; count: number; stars: number }>
}

export function trialFunnel30d() {
  // Воронка: сколько юзеров пришло → взяли триал → купили платный после триала.
  const d = db()
  const new_users = (d.prepare(
    "SELECT COUNT(*) as n FROM users WHERE created_at > datetime('now','-30 days')"
  ).get() as { n: number }).n

  const trial_users = (d.prepare(
    `SELECT COUNT(DISTINCT user_id) as n FROM subscriptions
     WHERE plan='vpn_trial' AND created_at > datetime('now','-30 days')`
  ).get() as { n: number }).n

  // Юзеры, у которых был триал И ПОТОМ платная подписка
  const trial_then_paid = (d.prepare(`
    SELECT COUNT(DISTINCT t.user_id) as n FROM subscriptions t
    JOIN subscriptions p ON p.user_id = t.user_id
                         AND p.plan != 'vpn_trial'
                         AND p.created_at > t.created_at
    WHERE t.plan = 'vpn_trial'
      AND t.created_at > datetime('now','-30 days')
  `).get() as { n: number }).n

  // Платные юзеры, у которых триала не было
  const direct_paid = (d.prepare(`
    SELECT COUNT(DISTINCT user_id) as n FROM subscriptions s
    WHERE s.plan != 'vpn_trial'
      AND s.created_at > datetime('now','-30 days')
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
  // Триалы исключены — это не выручка.
  return db().prepare(`
    SELECT u.id, u.username, u.first_name, u.created_at as joined_at,
           COUNT(s.id) FILTER (WHERE s.plan != 'vpn_trial')                       as paid_subs,
           COUNT(s.id) FILTER (WHERE s.plan = 'vpn_trial')                        as trial_subs,
           COALESCE(SUM(CASE WHEN s.plan != 'vpn_trial' THEN s.stars_paid END), 0) as total_stars,
           MAX(CASE WHEN s.status = 'active' THEN s.plan END)                     as current_plan,
           MAX(s.created_at)                                                       as last_purchase,
           MAX(CASE WHEN s.status = 'active' THEN s.expires_at END)               as active_until
    FROM users u
    LEFT JOIN subscriptions s ON s.user_id = u.id
    GROUP BY u.id
    HAVING total_stars > 0
    ORDER BY total_stars DESC, last_purchase DESC
    LIMIT ?
  `).all(limit) as Array<{
    id: number
    username: string | null
    first_name: string | null
    joined_at: string
    paid_subs: number
    trial_subs: number
    total_stars: number
    current_plan: string | null
    last_purchase: string | null
    active_until: string | null
  }>
}

export function moneyTotals() {
  const d = db()
  const r = (sql: string) => (d.prepare(sql).get() as { n: number }).n
  return {
    total_revenue_stars: r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial'"),
    paying_users:        r("SELECT COUNT(DISTINCT user_id) as n FROM subscriptions WHERE plan!='vpn_trial' AND stars_paid > 0"),
    avg_revenue_per_payer: 0, // computed in route from above
    avg_ltv_stars:       0, // same
    revenue_7d:          r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-7 days')"),
    revenue_30d:         r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-30 days')"),
    revenue_90d:         r("SELECT COALESCE(SUM(stars_paid),0) as n FROM subscriptions WHERE plan!='vpn_trial' AND created_at > datetime('now','-90 days')"),
    repeat_buyers:       r(`SELECT COUNT(*) as n FROM (
      SELECT user_id FROM subscriptions
      WHERE plan!='vpn_trial' AND stars_paid > 0
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
  const servers = d.prepare(`
    SELECT s.id, s.name, s.flag, s.city, s.host, s.protocol,
           s.active_peers, s.capacity, s.is_active, s.status, s.agent_url, s.created_at,
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
    is_active: number; status: string | null; agent_url: string | null;
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
    expiring_3d:        r("SELECT COUNT(*) as n FROM subscriptions WHERE status='active' AND expires_at <= datetime('now','+3 days')"),
    expiring_1d:        r("SELECT COUNT(*) as n FROM subscriptions WHERE status='active' AND expires_at <= datetime('now','+1 day')"),
  }
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
  return db().prepare(`
    SELECT u.id, u.username, u.first_name,
           COUNT(r.id) as invited,
           COALESCE(SUM(CASE WHEN s.plan != 'vpn_trial' THEN 1 ELSE 0 END), 0) as invited_paid
    FROM users u
    JOIN users r       ON r.referred_by = u.id
    LEFT JOIN subscriptions s ON s.user_id = r.id
    WHERE u.id IN (SELECT DISTINCT referred_by FROM users WHERE referred_by IS NOT NULL)
    GROUP BY u.id
    ORDER BY invited_paid DESC, invited DESC
    LIMIT ?
  `).all(limit) as Array<{ id: number; username: string | null; first_name: string | null; invited: number; invited_paid: number }>
}
