'use client'

import { useEffect, useRef, useState } from 'react'
import { TICKET_TEMPLATES } from './ticket-templates.data'

/**
 * Dropdown с быстрыми шаблонами ответа. По клику зовёт onPick(body) —
 * родительский TicketActions подставляет текст в textarea (controlled state),
 * после чего админ может отредактировать перед отправкой.
 *
 * Закрывается:
 *   - по клику вне дропдауна (ref + mousedown listener)
 *   - по Esc
 *   - после выбора шаблона
 */
export default function TicketTemplates({ onPick }: { onPick: (body: string) => void }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    // Чтобы не повесить лишний listener при закрытом меню — подписываемся
    // только когда open=true. И обязательно cleanup, иначе при unmount
    // дропдауна (после reply form close) лиснер остаётся висеть.
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="px-3 py-1.5 rounded-md bg-neutral-800 text-neutral-300 text-xs font-medium hover:bg-neutral-700 transition-colors inline-flex items-center gap-1.5"
      >
        Шаблоны
        <span className={`text-[8px] transition-transform ${open ? 'rotate-180' : ''}`}>▼</span>
      </button>
      {open && (
        <div className="absolute right-0 bottom-full mb-1 z-20 w-64 rounded-lg border border-neutral-800 bg-neutral-950 shadow-lg overflow-hidden">
          <ul className="divide-y divide-neutral-800">
            {TICKET_TEMPLATES.map(tpl => (
              <li key={tpl.id}>
                <button
                  type="button"
                  onClick={() => {
                    onPick(tpl.body)
                    setOpen(false)
                  }}
                  className="w-full text-left px-3 py-2 text-xs text-neutral-300 hover:bg-neutral-900 hover:text-white transition-colors"
                >
                  {tpl.label}
                </button>
              </li>
            ))}
          </ul>
          <div className="px-3 py-1.5 text-[10px] text-neutral-600 border-t border-neutral-800 bg-neutral-950">
            Текст можно отредактировать перед отправкой
          </div>
        </div>
      )}
    </div>
  )
}
