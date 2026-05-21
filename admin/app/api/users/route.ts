import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { searchUsers, userFull } from '@/lib/db'

export async function GET(req: NextRequest) {
  if (!await requireSession()) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { searchParams } = req.nextUrl
  const q  = searchParams.get('q')
  const id = searchParams.get('id')

  if (id) {
    const numId = parseInt(id, 10)
    if (!Number.isFinite(numId)) return NextResponse.json({ error: 'Invalid id' }, { status: 400 })
    return NextResponse.json(userFull(numId))
  }
  if (q)  return NextResponse.json(searchUsers(q))
  return NextResponse.json([])
}
