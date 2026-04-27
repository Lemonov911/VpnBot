import { useState, useRef, useEffect } from 'react'
import WebApp from '@twa-dev/sdk'
import { useLang, type Lang } from '../i18n'

const OPTIONS: { value: Lang; label: string; flag: string }[] = [
  { value: 'ru', label: 'RU', flag: '🇷🇺' },
  { value: 'en', label: 'EN', flag: '🇬🇧' },
]

export default function LangSwitch() {
  const { lang, setLang } = useLang()
  const tp = WebApp.themeParams
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
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 4,
          padding: '5px 10px', borderRadius: 20, border: 'none',
          background: 'var(--section-bg)',
          color: tp.text_color, fontSize: 12, fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        <span>{current.flag}</span>
        <span>{current.label}</span>
        <span style={{
          fontSize: 9, opacity: 0.45,
          transform: open ? 'rotate(180deg)' : 'none',
          transition: 'transform 0.15s', display: 'inline-block',
        }}>▼</span>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '110%', right: 0,
          background: tp.bg_color ?? '#1c1c1e',
          borderRadius: 12, overflow: 'hidden',
          boxShadow: '0 4px 20px rgba(0,0,0,0.25)',
          border: '1px solid rgba(128,128,128,0.15)',
          minWidth: 110, zIndex: 50,
        }}>
          {OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => { setLang(opt.value); setOpen(false); WebApp.HapticFeedback.selectionChanged() }}
              style={{
                width: '100%', padding: '11px 14px', border: 'none',
                background: opt.value === lang ? 'rgba(36,129,204,0.12)' : 'transparent',
                color: opt.value === lang ? 'var(--tg-theme-button-color,#2481cc)' : tp.text_color,
                fontSize: 13, fontWeight: opt.value === lang ? 700 : 400,
                cursor: 'pointer', textAlign: 'left',
                display: 'flex', alignItems: 'center', gap: 8,
              }}
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
