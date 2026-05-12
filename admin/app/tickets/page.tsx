import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import { allTicketsWithUser } from '@/lib/db'
import { redirect } from 'next/navigation'

const CATEGORY: Record<string, string> = {
  payment: '💳 Оплата',
  technical: '🔧 Технические',
  refund: '↩ Возврат',
  account: '👤 Аккаунт',
  other: '📝 Другое',
}

function fmtDateTime(iso: string) {
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default async function Tickets({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>
}) {
  const session = await requireSession()
  if (!session) redirect('/login')

  const params = await searchParams
  const filter = params.status === 'closed' ? 'closed' : params.status === 'all' ? undefined : 'open'
  const tickets = allTicketsWithUser(100, filter)

  const TabLink = ({ k, label }: { k: string; label: string }) => {
    const active =
      (k === 'open' && filter === 'open') ||
      (k === 'closed' && filter === 'closed') ||
      (k === 'all' && filter === undefined)
    return (
      <Link
        href={`/tickets?status=${k}`}
        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
          active ? 'bg-neutral-800 text-white' : 'text-neutral-500 hover:text-neutral-300'
        }`}
      >
        {label}
      </Link>
    )
  }

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between pt-2">
        <div>
          <div className="text-xl font-extrabold tracking-tight">Обращения</div>
          <div className="text-xs text-neutral-500 mt-0.5">Тикеты от пользователей</div>
        </div>
        <div className="flex gap-4 items-center">
          <Link href="/"           className="text-xs text-neutral-500 hover:text-neutral-300">Дашборд</Link>
          <Link href="/analytics"  className="text-xs text-neutral-500 hover:text-neutral-300">Аналитика</Link>
          <Link href="/clients"    className="text-xs text-neutral-500 hover:text-neutral-300">Клиенты</Link>
          <Link href="/monitoring" className="text-xs text-neutral-500 hover:text-neutral-300">Мониторинг</Link>
          <Link href="/servers"    className="text-xs text-neutral-500 hover:text-neutral-300">Серверы</Link>
        </div>
      </div>

      <div className="flex gap-2">
        <TabLink k="open"   label="Открытые" />
        <TabLink k="closed" label="Закрытые" />
        <TabLink k="all"    label="Все" />
      </div>

      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden">
        {tickets.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-neutral-500">
            нет тикетов с этим фильтром
          </div>
        ) : (
          <div className="divide-y divide-neutral-800">
            {tickets.map(t => (
              <div key={t.id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2 flex-wrap text-xs">
                    <span className="font-mono text-neutral-500">#{t.id}</span>
                    <span className="text-neutral-500">{CATEGORY[t.category] || t.category}</span>
                    <span className={
                      t.status === 'open' ? 'text-amber-400'
                      : t.status === 'closed' ? 'text-emerald-500'
                      : 'text-neutral-500'
                    }>
                      ● {t.status}
                    </span>
                  </div>
                  <div className="text-[10px] text-neutral-500 shrink-0">{fmtDateTime(t.created_at)}</div>
                </div>
                <div className="text-sm font-medium text-neutral-200 mb-1.5">
                  {t.first_name || 'unknown'}
                  {t.username && <span className="text-neutral-500"> @{t.username}</span>}
                  <span className="text-[10px] text-neutral-600 font-mono ml-2">id {t.user_id}</span>
                </div>
                <div className="text-sm text-neutral-300 whitespace-pre-wrap leading-relaxed">
                  {t.message || <span className="text-neutral-600 italic">пустое сообщение</span>}
                </div>
                {t.admin_msg_id && (
                  <div className="text-[10px] text-neutral-600 mt-2">
                    forwarded to admin chat (msg #{t.admin_msg_id})
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="text-xs text-neutral-600 text-center">
        💡 Ответ на тикет: в Telegram chat → reply на forward'нутое сообщение → ответ улетит юзеру в чат бота.
      </div>
    </div>
  )
}
