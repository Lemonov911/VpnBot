import { NextRequest, NextResponse } from 'next/server'
import { verifyTelegramAuth, isAdmin, createSession, COOKIE_NAME } from '@/lib/auth'

export async function POST(req: NextRequest) {
  const data = await req.json()

  if (!verifyTelegramAuth(data)) {
    return NextResponse.json({ error: 'Invalid auth data' }, { status: 401 })
  }

  const userId = parseInt(data.id)
  if (!isAdmin(userId)) {
    return NextResponse.json({ error: 'Access denied' }, { status: 403 })
  }

  const token = await createSession(userId, data.username ?? data.first_name)

  const res = NextResponse.json({ ok: true })
  res.cookies.set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 7, // 7 дней
    path: '/',
  })
  return res
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true })
  res.cookies.delete(COOKIE_NAME)
  return res
}
