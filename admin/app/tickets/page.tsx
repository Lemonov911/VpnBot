import Link from 'next/link'
import { requireSession } from '@/lib/auth'
import {
  ticketsInbox,
  ticketById,
  ticketCategoriesOpen,
  slaBreachCount,
  ticketThread,
  type TicketInboxRow,
  type TicketThreadMessage,
} from '@/lib/db'
import { redirect } from 'next/navigation'
import TicketActions from './TicketActions'
import UserContextPanel from '../_components/UserContextPanel'
import AdminNav from '../_components/AdminNav'

/**
 * /tickets — inbox layout (Track C, C1).
 *
 * ┌──────────────┬──────────────────────────────┐
 * │ list         │ detail                       │
 * │ ─────────    │ ─────                        │
 * │ ticket card  │ ticket header + message      │
 * │ ticket card  │ reply form (TicketActions)   │
 * │   …          │ UserContextPanel (C2)        │
 * └──────────────┴──────────────────────────────┘
 *
 * Mobile (<md): single column — если ?id передан, показываем detail (+ кнопка
 * назад к списку), иначе только список.
 *
 * Сортировка: по COALESCE(last_admin_reply_at, created_at) DESC — самый свежий
 * сверху (см. ticketsInbox в lib/db.ts). Это inbox-style: что только что
 * ожило, то и наверху.
 *
 * Unread dot: status=open + админ ни разу не отвечал (по audit_log). Без
 * ticket_messages таблицы более точного сигнала «есть новое от юзера» нет.
 */

const CATEGORY_LABEL: Record<string, string> = {
  payment: '💳 Оплата',
  technical: '🔧 Технические',
  refund: '↩ Возврат',
  account: '👤 Аккаунт',
  connection: '🔌 Подключение',
  other: '📝 Другое',
}

function categoryLabel(c: string): string {
  return CATEGORY_LABEL[c] ?? c
}

function fmtDateTime(iso: string): string {
  const d = new Date(iso.replace(' ', 'T'))
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

/**
 * Относительное время "5м назад" / "2ч назад" / "1д" — для списка.
 * Полная дата идёт title-tooltip'ом.
 */
function relTime(iso: string): string {
  const ts = new Date(iso.replace(' ', 'T')).getTime()
  if (!Number.isFinite(ts)) return '—'
  const diffSec = (Date.now() - ts) / 1000
  if (diffSec < 60)      return 'только что'
  if (diffSec < 3600)    return `${Math.floor(diffSec / 60)}м`
  if (diffSec < 86400)   return `${Math.floor(diffSec / 3600)}ч`
  if (diffSec < 604800)  return `${Math.floor(diffSec / 86400)}д`
  return fmtDateTime(iso)
}

function truncate(s: string, n: number): string {
  if (!s) return ''
  const flat = s.replace(/\s+/g, ' ').trim()
  if (flat.length <= n) return flat
  return flat.slice(0, n - 1) + '…'
}

/** Сохраняем текущие query params при смене ?id, чтобы фильтры не сбрасывались. */
function buildHref(params: { id?: number; status: string; category?: string }) {
  const sp = new URLSearchParams()
  if (params.status !== 'open') sp.set('status', params.status)
  if (params.category) sp.set('category', params.category)
  if (params.id != null) sp.set('id', String(params.id))
  const qs = sp.toString()
  return qs ? `/tickets?${qs}` : '/tickets'
}

function StatusPill({ status }: { status: string }) {
  const cls =
    status === 'open'   ? 'text-amber-400 bg-amber-500/10 border-amber-500/30' :
    status === 'closed' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' :
                          'text-neutral-400 bg-neutral-500/10 border-neutral-700'
  return (
    <span className={`px-1.5 py-0.5 rounded-full text-[10px] border ${cls}`}>
      {status}
    </span>
  )
}

function TicketCard({
  ticket,
  selected,
  status,
  category,
}: {
  ticket: TicketInboxRow
  selected: boolean
  status: string
  category?: string
}) {
  const href = buildHref({ id: ticket.id, status, category })
  return (
    <Link
      href={href}
      className={`block px-4 py-3 transition-colors ${
        selected
          ? 'bg-sky-500/10 border-l-2 border-l-sky-500'
          : 'hover:bg-neutral-800/50 border-l-2 border-l-transparent'
      }`}
    >
      <div className="flex items-start gap-2">
        {/* Unread dot: open ticket + нет admin reply. Полупрозрачный кружок
            слева, как в почтовых клиентах. */}
        <span
          className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${
            ticket.unread === 1 ? 'bg-sky-400' : 'bg-transparent'
          }`}
          aria-label={ticket.unread === 1 ? 'без ответа админа' : ''}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2 mb-1">
            <div className="text-sm font-medium text-neutral-200 truncate">
              {ticket.first_name || 'unknown'}
              {ticket.username && (
                <span className="text-neutral-500 font-normal"> @{ticket.username}</span>
              )}
            </div>
            <div
              className="text-[10px] text-neutral-500 shrink-0"
              title={fmtDateTime(ticket.sort_at)}
            >
              {relTime(ticket.sort_at)}
            </div>
          </div>
          <div className="flex items-center gap-2 mb-1 text-[10px]">
            <span className="font-mono text-neutral-500">#{ticket.id}</span>
            <span className="text-neutral-500">{categoryLabel(ticket.category)}</span>
            {ticket.status !== 'open' && <StatusPill status={ticket.status} />}
          </div>
          <div className={`text-xs truncate ${ticket.unread === 1 ? 'text-neutral-200 font-medium' : 'text-neutral-500'}`}>
            {truncate(ticket.message || '(пустое сообщение)', 80)}
          </div>
        </div>
      </div>
    </Link>
  )
}

function FilterTab({ k, label, status, category }: { k: string; label: string; status: string; category?: string }) {
  const active = status === k
  const href = buildHref({ status: k, category })
  return (
    <Link
      href={href}
      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        active ? 'bg-neutral-800 text-white' : 'text-neutral-500 hover:text-neutral-300'
      }`}
    >
      {label}
    </Link>
  )
}

function CategoryFilter({
  status,
  category,
  categories,
}: {
  status: string
  category?: string
  categories: Array<{ category: string; count: number }>
}) {
  if (categories.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1">
      <Link
        href={buildHref({ status, category: undefined })}
        className={`px-2 py-1 rounded-md text-[10px] transition-colors ${
          !category ? 'bg-neutral-800 text-neutral-200' : 'text-neutral-500 hover:text-neutral-300'
        }`}
      >
        все
      </Link>
      {categories.map(c => (
        <Link
          key={c.category}
          href={buildHref({ status, category: c.category })}
          className={`px-2 py-1 rounded-md text-[10px] transition-colors ${
            category === c.category ? 'bg-neutral-800 text-neutral-200' : 'text-neutral-500 hover:text-neutral-300'
          }`}
        >
          {categoryLabel(c.category)} · {c.count}
        </Link>
      ))}
    </div>
  )
}

/**
 * HH:MM — для bubble-метки. День определяется по группировке (всегда показываем
 * полную дату в title-tooltip'е если нужно). created_at SQLite формата
 * "YYYY-MM-DD HH:MM:SS" → нормализуем replace(' ', 'T').
 */
function fmtBubbleTime(iso: string): string {
  const d = new Date(iso.replace(' ', 'T'))
  if (!Number.isFinite(d.getTime())) return iso
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Chat-style timeline. User bubbles слева (neutral), admin справа (sky tinted).
 * Дизайн mimic'ает Telegram/iMessage: непрерывный flow, метки маленькие
 * сверху каждой bubble (sender + время).
 */
function TicketThread({ messages }: { messages: TicketThreadMessage[] }) {
  if (messages.length === 0) {
    return <div className="text-xs text-neutral-500 italic">нет сообщений</div>
  }
  return (
    <div className="space-y-2">
      {messages.map((m, i) => {
        const isAdmin = m.sender === 'admin'
        return (
          <div key={i} className={`flex ${isAdmin ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-2xl px-3.5 py-2 ${
                isAdmin
                  ? 'bg-sky-500/15 border border-sky-500/30 rounded-tr-md'
                  : 'bg-neutral-800 border border-neutral-700 rounded-tl-md'
              }`}
            >
              <div className="text-[10px] text-neutral-500 mb-0.5">
                {isAdmin
                  ? `Админ${m.admin_id ? ' ' + m.admin_id : ''}`
                  : 'Юзер'}
                {' · '}
                <span title={fmtDateTime(m.created_at)}>{fmtBubbleTime(m.created_at)}</span>
              </div>
              <div className="text-sm whitespace-pre-wrap leading-relaxed text-neutral-100">
                {m.text || <span className="text-neutral-500 italic">пустое сообщение</span>}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function TicketDetail({
  ticket,
  thread,
  status,
  category,
}: {
  ticket: TicketInboxRow
  thread: TicketThreadMessage[]
  status: string
  category?: string
}) {
  return (
    <div className="space-y-4">
      {/* Mobile-only back link */}
      <Link
        href={buildHref({ status, category })}
        className="md:hidden inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-white"
      >
        ← к списку
      </Link>

      <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-3">
        <div className="flex items-baseline justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2 text-xs flex-wrap">
            <span className="font-mono text-neutral-500">#{ticket.id}</span>
            <span className="text-neutral-400">{categoryLabel(ticket.category)}</span>
            <StatusPill status={ticket.status} />
          </div>
          <div className="text-[11px] text-neutral-500">
            создан {fmtDateTime(ticket.created_at)}
          </div>
        </div>

        <div className="text-sm font-medium text-neutral-200">
          {ticket.first_name || 'unknown'}
          {ticket.username && <span className="text-neutral-500"> @{ticket.username}</span>}
          <span className="text-[10px] text-neutral-600 font-mono ml-2">id {ticket.user_id}</span>
        </div>

        {/* Thread: initial user message + admin replies из audit_log (см.
            ticketThread в lib/db.ts). Без отдельной ticket_messages-таблицы —
            пока follow-up юзер-сообщения создают новые тикеты, в thread'е они
            не появятся. Это OK для MVP, когда подключим follow-up handler в
            боте — здесь поменяем источник, UI останется тот же. */}
        <TicketThread messages={thread} />

        {ticket.admin_msg_id && (
          <div className="text-[10px] text-neutral-600">
            forwarded to admin chat (msg #{ticket.admin_msg_id})
          </div>
        )}

        {ticket.status === 'open' ? (
          <TicketActions ticketId={ticket.id} userId={ticket.user_id} />
        ) : (
          <div className="text-xs text-neutral-500 italic">тикет закрыт — повторно открыть нельзя</div>
        )}
      </div>

      <UserContextPanel userId={ticket.user_id} />
    </div>
  )
}

export default async function Tickets({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; category?: string; id?: string }>
}) {
  const session = await requireSession()
  if (!session) redirect('/login')

  const params = await searchParams
  const status: 'open' | 'closed' | 'all' =
    params.status === 'closed' ? 'closed' :
    params.status === 'all'    ? 'all'    :
    'open'
  const category = params.category && params.category.length > 0 ? params.category : undefined
  const selectedId = params.id ? parseInt(params.id, 10) : NaN

  const tickets    = ticketsInbox({ status, category, limit: 200 })
  const categories = ticketCategoriesOpen()
  const sla        = slaBreachCount()

  // Если ?id передан, ищем тикет — сначала в текущем списке (быстро), потом
  // одиночным запросом если не нашли (мог быть отфильтрован).
  let selected: TicketInboxRow | null = null
  if (Number.isFinite(selectedId)) {
    selected = tickets.find(t => t.id === selectedId)
            ?? ticketById(selectedId)
  }

  // Thread грузим только для выбранного тикета — две дешёвые query (PK lookup
  // + audit_log по target). Если ничего не выбрано — пустой массив.
  const thread: TicketThreadMessage[] = selected ? ticketThread(selected.id) : []

  return (
    <div className="min-h-screen p-6 max-w-7xl mx-auto space-y-4">
      <AdminNav username={session.username} slaBreaches={sla} />

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-1">
          <FilterTab k="open"   label="Открытые" status={status} category={category} />
          <FilterTab k="closed" label="Закрытые" status={status} category={category} />
          <FilterTab k="all"    label="Все"      status={status} category={category} />
        </div>
        <div className="text-[11px] text-neutral-500">
          {tickets.length} {tickets.length === 1 ? 'тикет' : 'тикетов'}
          {sla > 0 && (
            <span className="text-rose-400 ml-2">
              · {sla} без ответа &gt;2ч
            </span>
          )}
        </div>
      </div>

      {status === 'open' && (
        <CategoryFilter status={status} category={category} categories={categories} />
      )}

      <div className="grid md:grid-cols-[360px_1fr] gap-4">
        {/* Список — скрыт на mobile если выбран ticket */}
        <div
          className={`bg-neutral-900 border border-neutral-800 rounded-2xl overflow-hidden h-fit ${
            selected ? 'hidden md:block' : 'block'
          }`}
        >
          {tickets.length === 0 ? (
            <div className="px-5 py-12 text-center text-sm text-neutral-500">
              нет тикетов с этим фильтром
            </div>
          ) : (
            <div className="divide-y divide-neutral-800 max-h-[calc(100vh-220px)] overflow-y-auto">
              {tickets.map(t => (
                <TicketCard
                  key={t.id}
                  ticket={t}
                  selected={selected?.id === t.id}
                  status={status}
                  category={category}
                />
              ))}
            </div>
          )}
        </div>

        {/* Detail — скрыт на mobile если ticket не выбран */}
        <div className={`${selected ? 'block' : 'hidden md:block'}`}>
          {selected ? (
            <TicketDetail ticket={selected} thread={thread} status={status} category={category} />
          ) : (
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl px-5 py-12 text-center text-sm text-neutral-500">
              {tickets.length > 0
                ? 'выбери тикет слева'
                : 'тикетов нет'}
            </div>
          )}
        </div>
      </div>

      <div className="text-xs text-neutral-600 text-center">
        💡 Можно ответить прямо отсюда — сообщение уйдёт юзеру от имени бота. Или в Telegram (reply на forward).
      </div>
    </div>
  )
}
