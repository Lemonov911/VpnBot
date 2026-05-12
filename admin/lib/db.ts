import Database from 'better-sqlite3'
import path from 'path'

const DB_PATH = path.resolve(process.cwd(), '../bot/bot.db')

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
