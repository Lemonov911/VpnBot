import { NextRequest, NextResponse } from 'next/server'
import { jwtVerify } from 'jose'

// Lazy чтобы build-time page-data collection не падал когда env недоступны.
function getJwtSecret(): Uint8Array {
  const s = process.env.JWT_SECRET
  if (!s) throw new Error('JWT_SECRET env var is required')
  return new TextEncoder().encode(s)
}
const ADMIN_IDS = (process.env.ADMIN_IDS ?? process.env.ADMIN_ID ?? '').split(',').map(s => parseInt(s.trim())).filter(Boolean)
const PUBLIC      = ['/login', '/api/auth']

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl
  if (PUBLIC.some(p => pathname.startsWith(p))) return NextResponse.next()

  const token = req.cookies.get('admin_token')?.value
  if (!token) return NextResponse.redirect(new URL('/login', req.url))

  try {
    const { payload } = await jwtVerify(token, getJwtSecret())
    const userId = payload.userId as number
    if (!ADMIN_IDS.includes(userId)) {
      return NextResponse.redirect(new URL('/login?error=forbidden', req.url))
    }
    return NextResponse.next()
  } catch {
    return NextResponse.redirect(new URL('/login', req.url))
  }
}

export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
