'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'

/**
 * Унифицированный nav-bar для всех админских страниц. Раньше каждая page.tsx
 * содержала свой копипаст-блок ссылок — забывал добавить новый пункт когда
 * появлялась новая страница, никто не видел в какой ты сейчас вкладке.
 *
 * Использование:
 *   <AdminNav username="…" slaBreaches={N} />
 *
 * `slaBreaches` — число тикетов где админ ещё не ответил >2ч (см.
 * slaBreachCount() в lib/db.ts). Рендерим badge рядом со ссылкой
 * «Обращения» когда > 0; иначе badge скрыт.
 *
 * Если prop не передан, AdminNav сам подтянет число через
 * /api/tickets/sla (client-side fetch). Это нужно для страниц других треков
 * (track A/B), куда нельзя добавить server-side вызов slaBreachCount().
 */
const NAV_ITEMS = [
  { href: '/',            label: 'Дашборд'    },
  { href: '/analytics',   label: 'Аналитика'  },
  { href: '/attribution', label: 'Трафик'     },
  { href: '/clients',     label: 'Клиенты'    },
  { href: '/payments',    label: 'Платежи'    },
  { href: '/grant',       label: 'Выдать'     },
  { href: '/monitoring',  label: 'Мониторинг' },
  { href: '/tickets',     label: 'Обращения'  },
  { href: '/servers',     label: 'Серверы'    },
]

export default function AdminNav({ username, slaBreaches }: { username?: string; slaBreaches?: number }) {
  const path = usePathname()

  // basePath = '/admin' → usePathname() возвращает уже без него
  // (Next.js normalizes), но иногда возвращает с ним — поддерживаем оба.
  const norm = (p: string) => p.replace(/^\/admin/, '') || '/'
  const current = norm(path ?? '/')

  // SLA breach count fallback: если caller не передал prop, тянем сами.
  // На /tickets prop приходит из server-компонента → fetch не делается,
  // нет flash-of-no-badge.
  const [fetched, setFetched] = useState<number | null>(null)
  useEffect(() => {
    if (typeof slaBreaches === 'number') return
    let cancelled = false
    fetch('/admin/api/tickets/sla', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(j => { if (!cancelled && j && typeof j.count === 'number') setFetched(j.count) })
      .catch(() => { /* silent — badge просто не покажется */ })
    return () => { cancelled = true }
  }, [slaBreaches])
  const effectiveSla = typeof slaBreaches === 'number' ? slaBreaches : (fetched ?? 0)

  return (
    <div className="flex items-center justify-between pt-2">
      <div>
        <div className="text-xl font-extrabold tracking-tight">MAX VPN &amp; eSIM</div>
        {/* min-h резервирует место под greeting даже если username не передан —
            иначе вертикальная высота navbar дёргалась бы между страницами,
            где username приходит (page/grant/payments/tickets/…) и где нет
            (clients/attribution/monitoring/servers до этого фикса). */}
        <div className="text-xs text-neutral-500 mt-0.5 min-h-[1rem]">
          {username ? `Привет, ${username}` : ' '}
        </div>
      </div>
      <div className="flex gap-4 items-center">
        {NAV_ITEMS.map(item => {
          // / должен матчить только /, остальные — точное совпадение или префикс /clients/123
          const active = item.href === '/'
            ? current === '/'
            : current === item.href || current.startsWith(item.href + '/')
          const showSla = item.href === '/tickets' && effectiveSla > 0
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`text-xs transition-colors inline-flex items-center gap-1.5 ${
                active
                  ? 'text-white font-semibold border-b-2 border-sky-500 pb-0.5'
                  : 'text-neutral-500 hover:text-neutral-300'
              }`}
            >
              {item.label}
              {showSla && (
                <span
                  // SLA-breach badge: тикеты где admin не ответил >2ч.
                  // tone=rose, малый высокий контраст — не теряется в навбаре,
                  // но и не давит. Скрыт когда 0 (см. showSla выше).
                  title={`${effectiveSla} ${effectiveSla === 1 ? 'тикет' : 'тикетов'} без ответа >2ч`}
                  className="px-1.5 py-0.5 rounded-full bg-rose-500/20 text-rose-300 text-[10px] font-semibold leading-none border border-rose-500/40"
                >
                  {effectiveSla}
                </span>
              )}
            </Link>
          )
        })}
        <a href="/admin/api/auth/logout" className="text-xs text-neutral-600 hover:text-rose-400 ml-2 pl-3 border-l border-neutral-800">
          Выход
        </a>
      </div>
    </div>
  )
}
