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
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
      {faqItems.map(({ q, a }, i) => (
        <div key={i}>
          <button
            onClick={() => { setOpen(open === i ? null : i); WebApp.HapticFeedback.selectionChanged() }}
            className={`w-full border-none bg-transparent py-[14px] px-4 cursor-pointer flex items-center gap-3 ${(open === i || i < faqItems.length - 1) ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
          >
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: FAQ_META[i].color }}>
              {FAQ_META[i].icon}
            </div>
            <span className="flex-1 text-[14px] font-semibold text-[var(--tg-theme-text-color)] text-left">{q}</span>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none" className={`shrink-0 transition-transform duration-200 ${open === i ? 'rotate-90' : ''}`}>
              <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
          {open === i && (
            <div className={`py-3 px-4 pl-[60px] text-[13px] text-[var(--tg-theme-hint-color)] leading-[1.6] ${i < faqItems.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
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
          <div className="w-[72px] h-[72px] rounded-[22px] mb-1 bg-success/12 flex items-center justify-center text-[36px]">✅</div>
          <div className="font-extrabold text-[22px] text-[var(--tg-theme-text-color)]">{t('support_done')}</div>
          <p className="text-[var(--tg-theme-hint-color)] text-sm leading-relaxed max-w-[280px]">
            {t('support_ticket')} #{ticketId} {t('support_ticket_accepted')}.<br />{t('support_done_sub')}
          </p>
          <button className="btn w-full mb-2.5" onClick={() => { setMessage(''); setState('form') }}>
            {t('support_write_more')}
          </button>
          <button className="btn w-full !bg-[var(--tg-theme-section-bg-color)] !text-[var(--tg-theme-text-color)]" onClick={() => nav('/')}>
            {t('support_home')}
          </button>
        </div>
      </div>
    )
  }

  const selectedCat = CATS.find(c => c.key === category) ?? CATS[0]

  return (
    <div className="page" style={{ gap: 12 }}>

      {/* FAQ */}
      <span className="section-title">{t('support_faq')}</span>
      <FaqGroup t={t} />

      {/* Тема обращения */}
      <span className="section-title">{t('support_form')}</span>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        {CATS.map((c, i) => (
          <button
            key={c.key}
            onClick={() => { setCategory(c.key); WebApp.HapticFeedback.selectionChanged() }}
            className={`w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-center gap-[14px] ${i < CATS.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
          >
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: c.color }}>
              {c.icon}
            </div>
            <span className="flex-1 text-[15px] font-medium text-[var(--tg-theme-text-color)] text-left">
              {c.label}
            </span>
            {category === c.key ? (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" fill={accent}/>
                <path d="M8 12l3 3 5-5" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            ) : (
              <div className="w-[18px] h-[18px] rounded-full border-2 border-[rgba(128,128,128,0.3)]" />
            )}
          </button>
        ))}
      </div>

      {/* Поле сообщения */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden py-1">
        <div className="py-[10px] px-4 pb-[6px] flex items-center gap-[10px]">
          <div className="w-7 h-7 rounded-lg shrink-0 flex items-center justify-center" style={{ background: selectedCat.color }}>
            {selectedCat.icon}
          </div>
          <span className="text-[13px] font-semibold text-[var(--tg-theme-text-color)]">{selectedCat.label}</span>
        </div>
        <textarea
          ref={textRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={t('support_placeholder')}
          rows={5}
          maxLength={2000}
          aria-label={t('support_placeholder')}
          className="w-full py-2 px-4 pb-1 border-none bg-transparent text-[var(--tg-theme-text-color)] text-sm leading-[1.6] resize-none outline-none font-sans box-border"
        />
        <div className="px-4 pb-2 text-[10px] text-[var(--tg-theme-hint-color)] text-right">
          {message.length} / 2000
        </div>
      </div>

      {/* Хинт когда юзер ждать ответа (раньше — нечего: тикет уходил «в пустоту») */}
      <div className="text-[11px] text-[var(--tg-theme-hint-color)] px-1 text-center">
        💬 Ответим прямо в чате с ботом — не выключай уведомления
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
