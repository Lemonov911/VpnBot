import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/** POST /api/tickets/[id]/close — закрыть тикет БЕЗ отправки сообщения. */
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json(
      { error: 'ADMIN_API_SECRET not configured on admin server' },
      { status: 503 }
    )
  }

  const { id } = await ctx.params
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/tickets/${id}/close`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET },
  })
  const data = await upstream.json().catch(() => ({}))
  return NextResponse.json(data, { status: upstream.status })
}
