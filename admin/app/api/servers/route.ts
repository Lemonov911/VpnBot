import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import Database from 'better-sqlite3'
import path from 'path'

const DB_PATH = path.resolve(process.cwd(), '../bot/bot.db')

function writeDb() {
  return new Database(DB_PATH)
}

// GET /api/servers — list
export async function GET() {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const db = writeDb()
  const servers = db.prepare(`
    SELECT id, name, flag, city, host, agent_url, protocol,
           capacity, active_peers, status, is_active, wg_pubkey, created_at
    FROM servers ORDER BY created_at DESC
  `).all()
  db.close()
  return NextResponse.json(servers)
}

// POST /api/servers — add server (verifies agent health first)
export async function POST(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const body = await req.json()
  const { name, flag, city, host, agent_url, agent_token, protocol, capacity } = body

  if (!name || !host || !agent_url || !agent_token || !protocol) {
    return NextResponse.json({ error: 'Missing required fields' }, { status: 400 })
  }

  // Verify agent is reachable and get wg_pubkey
  let wg_pubkey = ''
  try {
    const res = await fetch(`${agent_url}/health`, {
      headers: { 'X-Agent-Token': agent_token },
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) throw new Error(`Agent returned ${res.status}`)
    const data = await res.json() as any
    wg_pubkey = data.server_key ?? ''
  } catch (e: any) {
    return NextResponse.json({ error: `Cannot reach agent: ${e.message}` }, { status: 422 })
  }

  const db = writeDb()
  const result = db.prepare(`
    INSERT INTO servers (name, flag, city, host, user, agent_url, agent_token,
                         wg_pubkey, protocol, capacity, is_active, status)
    VALUES (?, ?, ?, ?, 'root', ?, ?, ?, ?, ?, 1, 'online')
  `).run(name, flag || '🌍', city || '', host, agent_url, agent_token,
         wg_pubkey, protocol, capacity || 100)
  db.close()

  return NextResponse.json({ id: result.lastInsertRowid, wg_pubkey })
}

// DELETE /api/servers/[id]
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
