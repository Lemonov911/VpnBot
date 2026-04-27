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
    <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
      {FAQ.map(({ q, a }, i) => (
        <div key={i}>
          <div
            onClick={() => { setOpen(open === i ? null : i); WebApp.HapticFeedback.selectionChanged() }}
            style={{
              padding: '13px 16px', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 14,
              borderBottom: (open === i || i < FAQ.length - 1) ? '1px solid rgba(128,128,128,0.1)' : 'none',
            }}
          >
            <div style={{
              width: 36, height: 36, borderRadius: 10, flexShrink: 0,
              background: 'rgba(36,129,204,0.12)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="var(--tg-theme-button-color,#2481cc)" strokeWidth="2"/>
                <path d="M12 8c0-1.1.9-2 2-2s2 .9 2 2c0 1.5-2 2-2 3" stroke="var(--tg-theme-button-color,#2481cc)" strokeWidth="2" strokeLinecap="round"/>
                <circle cx="12" cy="17" r="1" fill="var(--tg-theme-button-color,#2481cc)"/>
              </svg>
            </div>
            <span style={{ flex: 1, fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{q}</span>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none" style={{
              transform: open === i ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s', flexShrink: 0,
            }}>
              <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          {open === i && (
            <div style={{
              padding: '12px 16px 16px 66px',
              fontSize: 13, color: 'var(--hint)', lineHeight: 1.6, whiteSpace: 'pre-line',
              borderBottom: i < FAQ.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
            }}>
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
    WebApp.BackButton.onClick(() => nav('/esim'))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(() => nav('/esim')) }
  }, [nav])

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)' }}>
      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: 'var(--text)', marginBottom: 4 }}>FAQ</div>
        <div style={{ fontSize: 13, color: 'var(--hint)' }}>{t('esim_faq_sub')}</div>
      </div>
      <FAQGroup />
    </div>
  )
}