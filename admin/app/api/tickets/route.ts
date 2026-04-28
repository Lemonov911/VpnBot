import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { allTickets } from '@/lib/db'

export async function GET(req: NextRequest) {
  if (!await requireSession()) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  const status = req.nextUrl.searchParams.get('status') ?? 'open'
  return NextResponse.json(allTickets(status))
}
