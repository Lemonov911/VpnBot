import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/** POST /api/servers/[id]/migrate — мигрирует конфиги с мёртвого сервера.
 *  AWG: re-provision на лучшем доступном + уведомление юзерам.
 *  VLESS: сбрасывает мёртвые записи (multi-location копии на других серверах живы). */
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const { id } = await ctx.params
  const numId = parseInt(id, 10)
  if (!Number.isFinite(numId)) return NextResponse.json({ error: 'invalid id' }, { status: 400 })

  // admin_id из session — для audit_log forensics.
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/servers/${numId}/migrate-configs`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: JSON.stringify({ admin_id: session.userId }),
    signal: AbortSignal.timeout(300_000), // миграция может занять минуты (mirror backfill-vless)
  })
  const data = await upstream.json().catch(() => ({}))
  return NextResponse.json(data, { status: upstream.status })
}
