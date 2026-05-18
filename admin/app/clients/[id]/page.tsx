import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { userFull, type SubRow } from '@/lib/db'
import { redirect, notFound } from 'next/navigation'
import AdminNav from '../../_components/AdminNav'
import { ExtendSubButton, RefundSubButton, BanUserButton } from '../../_components/AdminActions'

const PLAN_NAMES: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: '🎁 Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
}

// Stars → ₽ для единого знаменателя в LTV.
const STARS_TO_RUB = 1.4

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtDateOnly(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
}

function StatusPill({ status, refundedAt }: { status: string; refundedAt: string | null }) {
  if (refundedAt) {
    return <span className="px-2 py-0.5 rounded-full text-[10px] bg-rose-500/10 text-rose-400 border border-rose-500/30">refunded</span>
  }
  const styles: Record<string, string> = {
    active:  'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    grace:   'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
    expired: 'bg-neutral-500/10 text-neutral-400 border-neutral-500/30',
  }
  const cls = styles[status] ?? 'bg-neutral-500/10 text-neutral-500 border-neutral-700'
  return <span className={`px-2 py-0.5 rounded-full text-[10px] border ${cls}`}>{status}</span>
}

function MoneyCell({ stars, rub }: { stars: number; rub: number }) {
  if (stars > 0 && rub > 0) {
    return (
      <div>
        <div className="text-yellow-400">⭐ {stars}</div>
        <div className="text-emerald-400 text-xs">💎 {rub.toLocaleString('ru')} ₽</div>
      </div>
    )
  }
  if (rub > 0) return <span className="text-emerald-400">💎 {rub.toLocaleString('ru')} ₽</span>
  if (stars > 0) return <span className="text-yellow-400">⭐ {stars}</span>
  return <span className="text-neutral-600">—</span>
}

export default async function ClientDetail({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) redirect('/login')

  const { id } = await params
  const userId = parseInt(id, 10)
  if (!Number.isFinite(userId)) notFound()

  const { user, subs, tickets, configCount } = userFull(userId)
  if (!user) notFound()

  // LTV-агрегаты (исключаем рефанды из выручки, но показываем счётчик).
  const realSubs = subs.filter(s => !s.refunded_at)
  const totalStars = realSubs.reduce((a, s) => a + (s.stars_paid || 0), 0)
  const totalRub   = realSubs.reduce((a, s) => a + (s.amount_rub || 0), 0)
  const ltvRub     = Math.round(totalStars * STARS_TO_RUB) + totalRub
  const paidSubs   = realSubs.filter(s => s.plan !== 'vpn_trial').length
  const trialSubs  = realSubs.filter(s => s.plan === 'vpn_trial').length
  const refundedCount = subs.filter(s => s.refunded_at).length
  const activeSub  = subs.find(s => s.status === 'active') ?? null

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <AdminNav username={session.username} />

      <div className="flex items-baseline gap-3">
        <Link href="/clients" className="text-sm text-neutral-400 hover:text-white">← Топ клиентов</Link>
      </div>

      {/* Header */}
      <div className={`bg-neutral-900 border rounded-2xl p-5 ${user.is_banned ? 'border-rose-500/40' : 'border-neutral-800'}`}>
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <div>
            <div className="text-xl font-extrabold flex items-center gap-2">
              <span>
                {user.first_name || 'unknown'}
                {user.username && (
                  <a
                    href={`https://t.me/${user.username}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-neutral-500 font-normal text-base hover:text-sky-400 transition-colors"
                  >
                    {' '}@{user.username}
                  </a>
                )}
              </span>
              {user.is_banned ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] bg-rose-500/20 text-rose-300 border border-rose-500/40">BANNED</span>
              ) : null}
            </div>
            <div className="text-[10px] text-neutral-600 font-mono mt-1">id {user.id}</div>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-xs text-neutral-500">
              Зарегистрирован {fmtDateOnly(user.created_at)}
            </div>
            <BanUserButton userId={user.id} banned={user.is_banned === 1} />
          </div>
        </div>
        {user.is_banned === 1 && user.banned_reason && (
          <div className="mt-2 text-xs text-rose-300">
            Причина: {user.banned_reason}
            {user.banned_at && <span className="text-neutral-500"> · {fmtDateOnly(user.banned_at)}</span>}
          </div>
        )}
        {(user.referred_by || user.ref_bonus_days > 0) && (
          <div className="mt-3 pt-3 border-t border-neutral-800 flex flex-wrap gap-4 text-xs">
            {user.referred_by && (
              <span className="text-neutral-400">
                Пришёл от <Link href={`/clients/${user.referred_by}`} className="text-sky-400 hover:underline font-mono">id {user.referred_by}</Link>
              </span>
            )}
            {user.ref_bonus_days > 0 && (
              <span className="text-emerald-400">+{user.ref_bonus_days} бонусных дней получено</span>
            )}
          </div>
        )}
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="LTV" value={ltvRub > 0 ? `≈ ${ltvRub.toLocaleString('ru')} ₽` : '—'}
                  hint={totalStars > 0 || totalRub > 0 ? `⭐${totalStars} + 💎${totalRub.toLocaleString('ru')}₽` : 'не платил'} />
        <StatCard label="Платных подписок" value={paidSubs}
                  hint={trialSubs > 0 ? `+ ${trialSubs} триал${trialSubs === 1 ? '' : 'а'}` : undefined} />
        <StatCard label="Активных конфигов" value={configCount}
                  hint={activeSub ? `до ${fmtDateOnly(activeSub.expires_at)}` : 'нет подписки'} />
        <StatCard label="Сейчас на тарифе"
                  value={activeSub ? (PLAN_NAMES[activeSub.plan] || activeSub.plan) : '—'}
                  hint={refundedCount > 0 ? `${refundedCount} refund${refundedCount === 1 ? '' : 'ов'}` : undefined} />
      </div>

      {/* Subscriptions history */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">История подписок</div>
          <div className="text-xs text-neutral-500">{subs.length} записей</div>
        </div>
        {subs.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm text-neutral-500">подписок нет</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-neutral-500 uppercase tracking-wide">
                <tr className="border-b border-neutral-800">
                  <th className="text-left  px-4 py-2 font-medium">Тариф</th>
                  <th className="text-left  px-4 py-2 font-medium">Статус</th>
                  <th className="text-right px-4 py-2 font-medium">Сумма</th>
                  <th className="text-left  px-4 py-2 font-medium">Куплена</th>
                  <th className="text-left  px-4 py-2 font-medium">Истекает</th>
                  <th className="text-left  px-4 py-2 font-medium">Действия</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800">
                {subs.map((s: SubRow) => {
                  const isActive = s.status === 'active' || s.status === 'grace'
                  const isStars  = !!(s.payment_id && !s.payment_id.startsWith('crypto_')
                                      && !s.payment_id.startsWith('free_'))
                  const alreadyRefunded = !!s.refunded_at
                  return (
                    <tr key={s.id} className="hover:bg-neutral-800/30">
                      <td className="px-4 py-2">
                        <div className="font-medium">{PLAN_NAMES[s.plan] || s.plan}</div>
                        {s.pending_plan && (
                          <div className="text-[10px] text-yellow-500">→ {PLAN_NAMES[s.pending_plan] || s.pending_plan}</div>
                        )}
                      </td>
                      <td className="px-4 py-2"><StatusPill status={s.status} refundedAt={s.refunded_at} /></td>
                      <td className="px-4 py-2 text-right"><MoneyCell stars={s.stars_paid} rub={s.amount_rub} /></td>
                      <td className="px-4 py-2 text-neutral-400 text-xs">{fmtDate(s.created_at)}</td>
                      <td className="px-4 py-2 text-neutral-400 text-xs">
                        {fmtDateOnly(s.expires_at)}
                        {s.grace_until && (
                          <div className="text-[10px] text-yellow-500">grace до {fmtDateOnly(s.grace_until)}</div>
                        )}
                        <div className="text-[10px] text-neutral-700 font-mono mt-0.5 truncate max-w-[140px]"
                             title={s.payment_id || ''}>
                          {s.payment_id || '—'}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {isActive && (
                            <>
                              <ExtendSubButton subId={s.id} days={7} />
                              <ExtendSubButton subId={s.id} days={30} />
                            </>
                          )}
                          {!alreadyRefunded && s.plan !== 'vpn_trial' && (
                            <RefundSubButton subId={s.id} isStars={isStars} />
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Tickets */}
      {tickets.length > 0 && (
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
          <div className="px-5 py-4 border-b border-neutral-800">
            <div className="font-semibold text-sm">Последние тикеты</div>
          </div>
          <div className="divide-y divide-neutral-800">
            {tickets.map(t => (
              <div key={t.id} className="px-5 py-3">
                <div className="flex items-baseline justify-between gap-2">
                  <div className="text-xs text-neutral-500">{t.category} · {fmtDate(t.created_at)}</div>
                  <div className="text-[10px] text-neutral-500">{t.status}</div>
                </div>
                <div className="text-sm mt-1 line-clamp-2">{t.message}</div>
              </div>
            ))}
          </div>
        </div>
      )}
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
