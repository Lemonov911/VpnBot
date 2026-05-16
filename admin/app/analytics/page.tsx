import { requireSession } from '@/lib/auth'
import {
  analyticsSummary,
  dailyRevenueLast30,
  planMix30d,
  trialFunnel30d,
  topReferrers,
} from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'
import { RevenueArea } from '../_components/RevenueChart'

function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className="text-2xl font-bold text-white">{value}</div>
      {hint && <div className="text-xs text-neutral-500 mt-1">{hint}</div>}
    </div>
  )
}

function planName(key: string) {
  const map: Record<string, string> = {
    vpn_base: 'База', vpn_max: 'Макс', vpn_trial: '🎁 Триал',
    vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
    vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
  }
  return map[key] ?? key
}

// Stars → ₽ для отображения суммарной выручки по тарифу в едином знаменателе.
// Курс примерный, реальная выплата от Telegram идёт по их курсу.
const STARS_TO_RUB = 1.4

export default async function Analytics() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const s      = analyticsSummary()
  const daily  = dailyRevenueLast30()
  const mix    = planMix30d()
  const funnel = trialFunnel30d()
  const refs   = topReferrers(10)

  const totalMixCount = mix.reduce((a, b) => a + b.count, 0) || 1

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <AdminNav username={session.username} />

      <div>
        <div className="text-xl font-extrabold tracking-tight">Аналитика</div>
        <div className="text-xs text-neutral-500 mt-0.5">За последние 30 дней</div>
      </div>

      {/* Headline KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Новых юзеров (30д)"  value={s.users_30d} hint={`из ${s.users_total} всего`} />
        <StatCard label="Платных подписок"     value={s.subs_paid_30d} hint={`+${s.subs_trial_30d} триалов`} />
        <StatCard label="⭐ за 30д"             value={s.revenue_stars_30d} hint={`из них ${s.revenue_stars_7d} за 7д`} />
        <StatCard label="Истекли (30д)"        value={s.expired_30d} hint="churn-сигнал" />
      </div>

      {/* Charts row — Recharts вместо inline SVG-sparkline (теперь с осями и тултипами) */}
      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-xs text-neutral-500 uppercase tracking-wider mb-2">⭐ Выручка по дням</div>
          <RevenueArea data={daily} metric="stars" color="#facc15" />
          <div className="text-[10px] text-neutral-600 mt-2">
            {daily.length > 0 ? `${daily[0].day} → ${daily[daily.length - 1].day}` : 'нет данных за 30 дней'}
          </div>
        </div>
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-xs text-neutral-500 uppercase tracking-wider mb-2">Платные подписки по дням</div>
          <RevenueArea data={daily} metric="paid_subs" color="#10b981" />
          <div className="text-[10px] text-neutral-600 mt-2">
            {daily.reduce((a, b) => a + b.paid_subs, 0)} за период
          </div>
        </div>
      </div>

      {/* Funnel + plan mix */}
      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">Воронка (30 дней)</div>
          <div className="space-y-2.5 text-sm">
            <div className="flex justify-between"><span className="text-neutral-400">Новых юзеров</span><span className="font-semibold">{funnel.new_users}</span></div>
            <div className="flex justify-between"><span className="text-neutral-400">Взяли триал</span><span className="font-semibold">{funnel.trial_users}</span></div>
            <div className="flex justify-between"><span className="text-neutral-400">Триал → платный</span><span className="font-semibold">{funnel.trial_then_paid}</span></div>
            <div className="flex justify-between"><span className="text-neutral-400">Платный без триала</span><span className="font-semibold">{funnel.direct_paid}</span></div>
            <div className="border-t border-neutral-800 pt-2 mt-2">
              <div className="flex justify-between text-emerald-400"><span>Конверсия триал → платный</span><span className="font-bold">{funnel.trial_conversion}%</span></div>
              <div className="flex justify-between text-sky-400"><span>Конверсия регистрация → платный</span><span className="font-bold">{funnel.register_to_paid}%</span></div>
            </div>
          </div>
        </div>

        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">Микс тарифов (30 дней)</div>
          {mix.length === 0 ? (
            <div className="text-sm text-neutral-500">нет данных</div>
          ) : (
            <div className="space-y-2">
              {mix.map(m => {
                const pct = Math.round((m.count / totalMixCount) * 100)
                const isTrial = m.plan === 'vpn_trial'
                // Суммарный ₽-эквивалент: Stars в рубли + прямые CryptoBot платежи.
                // Раздельно показываем ⭐ и 💎 в подсказке для прозрачности.
                const totalRub = Math.round(m.stars * STARS_TO_RUB) + (m.amount_rub || 0)
                return (
                  <div key={m.plan}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className={isTrial ? 'text-neutral-400' : 'text-white'}>{planName(m.plan)}</span>
                      <span className="text-neutral-500">
                        {m.count} · {pct}%
                        {!isTrial && totalRub > 0 && (
                          <>
                            {' · '}
                            <span className="text-emerald-400">≈ {totalRub.toLocaleString('ru')} ₽</span>
                          </>
                        )}
                      </span>
                    </div>
                    {!isTrial && (m.stars > 0 || m.amount_rub > 0) && (
                      <div className="text-[10px] text-neutral-600 mb-1 flex gap-2">
                        {m.stars > 0 && <span>⭐ {m.stars.toLocaleString('ru')}</span>}
                        {m.amount_rub > 0 && <span>💎 {m.amount_rub.toLocaleString('ru')} ₽</span>}
                      </div>
                    )}
                    <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                      <div
                        className={isTrial ? 'h-full bg-neutral-600' : 'h-full bg-emerald-500'}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Top referrers */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800">
          <div className="font-semibold text-sm">Топ рефереров</div>
          <div className="text-xs text-neutral-500 mt-0.5">Платные приглашения = бонус +7 дней рефереру</div>
        </div>
        <div className="divide-y divide-neutral-800">
          {refs.length === 0 ? (
            <div className="px-5 py-6 text-center text-sm text-neutral-500">пока никто не приглашал</div>
          ) : refs.map(r => (
            <div key={r.id} className="px-5 py-3 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">
                  {r.first_name || 'unknown'}{r.username ? ` @${r.username}` : ''}
                </div>
                <div className="text-[10px] text-neutral-600">id {r.id}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-semibold">{r.invited_paid} <span className="text-neutral-500 text-xs">платных</span></div>
                <div className="text-[10px] text-neutral-500">{r.invited} приглашённых</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
