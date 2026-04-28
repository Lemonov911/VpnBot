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
