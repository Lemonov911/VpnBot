import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { revalidatePath } from 'next/cache'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/**
 * POST /api/sub/[id]/mark-refunded
 *
 * Local-mark fallback: помечает подписку refunded БЕЗ обращения к
 * Telegram/CryptoBot/Lava. Используется когда внешний refund провалился
 * (например Stars charge старше 21 дня → Telegram отказывает в refund_star_payment).
 *
 * Не путать с /api/sub/[id]/refund — тот пытается через провайдера, и
 * 502 от него — триггер для перехода на этот endpoint в UI.
 *
 * Auth: JWT session (общий для админки) + ADMIN_API_SECRET к боту (X-Admin-Secret).
 */
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const { id } = await ctx.params
  const numId = parseInt(id, 10)
  if (!Number.isFinite(numId)) return NextResponse.json({ error: 'invalid id' }, { status: 400 })

  let body: Record<string, unknown> = {}
  try { body = (await req.json()) as Record<string, unknown> } catch {}
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/sub/${numId}/mark-refunded`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, admin_id: session.userId }),
    signal: AbortSignal.timeout(15_000),
  })
  const data: Record<string, unknown> = await upstream.json().catch(() => ({}))
  if (upstream.ok) {
    revalidatePath('/payments')
    revalidatePath(`/payments/${numId}`)
    revalidatePath('/clients')
    const uid = data?.user_id
    if (typeof uid === 'number' && Number.isFinite(uid)) {
      revalidatePath(`/clients/${uid}`)
    }
  }
  return NextResponse.json(data, { status: upstream.status })
}
