'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

/**
 * Reply form для тикета. Шлёт текст в bot REST, бот отправляет юзеру в чат
 * от имени @maxvpnesim_bot. По умолчанию после ответа тикет закрывается —
 * если нужно оставить открытым (продолжить диалог), снять чекбокс.
 */
export default function TicketActions({ ticketId, userId }: { ticketId: number; userId: number }) {
  const router = useRouter()
  const [text,    setText]    = useState('')
  const [close,   setClose]   = useState(true)
  const [sending, setSending] = useState(false)
  const [err,     setErr]     = useState('')
  const [done,    setDone]    = useState(false)
  const [showForm, setShowForm] = useState(false)

  const reply = async () => {
    setErr('')
    setSending(true)
    try {
      const res = await fetch(`/api/tickets/${ticketId}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.trim(), close }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
      setDone(true)
      // Через секунду — refresh page (или router refresh для SSR)
      setTimeout(() => router.refresh(), 700)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
    }
  }

  const closeOnly = async () => {
    if (!confirm('Закрыть тикет без ответа?')) return
    setSending(true)
    try {
      const res = await fetch(`/api/tickets/${ticketId}/close`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      router.refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setSending(false)
    }
  }

  if (done) {
    return (
      <div className="text-xs text-emerald-500 px-3 py-2 bg-emerald-500/10 rounded-md">
        ✓ Ответ отправлен{close ? ' · тикет закрыт' : ''}
      </div>
    )
  }

  if (!showForm) {
    return (
      <div className="flex gap-2 text-xs">
        <button
          onClick={() => setShowForm(true)}
          className="px-3 py-1.5 rounded-md bg-sky-500/15 text-sky-400 hover:bg-sky-500/25 transition-colors font-medium"
        >
          ✍ Ответить
        </button>
        <button
          onClick={closeOnly}
          disabled={sending}
          className="px-3 py-1.5 rounded-md bg-neutral-800 text-neutral-400 hover:text-neutral-200 transition-colors"
        >
          Закрыть без ответа
        </button>
        <span className="text-[10px] text-neutral-600 self-center ml-2">
          → юзер id <span className="font-mono">{userId}</span>
        </span>
      </div>
    )
  }

  return (
    <div className="bg-neutral-950 border border-neutral-800 rounded-lg p-3 space-y-2">
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        rows={4}
        maxLength={4000}
        placeholder="Ответ юзеру (придёт в чат с @maxvpnesim_bot)…"
        className="w-full px-3 py-2 bg-neutral-900 border border-neutral-800 rounded-md text-sm text-neutral-200 placeholder:text-neutral-600 outline-none focus:border-sky-500/50 resize-none"
        autoFocus
      />
      <div className="flex items-center justify-between gap-2 text-xs">
        <label className="flex items-center gap-1.5 text-neutral-400 cursor-pointer">
          <input type="checkbox" checked={close} onChange={e => setClose(e.target.checked)} />
          Закрыть после отправки
        </label>
        <span className="text-neutral-600">{text.length} / 4000</span>
      </div>
      {err && <div className="text-xs text-rose-400">⚠ {err}</div>}
      <div className="flex gap-2">
        <button
          onClick={reply}
          disabled={sending || !text.trim()}
          className="flex-1 px-3 py-2 rounded-md bg-sky-500 text-white text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-sky-600 transition-colors"
        >
          {sending ? 'Отправляю…' : 'Отправить юзеру'}
        </button>
        <button
          onClick={() => { setShowForm(false); setText(''); setErr('') }}
          disabled={sending}
          className="px-3 py-2 rounded-md bg-neutral-800 text-neutral-400 text-xs font-medium hover:text-neutral-200"
        >
          Отмена
        </button>
      </div>
    </div>
  )
}
