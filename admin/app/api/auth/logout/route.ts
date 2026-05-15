import { NextResponse } from 'next/server'
import { COOKIE_NAME } from '@/lib/auth'

/**
 * GET /api/auth/logout — чистит admin_token cookie и редиректит на /login.
 *
 * GET (не POST) — чтобы можно было привязать к обычной <a href> в nav-баре
 * без JS-fetch'а. POST/DELETE остался в /api/auth для programmatic вызовов.
 */
export async function GET(request: Request) {
  // basePath учитывается, но Next.js на 15-й мажор автоматически
  // подставляет его в res.cookies.delete и в headers location. Сходим
  // на /login через URL объект (правильно учтёт хост и basePath).
  const url = new URL(request.url)
  url.pathname = '/login'
  url.search = ''  // чистим query (на всякий)

  const res = NextResponse.redirect(url, 303)  // 303 — see-other, не кешируется
  res.cookies.set(COOKIE_NAME, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 0,  // expire immediately
    path: '/',
  })
  return res
}
