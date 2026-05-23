import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { stats, recentPayments, allTickets, moneyTotals } from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from './_components/AdminNav'
import { StatCard, EmptyState } from './_components/ui'

// Stars → ₽ rough conversion. Telegram Stars exchange rate ≈ 1 ⭐ = 1.4₽ (varies).
// Используется только для дисплея — фактические выплаты от Telegram идут в их курсе.
const STARS_TO_RUB = 1.4

function starsToRub(stars: number): number {
  return Math.round(stars * STARS_TO_RUB)
}

function MoneyCell({ label, stars, rub, highlight }: { label: string; stars: number; rub?: number; highlight?: boolean }) {
  const totalRub = starsToRub(stars) + (rub || 0)
  return (
    <div>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold ${highlight ? 'text-emerald-400' : 'text-white'}`}>
        ≈ {totalRub.toLocaleString('ru')} ₽
      </div>
      <div className="text-[10px] text-neutral-500">
        {stars > 0 && <span>⭐ {stars.toLocaleString('ru')}</span>}
        {stars > 0 && rub != null && rub > 0 && <span className="mx-1">·</span>}
        {rub != null && rub > 0 && <span>💎 {rub.toLocaleString('ru')} ₽</span>}
      </div>
    </div>
  )
}

function planName(key: string) {
  const map: Record<string, string> = {
    vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  }
  return map[key] ?? key
}

function payMethod(paymentId: string | null, provider: string | null) {
  // DB-stored payment_provider — canonical when populated (новые платежи).
  // Префикс payment_id — фолбэк для legacy-строк без provider.
  if (provider === 'lavatop')   return '💳 Lava'
  if (provider === 'oxapay')    return '💰 OxaPay'
  if (provider === 'cryptobot') return '💎 CryptoBot'
  if (provider === 'gift')      return '🎁 Подарок админа'
  if (provider === 'trial')     return '🎁 Trial'
  if (provider === 'free')      return '🎁 Бесплатно'
  if (provider === 'stars')     return '⭐ Stars'
  if (!paymentId) return '—'
  if (paymentId.startsWith('crypto_'))      return '💎 CryptoBot'
  if (paymentId.startsWith('oxapay_'))      return '💰 OxaPay'
  if (paymentId.startsWith('lavatop_'))     return '💳 Lava'
  if (paymentId.startsWith('admin_grant_')) return '🎁 Подарок админа'
  if (paymentId.startsWith('trial_'))       return '🎁 Trial'
  if (paymentId.startsWith('free_'))        return '🎁 Бесплатно'
  return '⭐ Stars'
}

export default async function Dashboard() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const s        = stats()
  const payments = recentPayments(10) as any[]
  const tickets  = allTickets('open') as any[]
  const money    = moneyTotals()

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">

      {/* Header */}
      <AdminNav username={session.username} />

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Пользователей"      value={s.users} />
        <StatCard
          label="Активных подписок"
          value={s.graceSubs > 0 ? `${s.activeSubs} (${s.graceSubs} grace)` : s.activeSubs}
        />
        <StatCard label="Stars заработано"   value={`⭐ ${s.totalStars}`} />
        <StatCard label="Тикетов открыто"    value={s.openTickets} tone={s.openTickets > 0 ? 'warning' : 'default'} />
      </div>

      {/* Деньги — totals + временные окна */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
        <div className="text-sm font-semibold text-white mb-3">💰 Выручка</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MoneyCell label="Всего"        stars={money.total_revenue_stars} rub={money.total_revenue_rub} highlight />
          <MoneyCell label="За 7 дней"    stars={money.revenue_7d}  rub={money.revenue_rub_7d} />
          <MoneyCell label="За 30 дней"   stars={money.revenue_30d} rub={money.revenue_rub_30d} />
          <MoneyCell label="За 90 дней"   stars={money.revenue_90d} rub={money.revenue_rub_90d} />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-4 pt-4 border-t border-neutral-800">
          <div>
            <div className="text-[10px] text-neutral-500 uppercase tracking-wider">Платящих юзеров</div>
            <div className="text-xl font-bold text-white">{money.paying_users}</div>
          </div>
          <div>
            <div className="text-[10px] text-neutral-500 uppercase tracking-wider">Повторных покупок</div>
            <div className="text-xl font-bold text-white">{money.repeat_buyers}</div>
            <div className="text-[10px] text-neutral-500">retention proxy</div>
          </div>
          <div>
            <div className="text-[10px] text-neutral-500 uppercase tracking-wider">Средний LTV</div>
            <div className="text-xl font-bold text-white">
              {money.paying_users > 0
                ? `≈ ${Math.round((starsToRub(money.total_revenue_stars) + money.total_revenue_rub) / money.paying_users).toLocaleString('ru')} ₽`
                : '—'}
            </div>
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6">

        {/* Recent payments */}
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800">
            <div className="font-semibold text-sm">Последние оплаты</div>
          </div>
          <div className="divide-y divide-neutral-800">
            {payments.map((p) => (
              <div key={p.id} className="px-5 py-3 flex items-center gap-3 hover:bg-neutral-800/30 transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">
                    {p.first_name}{p.username ? ` @${p.username}` : ''}
                  </div>
                  <div className="text-xs text-neutral-500">{planName(p.plan)} · {payMethod(p.payment_id, p.payment_provider)}</div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-sm font-semibold">
                    {p.amount_rub > 0
                      ? `💎 ${p.amount_rub.toLocaleString('ru')} ₽`
                      : `⭐ ${p.stars_paid}`}
                  </div>
                  <div className="text-xs text-neutral-500">
                    {new Date(p.created_at).toLocaleDateString('ru')}
                  </div>
                </div>
              </div>
            ))}
            {payments.length === 0 && <EmptyState title="Нет оплат" />}
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
            {tickets.length === 0 && <EmptyState title="Нет открытых тикетов 🎉" />}
          </div>
        </div>

      </div>
    </div>
  )
}
