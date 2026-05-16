import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { revalidatePath } from 'next/cache'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/** POST /api/sub/[id]/refund — пометить подписку refunded, опц. вернуть Stars. */
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const { id } = await ctx.params
  let body: unknown = {}
  try { body = await req.json() } catch {}
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/sub/${id}/refund`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await upstream.json().catch(() => ({}))
  // Не знаем user_id из body — пере-валидируем общие списки.
  revalidatePath('/payments')
  revalidatePath('/clients')
  return NextResponse.json(data, { status: upstream.status })
}
