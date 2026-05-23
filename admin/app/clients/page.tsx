import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { topClients, moneyTotals } from '@/lib/db'
import { redirect } from 'next/navigation'
import AdminNav from '../_components/AdminNav'
import { StatCard, Table, type Column } from '../_components/ui'

// Тип строки из topClients(): дёргаем форму руками, чтобы Column<TopClientRow>
// был строго типизирован. Поля должны совпадать с SELECT в lib/db.ts → topClients.
type TopClientRow = {
  id: number
  first_name: string | null
  username: string | null
  joined_at: string
  total_stars: number
  total_rub: number
  paid_subs: number
  trial_subs: number
  current_plan: string | null
  current_plan_status: string | null
  active_until: string | null
  last_purchase: string | null
}

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

const AVATAR_COLORS = [
  'bg-blue-800 text-blue-200',
  'bg-violet-800 text-violet-200',
  'bg-emerald-800 text-emerald-200',
  'bg-orange-800 text-orange-200',
  'bg-sky-800 text-sky-200',
  'bg-rose-800 text-rose-200',
]
function Avatar({ name, id }: { name: string; id: number }) {
  const letter = (name || '?')[0].toUpperCase()
  const cls = AVATAR_COLORS[id % AVATAR_COLORS.length]
  return (
    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${cls}`}>
      {letter}
    </div>
  )
}

export default async function Clients() {
  const session = await requireSession()
  if (!session) redirect('/login')

  const clients = topClients(50)
  const m = moneyTotals()
  // AD-F16: the "Средний LTV" card mixed channels — labelled with ⭐ but
  // dividing only stars_paid by paying_users (which counts RUB-payers too).
  // Now we show Stars-LTV and RUB-LTV separately, both clearly scoped.
  const avgLtvStars = m.paying_users > 0 ? Math.round(m.total_revenue_stars / m.paying_users) : 0
  const avgLtvRub   = m.paying_users > 0 ? Math.round(m.total_revenue_rub   / m.paying_users) : 0
  // Распределение по top-X
  const top10Sum = clients.slice(0, 10).reduce((a, b) => a + b.total_stars, 0)
  const top10Share = m.total_revenue_stars > 0 ? Math.round((top10Sum / m.total_revenue_stars) * 100) : 0

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-8">
      <AdminNav />

      {/* KPIs. AD-F16: split LTV across Stars and RUB channels — the prior
          single "⭐ LTV" card divided Stars-only revenue by all payers, which
          deflated the number whenever RUB-only customers paid. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Платящих юзеров"   value={m.paying_users} hint={`⭐${m.total_revenue_stars} · ₽${m.total_revenue_rub}`} />
        <StatCard label="Средний LTV ⭐"     value={`⭐ ${avgLtvStars}`} hint="stars_paid / payers (Stars-only metric)" />
        <StatCard label="Средний LTV ₽"     value={`₽ ${avgLtvRub.toLocaleString('ru')}`} hint="amount_rub / payers (RUB channel)" />
        <StatCard label="Повторных покупок"  value={m.repeat_buyers} hint="≥ 2 платных подписки" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Топ-10 = доля"      value={`${top10Share}%`} hint="от Stars-выручки" />
      </div>

      {/* Period buckets */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="⭐ за 7 дней"  value={m.revenue_7d} />
        <StatCard label="⭐ за 30 дней" value={m.revenue_30d} />
        <StatCard label="⭐ за 90 дней" value={m.revenue_90d} />
      </div>

      {/* Ranking — пилотный <Table>. Индекс # нужен для нумерации,
          поэтому держим map'ом снаружи, чтобы получить i. */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-800 flex items-baseline justify-between">
          <div className="font-semibold text-sm">Топ-50 клиентов</div>
          <div className="text-xs text-neutral-500">по сумме потраченных Stars (всё время)</div>
        </div>
        <Table<TopClientRow & { _rank: number }>
          rows={clients.map((c, i) => ({ ...(c as TopClientRow), _rank: i + 1 }))}
          rowKey={c => c.id}
          emptyText="пока никто не платил"
          columns={clientsColumns}
        />
      </div>
    </div>
  )
}

// Колонки таблицы — вынесены чтобы page-функция была компактнее.
// Тип `TopClientRow & { _rank: number }` потому что номер строки (#)
// мы вычисляем в map'е выше — Table сам не знает индекса.
const clientsColumns: Column<TopClientRow & { _rank: number }>[] = [
  {
    key: 'rank',
    label: '#',
    align: 'left',
    className: 'w-8 text-neutral-600 font-mono text-xs',
    render: c => c._rank,
  },
  {
    key: 'user',
    label: 'Юзер',
    align: 'left',
    render: c => (
      <div className="flex items-start gap-2.5">
        <Avatar name={c.first_name || '?'} id={c.id} />
        <div>
          <Link href={`/clients/${c.id}`} className="block hover:text-sky-400">
            <div className="font-medium truncate max-w-[180px]">
              {c.first_name || 'unknown'}
            </div>
            <div className="text-[10px] text-neutral-600 font-mono">id {c.id}</div>
          </Link>
          {c.username && (
            <a
              href={`https://t.me/${c.username}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 mt-1 px-1.5 py-0.5 rounded text-[10px] bg-sky-500/10 text-sky-400 border border-sky-500/20 hover:bg-sky-500/20 transition-colors"
            >
              ↗ @{c.username}
            </a>
          )}
        </div>
      </div>
    ),
  },
  {
    key: 'ltv',
    label: '⭐ LTV',
    align: 'right',
    className: 'font-semibold text-yellow-400',
    render: c => <>⭐ {c.total_stars}</>,
  },
  {
    key: 'paid_subs',
    label: 'Покупок',
    align: 'right',
    render: c => (
      <>
        {c.paid_subs}
        {c.trial_subs > 0 && (
          <span className="text-neutral-600 text-xs"> + {c.trial_subs} тр</span>
        )}
      </>
    ),
  },
  {
    key: 'current_plan',
    label: 'Сейчас на',
    align: 'left',
    render: c =>
      c.current_plan ? (
        <span className={c.current_plan_status === 'grace' ? 'text-amber-400' : 'text-emerald-400'}>
          {PLAN_NAMES[c.current_plan] || c.current_plan}
          {c.current_plan_status === 'grace' && (
            <span className="ml-1 text-[10px] uppercase tracking-wider">grace</span>
          )}
        </span>
      ) : (
        <span className="text-neutral-600">—</span>
      ),
  },
  {
    key: 'active_until',
    label: 'Истекает',
    align: 'left',
    className: 'text-neutral-400 text-xs',
    render: c => fmtDate(c.active_until),
  },
  {
    key: 'last_purchase',
    label: 'Last buy',
    align: 'left',
    className: 'text-neutral-500 text-xs',
    render: c => fmtDate(c.last_purchase),
  },
]
