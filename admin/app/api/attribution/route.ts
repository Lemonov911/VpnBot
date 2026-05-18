import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { attributionByPeriod, recentUtmCodes } from '@/lib/db'

/**
 * GET /api/attribution?days=30
 *
 * `days`: 7 | 30 | 90 | 0 (0 = всё время). Дефолт 30.
 *
 * Возвращает:
 *   rows         — таблица источников (group by traffic_source)
 *   recent_utm   — последние UTM-коды для re-use в генераторе ссылок
 *   bot_username — для генерации t.me/<bot>?start=... ссылок
 */
export async function GET(req: NextRequest) {
  const session = await requireSession()
  if (!session) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const rawDays = req.nextUrl.searchParams.get('days')
  // 0 → null (вся история), иначе clamp 1..3650.
  let days: number | null = 30
  if (rawDays !== null) {
    const n = parseInt(rawDays, 10)
    if (Number.isFinite(n)) {
      days = n <= 0 ? null : Math.max(1, Math.min(3650, n))
    }
  }

  const rows = attributionByPeriod(days)
  const recent = recentUtmCodes(20)
  const bot = process.env.BOT_USERNAME ?? ''

  return NextResponse.json({
    days,
    rows,
    recent_utm: recent,
    bot_username: bot,
  })
}
