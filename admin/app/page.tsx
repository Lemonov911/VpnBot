import { requireSession } from '@/lib/auth'
import { stats, recentPayments, allTickets } from '@/lib/db'
import { redirect } from 'next/navigation'

function StatCard({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className={`bg-neutral-900 border rounded-2xl p-5 ${warn ? 'border-yellow-500/30' : 'border-neutral-800'}`}>
      <div className="text-xs text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-3xl font-bold ${warn ? 'text-yellow-400' : 'text-white'}`}>{value}</div>
    </div>
  )
}

function planName(key: string) {
  const map: Record<string, string> = {
    vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  }
  return map[key] ?? key
}

function payMethod(paymentId: string) {
  if (!paymentId) return '—'
  if (paymentId.startsWith('crypto_')) return '💎 Крипто'
  if (paymentId.startsWith('free_')) return '🎁 Бесплатно'
  return '⭐ Stars'
}

export default async function Dashboard() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const s        = stats()
  const payments = recentPayments(10) as any[]
  const tickets  = allTickets('open') as any[]

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">

      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <div>
          <div className="text-xl font-extrabold tracking-tight">MAX VPN &amp; eSIM</div>
          <div className="text-xs text-neutral-500 mt-0.5">Привет, {session.username}</div>
        </div>
        <div className="flex gap-4 items-center">
          <a href="/analytics"  className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">Аналитика</a>
          <a href="/clients"    className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">Клиенты</a>
          <a href="/monitoring" className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">Мониторинг</a>
          <a href="/tickets"    className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">Обращения</a>
          <a href="/servers"    className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">Серверы</a>
          <a href="/logout"     className="text-xs text-neutral-600 hover:text-neutral-400 transition-colors">Выйти</a>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Пользователей"      value={s.users} />
        <StatCard label="Активных подписок"  value={s.activeSubs} />
        <StatCard label="Stars заработано"   value={`⭐ ${s.totalStars}`} />
        <StatCard label="Тикетов открыто"    value={s.openTickets} warn={s.openTickets > 0} />
      </div>

      <div className="grid md:grid-cols-2 gap-6">

        {/* Recent payments */}
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800">
            <div className="font-semibold text-sm">Последние оплаты</div>
          </div>
          <div className="divide-y divide-neutral-800">
            {payments.map((p) => (
              <div key={p.id} className="px-5 py-3 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">
                    {p.first_name}{p.username ? ` @${p.username}` : ''}
                  </div>
                  <div className="text-xs text-neutral-500">{planName(p.plan)} · {payMethod(p.payment_id)}</div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-sm font-semibold">⭐ {p.stars_paid}</div>
                  <div className="text-xs text-neutral-500">
                    {new Date(p.created_at).toLocaleDateString('ru')}
                  </div>
                </div>
              </div>
            ))}
            {payments.length === 0 && (
              <div className="px-5 py-8 text-center text-sm text-neutral-600">Нет оплат</div>
            )}
          </div>
        </div>

        {/* Open tickets */}
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800">
            <div className="font-semibold text-sm">Открытые тикеты</div>
          </div>
          <div className="divide-y divide-neutral-800">
            {tickets.map((t) => (
              <div key={t.id} className="px-5 py-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-xs text-neutral-500">
                    #{t.id} · <span className="text-blue-400">{t.category}</span> · {t.first_name}{t.username ? ` @${t.username}` : ''}
                  </div>
                  <div className="text-xs text-neutral-600">
                    {new Date(t.created_at).toLocaleDateString('ru')}
                  </div>
                </div>
                <div className="text-sm text-neutral-300 line-clamp-2">{t.message}</div>
              </div>
            ))}
            {tickets.length === 0 && (
              <div className="px-5 py-8 text-center text-sm text-neutral-600">Нет открытых тикетов 🎉</div>
            )}
          </div>
        </div>

      </div>
    </div>
  )
}
