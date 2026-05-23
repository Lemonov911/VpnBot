import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { slaBreachCount } from '@/lib/db'

/**
 * GET /api/tickets/sla — { count: N } — число тикетов без ответа админа >2ч.
 *
 * Используется AdminNav (client component) для рендера badge на страницах, где
 * caller не передал prop slaBreaches вручную (track A/B страницы — их
 * page.tsx был вне scope track-C задачи). Read-only, без побочных эффектов.
 */
export async function GET() {
  if (!await requireSession()) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  return NextResponse.json({ count: slaBreachCount() })
}
