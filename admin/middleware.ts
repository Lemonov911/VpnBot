import { NextRequest, NextResponse } from 'next/server'
import { jwtVerify } from 'jose'

const JWT_SECRET  = new TextEncoder().encode(process.env.JWT_SECRET ?? 'change-me-in-production')
const ADMIN_IDS   = (process.env.ADMIN_IDS ?? process.env.ADMIN_ID ?? '').split(',').map(s => parseInt(s.trim())).filter(Boolean)
const PUBLIC      = ['/login', '/api/auth']

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl
  if (PUBLIC.some(p => pathname.startsWith(p))) return NextResponse.next()

  const token = req.cookies.get('admin_token')?.value
  if (!token) return NextResponse.redirect(new URL('/login', req.url))

  try {
    const { payload } = await jwtVerify(token, JWT_SECRET)
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
