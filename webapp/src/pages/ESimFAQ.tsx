import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

function FAQGroup() {
  const [open, setOpen] = useState<number | null>(null)
  const t = useT()

  const FAQ: { q: string; a: string }[] = [
    { q: t('esim_faq_q1'), a: t('esim_faq_a1') },
    { q: t('esim_faq_q2'), a: t('esim_faq_a2') },
    { q: t('esim_faq_q3'), a: t('esim_faq_a3') },
    { q: t('esim_faq_q4'), a: t('esim_faq_a4') },
    { q: t('esim_faq_q5'), a: t('esim_faq_a5') },
    { q: t('esim_faq_q6'), a: t('esim_faq_a6') },
    { q: t('esim_faq_q7'), a: t('esim_faq_a7') },
    { q: t('esim_faq_q8'), a: t('esim_faq_a8') },
  ]

  return (
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
      {FAQ.map(({ q, a }, i) => (
        <div key={i}>
          <button
            onClick={() => { setOpen(open === i ? null : i); WebApp.HapticFeedback.selectionChanged() }}
            className={`w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-start gap-[14px] ${(open === i || i < FAQ.length - 1) ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
          >
            <div className="w-9 h-9 rounded-[10px] shrink-0 bg-primary/12 flex items-start justify-center">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="var(--tg-theme-button-color,#2481cc)" strokeWidth="2"/>
                <path d="M12 8c0-1.1.9-2 2-2s2 .9 2 2c0 1.5-2 2-2 3" stroke="var(--tg-theme-button-color,#2481cc)" strokeWidth="2" strokeLinecap="round"/>
                <circle cx="12" cy="17" r="1" fill="var(--tg-theme-button-color,#2481cc)"/>
              </svg>
            </div>
            <span className="flex-1 text-sm font-semibold text-[var(--tg-theme-text-color)] text-left">{q}</span>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none" className={`shrink-0 transition-transform duration-200 ${open === i ? 'rotate-90' : ''}`}>
              <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
          {open === i && (
            <div className={`py-3 px-4 pl-[66px] text-[13px] text-[var(--tg-theme-hint-color)] leading-[1.6] whitespace-pre-line ${i < FAQ.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
              {a}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export default function ESimFAQ() {
  const nav = useNavigate()
  const t = useT()

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/esim')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)' }}>
      <div className="px-1 pt-1.5 pb-0.5 flex items-start gap-2">
        <button onClick={() => nav('/esim')} className="w-8 h-8 rounded-lg bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex items-start justify-center shrink-0 cursor-pointer">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M15 18l-6-6 6-6" stroke="var(--tg-theme-text-color,#000)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
        <div>
          <div className="text-2xl font-extrabold text-[var(--tg-theme-text-color)]">FAQ</div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)]">{t('esim_faq_sub')}</div>
        </div>
      </div>
      <FAQGroup />
    </div>
  )
}