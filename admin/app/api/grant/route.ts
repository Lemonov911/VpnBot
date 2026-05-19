import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { revalidatePath } from 'next/cache'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/**
 * POST /api/grant — выдать бесплатную VPN-подписку любому tg-юзеру по ID.
 * Тело: { target_telegram_id, plan_key, days, reason?, target_username? }
 * admin_id берётся из session (telegram_id залогиненного админа) — клиент не
 * подделает чужой ID даже при утечке ADMIN_API_SECRET.
 */
export async function POST(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  let body: Record<string, unknown> = {}
  try { body = await req.json() } catch {}

  // Прокидываем admin_id из session, не из body.
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/grant_subscription`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, admin_id: session.userId }),
  })
  const data = await upstream.json().catch(() => ({}))

  // Обновляем грид grants + страницу клиента (если он в /clients).
  if (upstream.ok) {
    revalidatePath('/grant')
    revalidatePath('/payments')
    if (typeof body.target_telegram_id === 'number') {
      revalidatePath(`/clients/${body.target_telegram_id}`)
    }
  }
  return NextResponse.json(data, { status: upstream.status })
}

/** GET /api/grant?limit=50 — список последних grant'ов для отображения в UI. */
export async function GET(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const limit = req.nextUrl.searchParams.get('limit') ?? '50'
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/grants?limit=${encodeURIComponent(limit)}`, {
    method: 'GET',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET },
    cache: 'no-store',
  })
  const data = await upstream.json().catch(() => ({ grants: [] }))
  return NextResponse.json(data, { status: upstream.status })
}
