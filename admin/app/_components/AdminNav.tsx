'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

/**
 * Унифицированный nav-bar для всех админских страниц. Раньше каждая page.tsx
 * содержала свой копипаст-блок ссылок — забывал добавить новый пункт когда
 * появлялась новая страница, никто не видел в какой ты сейчас вкладке.
 *
 * Использование:
 *   <AdminNav username="…" />
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

export default function AdminNav({ username }: { username?: string }) {
  const path = usePathname()

  // basePath = '/admin' → usePathname() возвращает уже без него
  // (Next.js normalizes), но иногда возвращает с ним — поддерживаем оба.
  const norm = (p: string) => p.replace(/^\/admin/, '') || '/'
  const current = norm(path ?? '/')

  return (
    <div className="flex items-center justify-between pt-2">
      <div>
        <div className="text-xl font-extrabold tracking-tight">MAX VPN &amp; eSIM</div>
        {username && <div className="text-xs text-neutral-500 mt-0.5">Привет, {username}</div>}
      </div>
      <div className="flex gap-4 items-center">
        {NAV_ITEMS.map(item => {
          // / должен матчить только /, остальные — точное совпадение или префикс /clients/123
          const active = item.href === '/'
            ? current === '/'
            : current === item.href || current.startsWith(item.href + '/')
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`text-xs transition-colors ${
                active
                  ? 'text-white font-semibold border-b-2 border-sky-500 pb-0.5'
                  : 'text-neutral-500 hover:text-neutral-300'
              }`}
            >
              {item.label}
            </Link>
          )
        })}
        <a href="/api/auth/logout" className="text-xs text-neutral-600 hover:text-rose-400 ml-2 pl-3 border-l border-neutral-800">
          Выход
        </a>
      </div>
    </div>
  )
}
