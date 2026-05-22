import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/** POST /api/servers/[id]/backfill-vless — провижит существующие multi-location
 *  VLESS-слоты на этот сервер (для нового сервера или при включении из drain).
 *  Идемпотентна, проксирует на бот. */
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const { id } = await ctx.params
  const numId = parseInt(id, 10)
  if (!Number.isFinite(numId)) return NextResponse.json({ error: 'invalid id' }, { status: 400 })

  // Может занять минуты при большом числе подписок: provision_peer на агент
  // ~200-500ms × N слотов. Даём 5 минут — если backfill дольше, что-то не так.
  // admin_id из session — для audit_log forensics.
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/servers/${numId}/backfill-vless`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: JSON.stringify({ admin_id: session.userId }),
    signal: AbortSignal.timeout(300_000),
  })
  const data = await upstream.json().catch(() => ({}))
  return NextResponse.json(data, { status: upstream.status })
}
