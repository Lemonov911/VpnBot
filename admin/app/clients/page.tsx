import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { topClients, moneyTotals } from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'

const PLAN_NAMES: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: '🎁 Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
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

export default async function Clients() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const clients = topClients(50)
  const m = moneyTotals()
  const avgLtv = m.paying_users > 0 ? Math.round(m.total_revenue_stars / m.paying_users) : 0
  // Распределение по top-X
  const top10Sum = clients.slice(0, 10).reduce((a, b) => a + b.total_stars, 0)
  const top10Share = m.total_revenue_stars > 0 ? Math.round((top10Sum / m.total_revenue_stars) * 100) : 0

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <AdminNav />

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Платящих юзеров"   value={m.paying_users} hint={`всего ⭐${m.total_revenue_stars}`} />
        <StatCard label="Средний LTV"        value={`⭐ ${avgLtv}`} hint="total revenue / payers" />
        <StatCard label="Повторных покупок"  value={m.repeat_buyers} hint="≥ 2 платных подписки" />
        <StatCard label="Топ-10 = доля"      value={`${top10Share}%`} hint="от всей выручки" />
      </div>

      {/* Period buckets */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="⭐ за 7 дней"  value={m.revenue_7d} />
        <StatCard label="⭐ за 30 дней" value={m.revenue_30d} />
        <StatCard label="⭐ за 90 дней" value={m.revenue_90d} />
      </div>

      {/* Ranking */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">Топ-50 клиентов</div>
          <div className="text-xs text-neutral-500">по сумме потраченных Stars (всё время)</div>
        </div>
        {clients.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-500">пока никто не платил</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-neutral-500 uppercase tracking-wide">
                <tr className="border-b border-neutral-800">
                  <th className="text-left px-4 py-2 font-medium w-8">#</th>
                  <th className="text-left px-4 py-2 font-medium">Юзер</th>
                  <th className="text-right px-4 py-2 font-medium">⭐ LTV</th>
                  <th className="text-right px-4 py-2 font-medium">Покупок</th>
                  <th className="text-left px-4 py-2 font-medium">Сейчас на</th>
                  <th className="text-left px-4 py-2 font-medium">Истекает</th>
                  <th className="text-left px-4 py-2 font-medium">Last buy</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800">
                {clients.map((c, i) => (
                  <tr key={c.id} className="hover:bg-neutral-800/30 transition-colors">
                    <td className="px-4 py-2 text-neutral-500 font-mono">{i + 1}</td>
                    <td className="px-4 py-2">
                      <Link href={`/clients/${c.id}`} className="block hover:text-sky-400">
                        <div className="font-medium truncate max-w-[200px]">
                          {c.first_name || 'unknown'}
                          {c.username && <span className="text-neutral-500"> @{c.username}</span>}
                        </div>
                        <div className="text-[10px] text-neutral-600 font-mono">id {c.id}</div>
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-right font-semibold text-yellow-400">⭐ {c.total_stars}</td>
                    <td className="px-4 py-2 text-right">
                      {c.paid_subs}
                      {c.trial_subs > 0 && (
                        <span className="text-neutral-600 text-xs"> + {c.trial_subs} тр</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      {c.current_plan ? (
                        <span className="text-emerald-400">{PLAN_NAMES[c.current_plan] || c.current_plan}</span>
                      ) : (
                        <span className="text-neutral-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-neutral-400 text-xs">{fmtDate(c.active_until)}</td>
                    <td className="px-4 py-2 text-neutral-500 text-xs">{fmtDate(c.last_purchase)}</td>
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
