import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { revalidatePath } from 'next/cache'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/** POST /api/user/[id]/unban — снимает бан. */
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  if (!ADMIN_API_SECRET) {
    return NextResponse.json({ error: 'ADMIN_API_SECRET not configured' }, { status: 503 })
  }

  const { id } = await ctx.params
  const upstream = await fetch(`${BOT_API_BASE}/api/admin/user/${id}/unban`, {
    method: 'POST',
    headers: { 'X-Admin-Secret': ADMIN_API_SECRET, 'Content-Type': 'application/json' },
  })
  const data = await upstream.json().catch(() => ({}))
  revalidatePath(`/clients/${id}`)
  revalidatePath('/clients')
  return NextResponse.json(data, { status: upstream.status })
}
