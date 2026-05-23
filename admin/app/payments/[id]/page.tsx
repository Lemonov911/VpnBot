import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { paymentDetail, type PaymentChargeRow, type PaymentDetailRow } from '@/lib/db'
import { redirect, notFound } from 'next/navigation'
import AdminNav from '../../_components/AdminNav'
import { StatCard, EmptyState } from '../../_components/ui'

/**
 * B2: страница деталей одного платежа.
 *
 * Источник правды — `subscriptions` (как и весь /payments). В реальности
 * "платёж" в боте = "подписка с payment_id" + опционально multiple `payments`
 * строки если был upgrade (Stars doplata). Поэтому показываем:
 *  - subscription как основной объект
 *  - все `payments`-charges (timeline-таблица в самом низу — детализация
 *    по транзакциям для multi-charge сценариев)
 *  - timeline-блок «created → paid → applied → refunded» (точки события,
 *    собираются из sub-row + первого active config'а — здесь упрощённо
 *    оставлены 3 фиксированные точки)
 */

const PLAN_NAMES: Record<string, string> = {
  vpn_base: 'База', vpn_max: 'Макс', vpn_trial: '🎁 Триал',
  vpn_start: 'Старт', vpn_popular: 'Популярный', vpn_pro: 'Про', vpn_family: 'Семейный',
  vpn_1m: '1 мес', vpn_3m: '3 мес', vpn_1y: '1 год',
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function MethodLabel({ method }: { method: string }) {
  if (method === 'cryptobot' || method === 'crypto') return <span className="text-emerald-400">💎 CryptoBot</span>
  if (method === 'oxapay')      return <span className="text-amber-400">💰 OxaPay</span>
  if (method === 'lavatop')     return <span className="text-sky-400">💳 Lava</span>
  if (method === 'gift' || method === 'admin_grant') return <span className="text-fuchsia-400">🎁 Admin grant</span>
  if (method === 'trial')       return <span className="text-neutral-400">🎁 Trial</span>
  if (method === 'free')        return <span className="text-neutral-500">🎁 Free</span>
  return <span className="text-yellow-400">⭐ Stars</span>
}

function StatusPill({ row }: { row: PaymentDetailRow }) {
  if (row.refunded_at) {
    return <span className="px-2 py-0.5 rounded-full text-xs bg-rose-500/10 text-rose-400 border border-rose-500/30">refunded</span>
  }
  const styles: Record<string, string> = {
    active:  'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    grace:   'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
    expired: 'bg-neutral-500/10 text-neutral-400 border-neutral-500/30',
  }
  const cls = styles[row.status] ?? 'bg-neutral-500/10 text-neutral-500 border-neutral-700'
  return <span className={`px-2 py-0.5 rounded-full text-xs border ${cls}`}>{row.status}</span>
}

/**
 * Vertical-timeline. Каждая точка = событие, есть или нет. Если нет — точка
 * tone="future" (серый). Если есть — окрашена + дата под надписью.
 */
function TimelinePoint({
  label, ts, color, last,
}: { label: string; ts: string | null; color: string; last?: boolean }) {
  const hasTs = !!ts
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div className={`w-3 h-3 rounded-full ${hasTs ? color : 'bg-neutral-800 border border-neutral-700'}`}/>
        {!last && <div className="w-px flex-1 bg-neutral-800 my-1"/>}
      </div>
      <div className={`pb-6 ${hasTs ? '' : 'opacity-40'}`}>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-neutral-500 mt-0.5">{hasTs ? fmtDateTime(ts) : 'не произошло'}</div>
      </div>
    </div>
  )
}

export default async function PaymentDetail({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) redirect('/login')

  const { id } = await params
  const subId = parseInt(id, 10)
  if (!Number.isFinite(subId) || subId <= 0) notFound()

  const { row, charges } = paymentDetail(subId)
  if (!row) notFound()

  // BAN-status юзера — показываем рядом со ссылкой на клиента.
  const isBanned = !!row.ban_status
  // Метка «applied» — у нас нет отдельного applied_at, но если подписка
  // когда-либо стала active/grace/expired/refunded (=всё кроме pending) —
  // считаем что applied. В нашей схеме pending почти не используется (sub
  // создаётся сразу active после успешного charge'а), но фолбэк держим.
  const appliedAt = (row.status !== 'pending') ? row.created_at : null

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <AdminNav username={session.username} />

      <div>
        <Link href="/payments" className="text-xs text-neutral-500 hover:text-white">← к списку</Link>
        <div className="text-xl font-extrabold tracking-tight mt-2 flex items-center gap-3">
          Платёж #{row.id}
          <StatusPill row={row} />
        </div>
        <div className="text-xs text-neutral-500 mt-0.5 font-mono">{row.payment_id ?? '—'}</div>
      </div>

      {/* KPI-карточки */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Метод" value={<MethodLabel method={row.method} />} />
        <StatCard label="Тариф" value={PLAN_NAMES[row.plan] || row.plan} />
        <StatCard
          label="Сумма"
          value={
            row.amount_rub > 0
              ? <span className="text-emerald-400">💎 {row.amount_rub.toLocaleString('ru')} ₽</span>
              : row.stars_paid > 0
                ? <span className="text-yellow-400">⭐ {row.stars_paid}</span>
                : <span className="text-neutral-600">—</span>
          }
        />
        <StatCard
          label="Истекает"
          value={fmtDateTime(row.expires_at)}
          hint={row.pending_plan ? `pending: ${PLAN_NAMES[row.pending_plan] || row.pending_plan}` : undefined}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Timeline */}
        <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
          <div className="text-sm font-semibold mb-4">Timeline</div>
          <TimelinePoint label="Создан"  ts={row.created_at}  color="bg-sky-500" />
          <TimelinePoint label="Оплачен" ts={row.created_at}  color="bg-emerald-500" />
          <TimelinePoint label="Применён (подписка активирована)" ts={appliedAt} color="bg-emerald-500" />
          <TimelinePoint label="Возврат" ts={row.refunded_at} color="bg-rose-500" last />
        </div>

        {/* Linked entities */}
        <div className="space-y-4">
          {/* Клиент */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
            <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-2">Клиент</div>
            <div className="flex items-center justify-between gap-3">
              <div>
                <Link href={`/clients/${row.user_id}`} className="text-base font-semibold hover:text-sky-400">
                  {row.first_name || 'unknown'}
                </Link>
                <div className="text-xs text-neutral-500 font-mono mt-0.5">id {row.user_id}</div>
                {row.username && (
                  <a
                    href={`https://t.me/${row.username}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-0.5 mt-1 px-1.5 py-0.5 rounded text-[10px]
                               bg-sky-500/10 text-sky-400 border border-sky-500/20 hover:bg-sky-500/20"
                  >
                    ↗ @{row.username}
                  </a>
                )}
              </div>
              {isBanned && (
                <span className="px-2 py-0.5 rounded-full text-[10px] bg-rose-500/10 text-rose-400 border border-rose-500/30">
                  🚫 banned
                </span>
              )}
            </div>
          </div>

          {/* Подписка */}
          <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5">
            <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-2">Подписка</div>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-base font-semibold">{PLAN_NAMES[row.plan] || row.plan}</div>
                <div className="text-xs text-neutral-500 mt-0.5">
                  активна до <span className="text-neutral-300">{fmtDateTime(row.expires_at)}</span>
                </div>
              </div>
              <Link
                href={`/clients/${row.user_id}`}
                className="px-3 py-1 rounded-md text-xs bg-neutral-800 text-neutral-200 hover:bg-neutral-700"
              >
                → на страницу клиента
              </Link>
            </div>
          </div>

          {/* Granted by admin (если admin_grant) */}
          {row.granted_by_admin_id && (
            <div className="bg-fuchsia-500/5 border border-fuchsia-500/20 rounded-2xl p-5">
              <div className="text-[10px] text-fuchsia-400 uppercase tracking-wider mb-1">🎁 Admin grant</div>
              <div className="text-sm">Выдал админ <span className="font-mono">#{row.granted_by_admin_id}</span></div>
            </div>
          )}
        </div>
      </div>

      {/* Payments-charges (raw payload по сути) */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800">
          <div className="font-semibold text-sm">Транзакции ({row.payments_count})</div>
          <div className="text-xs text-neutral-500 mt-0.5">
            Все строки в `payments` для этой подписки. На upgrade-сценариях здесь 2+ записей.
          </div>
        </div>
        {charges.length === 0 ? (
          <EmptyState title="нет связанных транзакций" description="старая legacy-подписка без payments-row" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wider text-neutral-500 bg-neutral-950/50">
              <tr>
                <th className="px-5 py-2 text-left">Когда</th>
                <th className="px-5 py-2 text-left">Метод</th>
                <th className="px-5 py-2 text-left">tx_id</th>
                <th className="px-5 py-2 text-right">Сумма</th>
                <th className="px-5 py-2 text-left">Статус</th>
              </tr>
            </thead>
            <tbody>
              {charges.map((c: PaymentChargeRow) => (
                <tr key={c.id} className="border-t border-neutral-800/80">
                  <td className="px-5 py-3 text-xs text-neutral-400 whitespace-nowrap">{fmtDateTime(c.created_at)}</td>
                  <td className="px-5 py-3 text-xs"><MethodLabel method={c.method} /></td>
                  <td className="px-5 py-3 text-[11px] font-mono text-neutral-500 max-w-[260px] truncate">{c.tx_id || '—'}</td>
                  <td className="px-5 py-3 text-right text-xs">
                    {c.amount_usd && c.amount_usd > 0
                      ? <span className="text-emerald-400">${c.amount_usd.toFixed(2)}</span>
                      : c.stars && c.stars > 0
                        ? <span className="text-yellow-400">⭐ {c.stars}</span>
                        : <span className="text-neutral-600">—</span>}
                  </td>
                  <td className="px-5 py-3 text-xs">
                    {c.refunded_at
                      ? <span className="text-rose-400">refunded</span>
                      : c.is_free_grant
                        ? <span className="text-fuchsia-400">grant</span>
                        : <span className="text-emerald-400">{c.status}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
