import Link from 'next/link'
import type { StuckPayment } from '@/lib/db'

/**
 * StuckPaymentsAlert — баннер на главной dashboard'е, который показывает
 * race-rejection кейсы: payment записан как paid/completed, но subscription
 * либо нет, либо она в expired/refunded.
 *
 * Это сигнал багов в provision-cascade (fcfe3ee, bfaab08): юзер заплатил,
 * но из-за race condition в bot-handler'е его подписка не активировалась
 * или была откатана. Админу нужно либо refund'нуть, либо вручную
 * выдать sub через /grant.
 *
 * Дизайн: rose-900/30 bg + warning-icon + первые 5 строк с jump-links
 * на user-страницу (там админ откроет историю и решит, что делать).
 *
 * Если count=0 — компонент не рендерится вообще (вызывающий код может
 * проверить `stuck.length > 0`, но и сюда защита: вернуть null).
 */

const REASON_LABEL: Record<string, string> = {
  no_sub:   'sub не создана',
  expired:  'sub expired',
  refunded: 'sub refunded',
  unknown:  '—',
}

const REASON_TONE: Record<string, string> = {
  no_sub:   'text-rose-300 bg-rose-900/40',
  expired:  'text-amber-300 bg-amber-900/40',
  refunded: 'text-neutral-300 bg-neutral-800',
  unknown:  'text-neutral-400 bg-neutral-800',
}

function paymentLabel(method: string, stars: number | null, rub: number | null): string {
  if (method === 'stars' && stars != null && stars > 0) return `⭐ ${stars}`
  if (rub != null && rub > 0) {
    const icon = method === 'crypto' ? '💎' : method === 'oxapay' ? '💰' : method === 'lavatop' ? '💳' : '₽'
    return `${icon} ${rub.toLocaleString('ru')} ₽`
  }
  return method
}

export default function StuckPaymentsAlert({ items }: { items: StuckPayment[] }) {
  if (items.length === 0) return null

  const head = items.slice(0, 5)

  return (
    <div className="bg-rose-900/30 border border-rose-800/60 rounded-2xl p-5">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-rose-400" aria-hidden>⚠️</span>
        <div className="text-sm font-semibold text-rose-100">
          Stuck-платежи: {items.length}
        </div>
        <div className="text-[11px] text-rose-300/70 ml-auto">
          paid без активной sub — race-rejection (fcfe3ee, bfaab08)
        </div>
      </div>

      <div className="space-y-1.5">
        {head.map(p => {
          const name = p.first_name || `user_${p.user_id}`
          const handle = p.username ? `@${p.username}` : ''
          return (
            <Link
              key={p.payment_id}
              href={`/clients/${p.user_id}`}
              className="flex items-center gap-3 px-3 py-2 rounded-lg bg-neutral-950/40 hover:bg-neutral-950/70 transition-colors text-sm"
            >
              <div className="flex-1 min-w-0 truncate">
                <span className="text-white">{name}</span>
                {handle && <span className="text-neutral-500 ml-1">{handle}</span>}
                <span className="text-neutral-600 ml-2">#{p.payment_id}</span>
              </div>
              <div className="text-neutral-300 shrink-0">
                {paymentLabel(p.method, p.stars, p.amount_rub)}
              </div>
              <div className={`text-[10px] px-2 py-0.5 rounded-full shrink-0 ${REASON_TONE[p.reason] ?? REASON_TONE.unknown}`}>
                {REASON_LABEL[p.reason] ?? p.reason}
              </div>
              <div className="text-[10px] text-neutral-600 shrink-0 hidden md:block">
                {new Date(p.created_at).toLocaleDateString('ru')}
              </div>
            </Link>
          )
        })}
      </div>

      {items.length > 5 && (
        <div className="mt-3 text-[11px] text-rose-300/80">
          + ещё {items.length - 5} —{' '}
          <Link href="/payments?stuck=1" className="underline hover:text-rose-200">
            показать все на /payments
          </Link>
        </div>
      )}
    </div>
  )
}
