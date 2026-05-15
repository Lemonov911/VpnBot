import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getReferralStats, type ReferralStats } from '../api'
import { useT, useLang } from '../i18n'

// Fallback для устройств где navigator.clipboard недоступен (старые iOS WebView,
// insecure contexts). Создаём временный textarea, выделяем, document.execCommand('copy').
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

export default function Referral() {
  const nav    = useNavigate()
  const t      = useT()
  const lang   = useLang().lang

  const STEPS = [
    { num: '1', color: '#2481cc', title: t('ref_how1_title'), sub: t('ref_how1_sub') },
    { num: '2', color: '#27ae60', title: t('ref_how2_title'), sub: t('ref_how2_sub') },
    { num: '3', color: '#e67e22', title: t('ref_how3_title'), sub: t('ref_how3_sub') },
  ]

  const [stats,   setStats]   = useState<ReferralStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied,  setCopied]  = useState(false)

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    getReferralStats()
      .then(setStats)
      .catch(() => setStats(null))  // API упал — UI покажет ref_error fallback
      .finally(() => setLoading(false))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleCopy = () => {
    if (!stats) return
    WebApp.HapticFeedback.impactOccurred('light')
    // navigator.clipboard может отсутствовать (Safari в insecure context,
    // старый WebView). Fallback на legacy execCommand для iOS<16 WebView.
    const text = stats.ref_link
    const onSuccess = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
        legacyCopy(text) && onSuccess()
      })
    } else {
      if (legacyCopy(text)) onSuccess()
    }
  }

  const handleShare = () => {
    if (!stats) return
    WebApp.HapticFeedback.impactOccurred('light')
    // Раньше тут был список конкретных заблокированных сервисов («Instagram,
    // YouTube, ChatGPT»). 149-ФЗ (запрет рекламы VPN в РФ с сент 2025)
    // прицельно бьёт по упоминаниям обхода блокировок конкретных ресурсов
    // — это легко цитируется при подаче на блок. Обобщённая формулировка
    // ниже не реклама обхода, а просто описание категории софта.
    const text = encodeURIComponent(lang === 'ru'
      ? `🛡 MAX VPN — быстрый VPN для телефона и компьютера\nПопробуй: ${stats.ref_link}`
      : `🛡 MAX VPN — fast VPN for mobile and desktop\nTry it: ${stats.ref_link}`
    )
    WebApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(stats.ref_link)}&text=${text}`)
  }

  return (
    <div className="page" style={{ gap: 12 }}>

      {/* Hero — без неё страница начиналась с «Как это работает» без контекста */}
      <div className="fade-in rounded-[20px] p-4 bg-gradient-to-br from-[#ff6b6b] to-[#feca57] text-white shadow-[0_8px_24px_rgba(255,107,107,0.25)]">
        <div className="text-base font-bold">{t('ref_title')}</div>
        <div className="text-[12px] opacity-90 mt-0.5">{t('ref_sub2')}</div>
      </div>

      {/* How it works */}
      <span className="section-title">{t('ref_how_title')}</span>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        {STEPS.map(({ num, color, title, sub }, i) => (
          <div key={i} className={`py-[13px] px-4 flex items-center gap-[14px] ${i < STEPS.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
            <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center font-extrabold text-base text-white" style={{ background: color }}>
              {num}
            </div>
            <div>
              <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] leading-[1.3]">{title}</div>
              <div className="text-xs text-[var(--tg-theme-hint-color)] mt-0.5">{sub}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Ссылка */}
      <span className="section-title">{t('ref_link_title')}</span>
      {loading ? (
        <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] py-3 px-[14px]">
          <div className="h-1 rounded bg-[rgba(128,128,128,0.12)] overflow-hidden">
            <div className="h-full rounded bg-gradient-to-r from-transparent via-[var(--tg-theme-button-color,#2481cc)] to-transparent animate-[progress-slide_1.4s_ease-in-out_infinite] w-1/2" />
          </div>
        </div>
      ) : stats ? (
        <>
          <div className="bg-[var(--tg-theme-section-bg-color)] rounded-[14px] py-3 px-[14px] flex items-center gap-[10px]">
            <span className="flex-1 text-[13px] text-[var(--tg-theme-hint-color)] overflow-hidden text-ellipsis whitespace-nowrap">
              {stats.ref_link}
            </span>
            <button onClick={handleCopy} className={`py-[7px] px-[14px] rounded-[10px] border-none text-white text-xs font-semibold cursor-pointer shrink-0 transition-colors ${copied ? 'bg-success' : 'bg-[var(--tg-theme-button-color,#2481cc)]'}`}>
              {copied ? t('ref_copied') : t('ref_copy')}
            </button>
          </div>

          <button
            onClick={handleShare}
            className="w-full py-[13px] rounded-[14px] border-none text-white text-[15px] font-semibold cursor-pointer flex items-center justify-center gap-2"
            style={{ background: 'var(--tg-theme-button-color, #2481cc)', color: 'var(--tg-theme-button-text-color, #fff)' }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M22 2L11 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <path d="M22 2L15 22l-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            {t('ref_share')}
          </button>

          {/* Статистика */}
          {(stats.invited > 0 || stats.converted > 0 || stats.bonus_days > 0) && (
            <>
              <span className="section-title">{t('ref_stats')}</span>
              <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
                {[
                  {
                    color: '#2481cc',
                    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3.5" stroke="#fff" strokeWidth="2"/><path d="M2 20c0-3.314 3.134-6 7-6s7 2.686 7 6" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7.5" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M22 20c0-2.761-2.239-5-5-5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg>,
                    label: t('ref_invited'),
                    value: stats.invited,
                  },
                  {
                    color: '#27ae60',
                    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><circle cx="12" cy="12" r="10" stroke="#fff" strokeWidth="2"/></svg>,
                    label: t('ref_bought'),
                    value: stats.converted,
                  },
                  {
                    color: '#e67e22',
                    icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="#fff" strokeWidth="2"/><path d="M12 6v6l4 2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg>,
                    label: t('ref_bonus'),
                    value: `+${stats.bonus_days}`,
                  },
                ].map(({ color, icon, label, value }, i, arr) => (
                  <div key={label} className={`py-[13px] px-4 flex items-center gap-[14px] ${i < arr.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                    <div className="w-9 h-9 rounded-[10px] shrink-0 flex items-center justify-center" style={{ background: color }}>
                      {icon}
                    </div>
                    <span className="flex-1 text-[15px] font-medium text-[var(--tg-theme-text-color)]">{label}</span>
                    <span className="text-lg font-bold text-[var(--tg-theme-text-color)]">{value}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center break-words">
          {t('ref_error')}
        </p>
      )}

    </div>
  )
}
