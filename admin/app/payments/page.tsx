import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { allPayments, type PaymentRow } from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'

const PLAN_NAMES: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: '🎁 Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
}

const STARS_TO_RUB = 1.4

function fmtDate(iso: string) {
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function MethodPill({ method, refunded }: { method: string; refunded: boolean }) {
  if (refunded) return <span className="text-rose-400">↶ refund</span>
  if (method === 'crypto') return <span className="text-emerald-400">💎 CryptoBot</span>
  if (method === 'free')   return <span className="text-neutral-500">🎁 gift</span>
  return <span className="text-yellow-400">⭐ Stars</span>
}

type Filters = {
  method?: 'stars' | 'crypto' | 'free'
  plan?: string
  days?: number
  hideRefunds?: boolean
}

function parseFilters(sp: Record<string, string | string[] | undefined>): Filters {
  const f: Filters = {}
  const m = sp.method
  if (m === 'stars' || m === 'crypto' || m === 'free') f.method = m
  if (typeof sp.plan === 'string' && PLAN_NAMES[sp.plan]) f.plan = sp.plan
  if (typeof sp.days === 'string') {
    const d = parseInt(sp.days, 10)
    if ([7, 30, 90, 365].includes(d)) f.days = d
  }
  if (sp.hideRefunds === '1') f.hideRefunds = true
  return f
}

function buildHref(current: Filters, patch: Partial<Filters> & { reset?: true }): string {
  if (patch.reset) return '/payments'
  const next: Filters = { ...current, ...patch }
  // Очистка undefined — иначе ?plan=undefined в URL
  const params = new URLSearchParams()
  if (next.method)      params.set('method', next.method)
  if (next.plan)        params.set('plan', next.plan)
  if (next.days)        params.set('days', String(next.days))
  if (next.hideRefunds) params.set('hideRefunds', '1')
  const qs = params.toString()
  return qs ? `/payments?${qs}` : '/payments'
}

function FilterChip({ label, href, active }: { label: string; href: string; active: boolean }) {
  return (
    <Link
      href={href}
      className={`px-3 py-1 rounded-full text-xs border transition-colors ${
        active
          ? 'bg-sky-500/20 text-sky-300 border-sky-500/40'
          : 'bg-neutral-900 text-neutral-400 border-neutral-800 hover:border-neutral-700'
      }`}
    >
      {label}
    </Link>
  )
}

export default async function Payments({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const session = await requireSession()
  if (!session) redirect('/login')

  const sp = await searchParams
  const filters = parseFilters(sp)
  const rows = allPayments({
    method: filters.method,
    plan: filters.plan,
    days: filters.days,
    includeRefunds: !filters.hideRefunds,
    limit: 500,
  })

  // Aggregates по текущему фильтру
  const realRows = rows.filter(r => !r.refunded_at)
  const totalStars = realRows.reduce((a, r) => a + (r.stars_paid || 0), 0)
  const totalRub   = realRows.reduce((a, r) => a + (r.amount_rub || 0), 0)
  const equivRub   = Math.round(totalStars * STARS_TO_RUB) + totalRub
  const refundedCount = rows.length - realRows.length

  const planKeys = ['vpn_base', 'vpn_max', 'vpn_start', 'vpn_popular', 'vpn_pro', 'vpn_family', 'vpn_trial']

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <AdminNav username={session.username} />

      <div>
        <div className="text-xl font-extrabold tracking-tight">Платежи</div>
        <div className="text-xs text-neutral-500 mt-0.5">
          Каждая платная подписка = одна запись. Метод определяется по payment_id.
        </div>
      </div>

      {/* Aggregate KPIs (отражают текущий фильтр) */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Платежей" value={realRows.length}
                  hint={refundedCount > 0 ? `+ ${refundedCount} refund` : undefined} />
        <StatCard label="≈ Выручка"
                  value={equivRub > 0 ? `${equivRub.toLocaleString('ru')} ₽` : '—'}
                  hint="Stars × 1.4 + 💎" />
        <StatCard label="⭐ Stars"  value={totalStars.toLocaleString('ru')} />
        <StatCard label="💎 ₽"      value={totalRub.toLocaleString('ru')} />
      </div>

      {/* Filters */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-neutral-500 mr-1">Период:</span>
          <FilterChip label="Всё время" href={buildHref(filters, { days: undefined })} active={!filters.days} />
          {[7, 30, 90, 365].map(d => (
            <FilterChip key={d} label={`${d} д`} href={buildHref(filters, { days: d })} active={filters.days === d} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-neutral-500 mr-1">Метод:</span>
          <FilterChip label="Все"        href={buildHref(filters, { method: undefined })} active={!filters.method} />
          <FilterChip label="⭐ Stars"    href={buildHref(filters, { method: 'stars' })}   active={filters.method === 'stars'} />
          <FilterChip label="💎 Crypto"   href={buildHref(filters, { method: 'crypto' })}  active={filters.method === 'crypto'} />
          <FilterChip label="🎁 Free"     href={buildHref(filters, { method: 'free' })}    active={filters.method === 'free'} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-neutral-500 mr-1">Тариф:</span>
          <FilterChip label="Все" href={buildHref(filters, { plan: undefined })} active={!filters.plan} />
          {planKeys.map(p => (
            <FilterChip key={p} label={PLAN_NAMES[p]} href={buildHref(filters, { plan: p })} active={filters.plan === p} />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterChip
            label={filters.hideRefunds ? '✓ Скрыть refund' : 'Скрыть refund'}
            href={buildHref(filters, { hideRefunds: filters.hideRefunds ? undefined : true })}
            active={!!filters.hideRefunds}
          />
          {(filters.method || filters.plan || filters.days || filters.hideRefunds) && (
            <Link href="/payments" className="text-xs text-neutral-500 hover:text-white">
              сбросить фильтры
            </Link>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">{rows.length} записей</div>
          {rows.length === 500 && (
            <div className="text-[10px] text-neutral-500">показаны последние 500</div>
          )}
        </div>
        {rows.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-500">по выбранным фильтрам ничего нет</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-neutral-500 uppercase tracking-wide">
                <tr className="border-b border-neutral-800">
                  <th className="text-left  px-4 py-2 font-medium">Когда</th>
                  <th className="text-left  px-4 py-2 font-medium">Юзер</th>
                  <th className="text-left  px-4 py-2 font-medium">Тариф</th>
                  <th className="text-left  px-4 py-2 font-medium">Метод</th>
                  <th className="text-right px-4 py-2 font-medium">Сумма</th>
                  <th className="text-left  px-4 py-2 font-medium">Payment ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800">
                {rows.map((r: PaymentRow) => (
                  <tr key={r.id} className={`hover:bg-neutral-800/30 ${r.refunded_at ? 'opacity-60' : ''}`}>
                    <td className="px-4 py-2 text-neutral-400 text-xs whitespace-nowrap">{fmtDate(r.created_at)}</td>
                    <td className="px-4 py-2">
                      <Link href={`/clients/${r.user_id}`} className="hover:text-sky-400">
                        <div className="font-medium truncate max-w-[200px]">
                          {r.first_name || 'unknown'}
                          {r.username && <span className="text-neutral-500"> @{r.username}</span>}
                        </div>
                        <div className="text-[10px] text-neutral-600 font-mono">id {r.user_id}</div>
                      </Link>
                    </td>
                    <td className="px-4 py-2">{PLAN_NAMES[r.plan] || r.plan}</td>
                    <td className="px-4 py-2 text-xs"><MethodPill method={r.method} refunded={!!r.refunded_at} /></td>
                    <td className="px-4 py-2 text-right">
                      {r.amount_rub > 0 ? (
                        <span className="text-emerald-400">💎 {r.amount_rub.toLocaleString('ru')} ₽</span>
                      ) : r.stars_paid > 0 ? (
                        <span className="text-yellow-400">⭐ {r.stars_paid}</span>
                      ) : (
                        <span className="text-neutral-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-[10px] text-neutral-600 font-mono truncate max-w-[180px]">
                      {r.payment_id || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className="text-2xl font-bold text-white">{value}</div>
      {hint && <div className="text-xs text-neutral-500 mt-1">{hint}</div>}
    </div>
  )
}
