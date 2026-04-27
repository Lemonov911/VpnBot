import { useState, useRef, useEffect } from 'react'
import WebApp from '@twa-dev/sdk'
import { useLang, type Lang } from '../i18n'

const OPTIONS: { value: Lang; label: string; flag: string }[] = [
  { value: 'ru', label: 'RU', flag: '🇷🇺' },
  { value: 'en', label: 'EN', flag: '🇬🇧' },
]

export default function LangSwitch() {
  const { lang, setLang } = useLang()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const current = OPTIONS.find(o => o.value === lang)!

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 px-2.5 py-[5px] rounded-full border-none tg-section text-xs font-semibold cursor-pointer text-[var(--tg-theme-text-color)]"
      >
        <span>{current.flag}</span>
        <span>{current.label}</span>
        <span className={`inline-block text-[9px] opacity-45 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}>▼</span>
      </button>

      {open && (
        <div className="absolute top-[110%] right-0 rounded-xl overflow-hidden shadow-[0_4px_20px_rgba(0,0,0,0.25)] border border-[rgba(128,128,128,0.15)] min-w-[110px] z-50 bg-[var(--tg-theme-bg-color,#1c1c1e)]">
          {OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => { setLang(opt.value); setOpen(false); WebApp.HapticFeedback.selectionChanged() }}
              className={`w-full px-3.5 py-[11px] border-none text-left cursor-pointer flex items-center gap-2 text-[13px] ${
                opt.value === lang
                  ? 'bg-[rgba(36,129,204,0.12)] text-[var(--tg-theme-button-color,#2481cc)] font-bold'
                  : 'bg-transparent text-[var(--tg-theme-text-color)] font-normal'
              }`}
            >
              <span>{opt.flag}</span>
              <span>{opt.value === 'ru' ? 'Русский' : 'English'}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}