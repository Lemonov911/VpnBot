import { createHmac } from 'crypto'
import { SignJWT, jwtVerify } from 'jose'
import { cookies } from 'next/headers'
import { NextRequest } from 'next/server'

const BOT_TOKEN   = process.env.BOT_TOKEN!
const JWT_SECRET  = new TextEncoder().encode(process.env.JWT_SECRET ?? 'change-me-in-production')
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

  // Ключ = SHA256(bot_token)
  const secret = createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest()
  const hmac   = createHmac('sha256', secret).update(checkString).digest('hex')

  if (hmac !== hash) return false

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
    .sign(JWT_SECRET)
}

export async function getSession(): Promise<{ userId: number; username: string } | null> {
  try {
    const token = (await cookies()).get(COOKIE_NAME)?.value
    if (!token) return null
    const { payload } = await jwtVerify(token, JWT_SECRET)
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
