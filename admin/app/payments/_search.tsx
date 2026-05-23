'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useState, useTransition, type FormEvent } from 'react'

/**
 * B1: search bar для /payments. Client-component потому что page.tsx —
 * RSC (server-side prepared query), а нам нужен controlled-input + push
 * на новый URL.
 *
 * Поведение:
 *   - submit → router.push с обновлённым ?q=<value>; остальные query-params
 *     (?method=, ?plan=, ?days=, ?hideRefunds=) сохраняются.
 *   - пустая строка → ?q убирается.
 *
 * UI намеренно простой: подсказку про accepted-формат держим в placeholder
 * (а не отдельным help-text'ом — место экономим, фильтры под ним и так
 * перегружены).
 */
export function PaymentsSearchBar({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial)
  const [isPending, startTransition] = useTransition()
  const router = useRouter()
  const sp = useSearchParams()

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const params = new URLSearchParams(sp?.toString() ?? '')
    const trimmed = value.trim()
    if (trimmed) params.set('q', trimmed)
    else        params.delete('q')
    const qs = params.toString()
    startTransition(() => {
      // basePath /admin прибавляется Next.js при server-side, но client-side
      // router.push нужен относительный путь без префикса.
      router.push(qs ? `/payments?${qs}` : '/payments')
    })
  }

  const clear = () => {
    setValue('')
    const params = new URLSearchParams(sp?.toString() ?? '')
    params.delete('q')
    const qs = params.toString()
    startTransition(() => {
      router.push(qs ? `/payments?${qs}` : '/payments')
    })
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <input
        type="text"
        value={value}
        onChange={e => setValue(e.target.value)}
        placeholder="Поиск: user_id, username, payment_id или префикс tx_id"
        maxLength={200}
        className="flex-1 px-3 py-2 rounded-md text-sm bg-neutral-900 border border-neutral-800
                   text-white placeholder:text-neutral-600 focus:outline-none focus:border-sky-500/50"
      />
      {initial && (
        <button
          type="button"
          onClick={clear}
          className="px-3 py-2 rounded-md text-xs bg-neutral-800 text-neutral-400 hover:text-white"
        >
          ✕
        </button>
      )}
      <button
        type="submit"
        disabled={isPending}
        className="px-4 py-2 rounded-md text-sm bg-sky-600 text-white hover:bg-sky-500 disabled:opacity-50"
      >
        {isPending ? '…' : 'Найти'}
      </button>
    </form>
  )
}
