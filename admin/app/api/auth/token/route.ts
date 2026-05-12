import { NextRequest, NextResponse } from 'next/server'
import { createHmac } from 'crypto'
import { isAdmin, createSession, COOKIE_NAME } from '@/lib/auth'

const BOT_TOKEN = process.env.BOT_TOKEN!

/** Верифицирует одноразовый токен, выданный ботом */
function verifyBotToken(token: string): { userId: number; username: string } | null {
  try {
    const decoded = Buffer.from(token, 'base64url').toString()
    const [payload64, sig] = decoded.split('.')
    if (!payload64 || !sig) return null

    const expected = createHmac('sha256', BOT_TOKEN).update(payload64).digest('hex')
    if (expected !== sig) return null

    const { userId, username, exp } = JSON.parse(Buffer.from(payload64, 'base64').toString())
    if (Date.now() / 1000 > exp) return null

    return { userId, username }
  } catch {
    return null
  }
}

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get('t')
  if (!token) return NextResponse.json({ error: 'No token' }, { status: 400 })

  const payload = verifyBotToken(token)
  if (!payload) return NextResponse.json({ error: 'Invalid or expired token' }, { status: 401 })

  if (!isAdmin(payload.userId)) return NextResponse.json({ error: 'Access denied' }, { status: 403 })

  const session = await createSession(payload.userId, payload.username)
  const host = req.headers.get('x-forwarded-host') ?? req.headers.get('host') ?? 'maxvpnesim.com'
  const proto = req.headers.get('x-forwarded-proto') ?? 'https'
  const res = NextResponse.redirect(`${proto}://${host}/admin`)
  res.cookies.set(COOKIE_NAME, session, {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 7,
    path: '/',
  })
  return res
}
