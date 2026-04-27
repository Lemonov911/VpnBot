import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { createSupportTicket, type SupportCategory } from '../api'
import { useT } from '../i18n'


const FAQ_META = [
  { color: '#27ae60', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg> },
  { color: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 16v-8M8 12l4 4 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><rect x="3" y="3" width="18" height="18" rx="3" stroke="#fff" strokeWidth="2"/></svg> },
  { color: '#e67e22', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> },
  { color: '#ff3b30', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 12h6M12 9v6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
  { color: '#8e44ad', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/><path d="M2 10h20" stroke="#fff" strokeWidth="2"/><path d="M6 15h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
]

function FaqGroup({ t }: { t: ReturnType<typeof useT> }) {
  const [open, setOpen] = useState<number | null>(null)
  const tp = WebApp.themeParams
  const faqItems = [
    { q: t('faq_q1'), a: t('faq_a1') },
    { q: t('faq_q2'), a: t('faq_a2') },
    { q: t('faq_q3'), a: t('faq_a3') },
    { q: t('faq_q4'), a: t('faq_a4') },
    { q: t('faq_q5'), a: t('faq_a5') },
  ]
  return (
    <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
      {faqItems.map(({ q, a }, i) => (
        <div key={i}>
          <div
            onClick={() => { setOpen(open === i ? null : i); WebApp.HapticFeedback.selectionChanged() }}
            style={{
              padding: '14px 16px', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 12,
              borderBottom: (open === i || i < faqItems.length - 1) ? '1px solid rgba(128,128,128,0.1)' : 'none',
            }}
          >
            <div style={{
              width: 36, height: 36, borderRadius: 10, flexShrink: 0,
              background: FAQ_META[i].color,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {FAQ_META[i].icon}
            </div>
            <span style={{ flex: 1, fontSize: 14, fontWeight: 600, color: tp.text_color, textAlign: 'left' }}>{q}</span>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none" style={{
              transform: open === i ? 'rotate(90deg)' : 'none',
              transition: 'transform 0.2s', flexShrink: 0,
            }}>
              <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          {open === i && (
            <div style={{
              padding: '12px 16px 16px 60px',
              fontSize: 13, color: tp.hint_color, lineHeight: 1.6,
              borderBottom: i < faqItems.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
            }}>
              {a}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

type PageState = 'form' | 'sending' | 'done' | 'error'

export default function Support() {
  const nav = useNavigate()
  const tp  = WebApp.themeParams
  const t   = useT()
  const accent = 'var(--tg-theme-button-color, #2481cc)'

  const [category, setCategory] = useState<SupportCategory>('vpn')
  const [message,  setMessage]  = useState('')
  const [state,    setState]    = useState<PageState>('form')
  const [ticketId, setTicketId] = useState<number | null>(null)
  const [errMsg,   setErrMsg]   = useState('')
  const textRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleSubmit = async () => {
    if (!message.trim() || state === 'sending') return
    WebApp.HapticFeedback.impactOccurred('light')
    setState('sending')
    setErrMsg('')
    try {
      const { ticket_id } = await createSupportTicket(category, message.trim())
      setTicketId(ticket_id)
      WebApp.HapticFeedback.notificationOccurred('success')
      setState('done')
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : t('server_error'))
      setState('error')
    }
  }

  const CATS = [
    { key: 'vpn'     as SupportCategory, label: t('support_cat_vpn'),  color: '#27ae60', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg> },
    { key: 'esim'    as SupportCategory, label: t('support_cat_esim'), color: '#2481cc', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/><path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.5" strokeLinecap="round"/></svg> },
    { key: 'payment' as SupportCategory, label: t('support_cat_pay'),  color: '#e67e22', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/><path d="M2 10h20" stroke="#fff" strokeWidth="2"/><path d="M6 15h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
    { key: 'other'   as SupportCategory, label: t('support_cat_other'),color: '#8e44ad', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  ]

  if (state === 'done') {
    return (
      <div className="page">
        <div className="center">
          <div style={{
            width: 72, height: 72, borderRadius: 22,
            background: 'rgba(39,174,96,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 36, marginBottom: 4,
          }}>✅</div>
          <div style={{ fontWeight: 700, fontSize: 22, color: tp.text_color }}>{t('support_done')}</div>
          <p style={{ color: tp.hint_color, fontSize: 14, lineHeight: 1.6 }}>
            {t('support_ticket')} #{ticketId} {t('support_ticket_accepted')}.<br />{t('support_done_sub')}
          </p>
          <button className="btn" onClick={() => { setMessage(''); setState('form') }} style={{ width: '100%', marginBottom: 10 }}>
            {t('support_write_more')}
          </button>
          <button className="btn" style={{ width: '100%', background: 'var(--section-bg)', color: tp.text_color }} onClick={() => nav('/')}>
            {t('support_home')}
          </button>
        </div>
      </div>
    )
  }

  const selectedCat = CATS.find(c => c.key === category) ?? CATS[0]

  return (
    <div className="page" style={{ gap: 12 }}>

      {/* Header */}
      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>{t('support_title')}</div>
        <div style={{ fontSize: 13, color: tp.hint_color }}>{t('support_sub')}</div>
      </div>

      {/* FAQ */}
      <span className="section-title">{t('support_faq')}</span>
      <FaqGroup t={t} />

      {/* Тема обращения */}
      <span className="section-title">{t('support_form')}</span>
      <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
        {CATS.map((c, i) => (
          <button
            key={c.key}
            onClick={() => { setCategory(c.key); WebApp.HapticFeedback.selectionChanged() }}
            style={{
              width: '100%', border: 'none', background: 'transparent',
              padding: '13px 16px', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 14,
              borderBottom: i < CATS.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
            }}
          >
            <div style={{
              width: 36, height: 36, borderRadius: 10, flexShrink: 0,
              background: c.color, display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {c.icon}
            </div>
            <span style={{ flex: 1, fontSize: 15, fontWeight: 500, color: tp.text_color, textAlign: 'left' }}>
              {c.label}
            </span>
            {category === c.key ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" fill={accent}/>
                <path d="M8 12l3 3 5-5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            ) : (
              <div style={{ width: 18, height: 18, borderRadius: '50%', border: '2px solid rgba(128,128,128,0.3)' }} />
            )}
          </button>
        ))}
      </div>

      {/* Поле сообщения */}
      <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden', padding: '4px 0' }}>
        <div style={{ padding: '10px 16px 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 8, flexShrink: 0,
            background: selectedCat.color,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {selectedCat.icon}
          </div>
          <span style={{ fontSize: 13, fontWeight: 600, color: tp.text_color }}>{selectedCat.label}</span>
        </div>
        <textarea
          ref={textRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={t('support_placeholder')}
          rows={5}
          style={{
            width: '100%', padding: '8px 16px 16px',
            border: 'none', background: 'transparent', color: tp.text_color,
            fontSize: 14, lineHeight: 1.6, resize: 'none', outline: 'none',
            fontFamily: 'inherit', boxSizing: 'border-box',
          }}
        />
      </div>

      {state === 'error' && (
        <p style={{ color: 'var(--tg-theme-destructive-text-color,#ff3b30)', textAlign: 'center', fontSize: 13, margin: 0 }}>
          {errMsg}
        </p>
      )}

      <button
        className="btn"
        disabled={!message.trim() || state === 'sending'}
        onClick={handleSubmit}
        style={{ width: '100%' }}
      >
        {state === 'sending' ? t('support_sending') : t('support_send')}
      </button>

    </div>
  )
}
