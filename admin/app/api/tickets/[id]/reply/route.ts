import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'

const BOT_API_BASE     = process.env.BOT_API_BASE     ?? 'http://127.0.0.1:8080'
const ADMIN_API_SECRET = process.env.ADMIN_API_SECRET ?? ''

/**
 * POST /api/tickets/[id]/reply
 * Body: { text: string, close?: boolean }
 *
 * Прокси к боту: bot имеет полномочия слать сообщения юзеру и менять статус
 * тикета. Админка не пишет напрямую в SQLite (read-only) — все мутации идут
 * через bot REST с shared-secret.
 */
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  if (!ADMIN_API_SECRET) {
    return NextResponse.json(
      { error: 'ADMIN_API_SECRET not configured on admin server' },
      { status: 503 }
    )
  }

  const { id } = await ctx.params
  let body: { text?: string; close?: boolean }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'bad json' }, { status: 400 })
  }

  const text = (body.text ?? '').trim()
  if (!text) return NextResponse.json({ error: 'text required' }, { status: 400 })

  const upstream = await fetch(`${BOT_API_BASE}/api/admin/tickets/${id}/reply`, {
    method: 'POST',
    headers: {
      'Content-Type':    'application/json',
      'X-Admin-Secret':  ADMIN_API_SECRET,
    },
    body: JSON.stringify({ text, close: body.close ?? true }),
  })
  const data = await upstream.json().catch(() => ({}))
  return NextResponse.json(data, { status: upstream.status })
}
