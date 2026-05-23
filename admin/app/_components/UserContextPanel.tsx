import Link from 'next/link'
import { userContextForTicket } from '@/lib/db'

/**
 * Inline-секция «О юзере» в detail-view тикета. Чтобы админ не уходил на
 * /clients/[id] ради быстрого взгляда «кто это / есть ли активная подписка /
 * не забанен ли».
 *
 * Server component — читает SQLite напрямую при рендере страницы. Один трип:
 * userContextForTicket() делает 4 запроса (profile + active sub + top-3 payments
 * + ban-status).
 */

const PLAN_LABELS: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: 'Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
}

const METHOD_LABELS: Record<string, string> = {
  stars: 'Stars',
  cryptobot: 'CryptoBot',
  oxapay: 'OxaPay',
  lavatop: 'Lava',
  gift: 'Подарок',
  trial: 'Триал',
  free: 'Free',
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function UserContextPanel({ userId }: { userId: number }) {
  const { profile, subscription, recentPayments, ban } = userContextForTicket(userId)

  if (!profile) {
    return (
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-4 text-xs text-neutral-500">
        Юзер не найден в БД (id {userId})
      </div>
    )
  }

  const banned = ban && ban.is_banned === 1

  return (
    <div className={`bg-neutral-900 border rounded-2xl p-4 space-y-3 ${banned ? 'border-rose-500/40' : 'border-neutral-800'}`}>
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-neutral-400">О юзере</div>
        <Link
          href={`/clients/${profile.id}`}
          className="text-[10px] text-sky-400 hover:text-sky-300"
        >
          открыть карточку →
        </Link>
      </div>

      {/* Profile row */}
      <div>
        <div className="text-sm font-medium text-neutral-200">
          {profile.first_name || 'unknown'}
          {profile.username && (
            <span className="text-neutral-500"> @{profile.username}</span>
          )}
        </div>
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5">id {profile.id}</div>
        <div className="text-[10px] text-neutral-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
          <span>зарегистрирован: {fmtDate(profile.created_at)}</span>
          {profile.referred_by != null && (
            <span>пригласил: <span className="font-mono">{profile.referred_by}</span></span>
          )}
          {profile.traffic_source && (
            <span>источник: <span className="font-mono">{profile.traffic_source}</span></span>
          )}
        </div>
      </div>

      {/* Ban warning */}
      {banned && (
        <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/30 rounded-md px-3 py-2">
          <div className="font-semibold">ЗАБАНЕН</div>
          {ban?.banned_reason && <div className="mt-1">{ban.banned_reason}</div>}
          {ban?.banned_at && (
            <div className="text-[10px] text-rose-400/70 mt-0.5">с {fmtDate(ban.banned_at)}</div>
          )}
        </div>
      )}

      {/* Subscription */}
      <div className="border-t border-neutral-800 pt-3">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1">Подписка</div>
        {subscription ? (
          <div className="text-xs text-neutral-300 flex flex-wrap gap-x-3 gap-y-0.5">
            <span className="font-medium">{PLAN_LABELS[subscription.plan] ?? subscription.plan}</span>
            <span className={
              subscription.status === 'active'  ? 'text-emerald-400' :
              subscription.status === 'grace'   ? 'text-yellow-400' :
              subscription.status === 'expired' ? 'text-neutral-500' :
              'text-neutral-400'
            }>
              ● {subscription.status}
            </span>
            {subscription.expires_at && (
              <span className="text-neutral-500">до {fmtDate(subscription.expires_at)}</span>
            )}
          </div>
        ) : (
          <div className="text-xs text-neutral-500 italic">нет подписки</div>
        )}
      </div>

      {/* Recent payments */}
      <div className="border-t border-neutral-800 pt-3">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-1.5">Последние платежи</div>
        {recentPayments.length === 0 ? (
          <div className="text-xs text-neutral-500 italic">платежей нет</div>
        ) : (
          <ul className="space-y-1">
            {recentPayments.map(p => (
              <li key={p.id} className="text-xs flex items-baseline justify-between gap-2">
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-neutral-500 text-[10px] shrink-0">{fmtDateTime(p.created_at)}</span>
                  <span className="text-neutral-300 truncate">
                    {PLAN_LABELS[p.plan] ?? p.plan}
                  </span>
                  <span className="text-[10px] text-neutral-500">{METHOD_LABELS[p.method] ?? p.method}</span>
                </div>
                <div className="shrink-0 flex items-baseline gap-1.5">
                  {p.refunded_at && (
                    <span className="text-[10px] text-rose-400">refund</span>
                  )}
                  {p.stars_paid > 0 && (
                    <span className="text-yellow-400 text-xs">⭐ {p.stars_paid}</span>
                  )}
                  {p.amount_rub > 0 && (
                    <span className="text-emerald-400 text-xs">{p.amount_rub} ₽</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
