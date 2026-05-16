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
  // Может занять минуты при большом числе подписок: provision_peer на агент
  // ~200-500ms × N слотов. Bot-side таймаута нет, держим клиента открытым.
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/servers/${id}/backfill-vless`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: '{}',
  })
  const data = await upstream.json().catch(() => ({}))
  return NextResponse.json(data, { status: upstream.status })
}
