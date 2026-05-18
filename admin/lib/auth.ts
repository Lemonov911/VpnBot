import { createHash, createHmac, timingSafeEqual } from 'crypto'
import { SignJWT, jwtVerify } from 'jose'
import { cookies } from 'next/headers'
import { NextRequest } from 'next/server'

const BOT_TOKEN   = process.env.BOT_TOKEN!

// Sec audit M2 (15.05): hard-fail если JWT_SECRET не задан — раньше был
// fallback 'change-me-in-production' который позволял attacker'у minted'ить
// admin JWTs. Проверка lazy (на использование, не на импорт), чтобы build-time
// page-data collection не падал когда env-vars недоступны (next build пытается
// «прогреть» все routes).
function getJwtSecret(): Uint8Array {
  const s = process.env.JWT_SECRET
  if (!s) throw new Error('JWT_SECRET env var is required')
  return new TextEncoder().encode(s)
}
const ADMIN_IDS   = (process.env.ADMIN_IDS ?? process.env.ADMIN_ID ?? '').split(',').map(s => parseInt(s.trim())).filter(Boolean)
const COOKIE_NAME = 'admin_token'

/** Проверяет данные от Telegram Login Widget */
export function verifyTelegramAuth(data: Record<string, string>): boolean {
  const { hash, ...rest } = data
  if (!hash) return false

  const checkString = Object.entries(rest)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')

  // Telegram Login Widget: secret = SHA256(bot_token), не HMAC("WebAppData", ...)
  // Mini App использует другой алгоритм — здесь именно Widget.
  const secret = createHash('sha256').update(BOT_TOKEN).digest()
  const hmac   = createHmac('sha256', secret).update(checkString).digest()

  // timing-safe сравнение чтобы не течь через timing side-channel
  if (!timingSafeEqual(hmac, Buffer.from(hash, 'hex'))) return false

  // Не старше 5 минут
  const authDate = parseInt(rest.auth_date ?? '0')
  if (Date.now() / 1000 - authDate > 300) return false

  return true
}

export function isAdmin(userId: number): boolean {
  return ADMIN_IDS.includes(userId)
}

export async function createSession(userId: number, username: string): Promise<string> {
  return new SignJWT({ userId, username })
    .setProtectedHeader({ alg: 'HS256' })
    .setExpirationTime('7d')
    .setIssuedAt()
    .sign(getJwtSecret())
}

export async function getSession(): Promise<{ userId: number; username: string } | null> {
  try {
    const token = (await cookies()).get(COOKIE_NAME)?.value
    if (!token) return null
    const { payload } = await jwtVerify(token, getJwtSecret())
    return payload as { userId: number; username: string }
  } catch {
    return null
  }
}

export async function requireSession(req?: NextRequest) {
  const session = await getSession()
  if (!session) return null
  if (!isAdmin(session.userId)) return null
  return session
}

export { COOKIE_NAME }
