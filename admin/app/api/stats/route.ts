import { NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { stats, recentPayments } from '@/lib/db'

export async function GET() {
  if (!await requireSession()) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  return NextResponse.json({ stats: stats(), payments: recentPayments(10) })
}
