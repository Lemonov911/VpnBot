import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import {
  analyticsSummary,
  dailyRevenueLast30,
  planMix30d,
  trialFunnel30d,
  topReferrers,
} from '@/lib/db'

export async function GET() {
  if (!await requireSession()) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  return NextResponse.json({
    summary:       analyticsSummary(),
    daily_revenue: dailyRevenueLast30(),
    plan_mix:      planMix30d(),
    funnel:        trialFunnel30d(),
    top_referrers: topReferrers(10),
  })
}
