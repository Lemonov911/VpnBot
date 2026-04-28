import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { searchUsers, userFull } from '@/lib/db'

export async function GET(req: NextRequest) {
  if (!await requireSession()) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { searchParams } = req.nextUrl
  const q  = searchParams.get('q')
  const id = searchParams.get('id')

  if (id) return NextResponse.json(userFull(parseInt(id)))
  if (q)  return NextResponse.json(searchUsers(q))
  return NextResponse.json([])
}
