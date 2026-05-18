import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import Database from 'better-sqlite3'
import path from 'path'
import crypto from 'crypto'

// Абсолютный path через env — Next.js standalone делает process.chdir() на свою
// папку, относительный path резолвится в /opt/vpnbot/admin/.next/standalone/...
// что не существует. См. также admin/lib/db.ts:DB_PATH.
const DB_PATH = process.env.BOT_DB_PATH
  ?? path.resolve(process.cwd(), '../bot/bot.db')

function writeDb() {
  return new Database(DB_PATH)
}

/**
 * HMAC-подпись для /health probe нового агента. Бот шлёт `X-Agent-Sig: <ts>.<hex>`
 * где hex = HMAC_SHA256(token, ts + ":" + method + path + ":" + body).
 * Legacy `X-Agent-Token` больше не поддерживается агентом (sec audit C1).
 */
function buildAgentSig(token: string, method: string, path: string, body = ''): string {
  const ts = Math.floor(Date.now() / 1000).toString()
  const msg = `${ts}:${method}${path}:${body}`
  const sig = crypto.createHmac('sha256', token).update(msg).digest('hex')
  return `${ts}.${sig}`
}

// GET /api/servers — list
export async function GET() {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const db = writeDb()
  // `status` колонки в servers нет (не в migration). is_active — главный флаг.
  const servers = db.prepare(`
    SELECT id, name, flag, city, host, agent_url, protocol,
           capacity, active_peers, is_active, wg_pubkey, created_at
    FROM servers ORDER BY created_at DESC
  `).all()
  db.close()
  return NextResponse.json(servers)
}

// POST /api/servers — add server (verifies agent health first via HMAC)
export async function POST(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json()
  const { name, flag, city, host, agent_url, agent_token, protocol, capacity } = body

  if (!name || !host || !agent_url || !agent_token || !protocol) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  // SSRF-защита: принимаем только http/https схемы.
  // Без этого авторизованный admin мог передать file:// или http://169.254.169.254/...
  try {
    const parsed = new URL(agent_url)
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      return NextResponse.json({ error: 'Invalid agent_url: only http/https allowed' }, { status: 400 })
    }
  } catch {
    return NextResponse.json({ error: 'Invalid agent_url: not a valid URL' }, { status: 400 })
  }

  // Verify agent is reachable. /health не требует auth, но всё равно подписываем —
  // если кто-то выставит /health за auth-wall, не сломаемся.
  let wg_pubkey = ''
  try {
    const sig = buildAgentSig(agent_token, 'GET', '/health')
    const res = await fetch(`${agent_url}/health`, {
      headers: { 'X-Agent-Sig': sig },
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) throw new Error(`Agent returned ${res.status}`)
    const data = await res.json() as { server_key?: string }
    wg_pubkey = data.server_key ?? ''
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return NextResponse.json({ error: `Cannot reach agent: ${msg}` }, { status: 422 })
  }

  const db = writeDb()
  // `status` колонки нет — убрана из INSERT.
  const result = db.prepare(`
    INSERT INTO servers (name, flag, city, host, user, agent_url, agent_token,
                         wg_pubkey, protocol, capacity, is_active)
    VALUES (?, ?, ?, ?, 'root', ?, ?, ?, ?, ?, 1)
  `).run(name, flag || '🌍', city || '', host, agent_url, agent_token,
         wg_pubkey, protocol, capacity || 100)
  db.close()

  return NextResponse.json({ id: result.lastInsertRowid, wg_pubkey })
}

// DELETE /api/servers?id=N — soft-disable (drain), keeps row in DB.
// Existing peers continue serving traffic via agent until natural expiry;
// new provisioning skips this server (get_best_server filters is_active=1).
export async function DELETE(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const id = new URL(req.url).searchParams.get('id')
  if (!id) return NextResponse.json({ error: 'No id' }, { status: 400 })

  const db = writeDb()
  db.prepare('UPDATE servers SET is_active=0 WHERE id=?').run(id)
  db.close()
  return NextResponse.json({ ok: true })
}

// PATCH /api/servers?id=N
// Body: { "is_active": 1 } — re-enable previously drained server.
//       { "capacity": 200 } — change soft cap (load-balancer uses
//         active_peers/capacity ratio; raising capacity makes this server
//         preferred until peers catch up).
export async function PATCH(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const id = new URL(req.url).searchParams.get('id')
  if (!id) return NextResponse.json({ error: 'No id' }, { status: 400 })

  let body: { is_active?: number; capacity?: number } = {}
  try { body = await req.json() } catch {}

  const sets: string[] = []
  const args: unknown[] = []
  if (body.is_active === 0 || body.is_active === 1) {
    sets.push('is_active=?')
    args.push(body.is_active)
  }
  if (typeof body.capacity === 'number' && body.capacity >= 1 && body.capacity <= 10000) {
    sets.push('capacity=?')
    args.push(Math.round(body.capacity))
  }
  if (sets.length === 0) {
    return NextResponse.json({ error: 'nothing to update' }, { status: 400 })
  }

  args.push(id)
  const db = writeDb()
  const result = db.prepare(`UPDATE servers SET ${sets.join(', ')} WHERE id=?`).run(...args)
  db.close()
  if (result.changes === 0) {
    return NextResponse.json({ error: 'server not found' }, { status: 404 })
  }
  return NextResponse.json({ ok: true })
}
