import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { getReferralStats, type ReferralStats } from '../api'
import { useT, useLang } from '../i18n'

export default function Referral() {
const nav    = useNavigate()
  const tp     = WebApp.themeParams
  const t      = useT()
  const lang   = useLang().lang
  const accent = 'var(--tg-theme-button-color, #2481cc)'

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
    getReferralStats().then(setStats).finally(() => setLoading(false))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleCopy = () => {
    if (!stats) return
    WebApp.HapticFeedback.impactOccurred('light')
    navigator.clipboard.writeText(stats.ref_link).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleShare = () => {
    if (!stats) return
    WebApp.HapticFeedback.impactOccurred('light')
    const text = encodeURIComponent(lang === 'ru'
      ? `🛡 VPN без блокировок — Amnezia WireGuard\nПопробуй по моей ссылке: ${stats.ref_link}`
      : `🛡 VPN without blocks — Amnezia WireGuard\nTry it with my link: ${stats.ref_link}`
    )
    WebApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(stats.ref_link)}&text=${text}`)
  }

  return (
    <div className="page" style={{ gap: 12 }}>

      {/* Header */}
      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>{t('ref_title')}</div>
        <div style={{ fontSize: 13, color: tp.hint_color }}>
          {t('ref_sub')}{' '}
          <span style={{ color: tp.text_color, fontWeight: 600 }}>{t('ref_sub2')}</span>
        </div>
      </div>

      {/* How it works */}
      <span className="section-title">{t('ref_how_title')}</span>
      <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
        {[
          { num: '1', color: '#2481cc', title: t('ref_how1_title'), sub: t('ref_how1_sub') },
          { num: '2', color: '#27ae60', title: t('ref_how2_title'), sub: t('ref_how2_sub') },
          { num: '3', color: '#e67e22', title: t('ref_how3_title'), sub: t('ref_how3_sub') },
        ].map(({ num, color, title, sub }, i, arr) => (
          <div key={i} style={{
            padding: '13px 16px',
            display: 'flex', alignItems: 'center', gap: 14,
            borderBottom: i < arr.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
          }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10, flexShrink: 0,
              background: color,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 800, fontSize: 16, color: '#fff',
            }}>
              {num}
            </div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 600, color: tp.text_color, lineHeight: 1.3 }}>{title}</div>
              <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 2 }}>{sub}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Ссылка */}
      <span className="section-title">{t('ref_link_title')}</span>
      {loading ? (
        <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 14, padding: '12px 14px' }}>
          <div style={{ height: 4, borderRadius: 4, background: 'rgba(128,128,128,0.12)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 4,
              background: `linear-gradient(90deg, transparent 0%, ${accent} 40%, transparent 100%)`,
              animation: 'progress-slide 1.4s ease-in-out infinite',
              width: '50%',
            }} />
          </div>
        </div>
      ) : stats ? (
        <>
          <div style={{
            background: 'var(--section-bg)', borderRadius: 14,
            padding: '12px 14px',
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <span style={{
              flex: 1, fontSize: 13, color: tp.hint_color,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {stats.ref_link}
            </span>
            <button onClick={handleCopy} style={{
              padding: '7px 14px', borderRadius: 10, border: 'none',
              background: copied ? '#27ae60' : accent,
              color: '#fff', fontSize: 12, fontWeight: 600,
              cursor: 'pointer', flexShrink: 0, transition: 'background 0.2s',
            }}>
              {copied ? t('ref_copied') : t('ref_copy')}
            </button>
          </div>

          <button
            onClick={handleShare}
            style={{
              width: '100%', padding: '13px 0', borderRadius: 14, border: 'none',
              background: accent, color: tp.button_text_color ?? '#fff',
              fontSize: 15, fontWeight: 600, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            }}
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
              <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
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
                  <div key={label} style={{
                    padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14,
                    borderBottom: i < arr.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
                  }}>
                    <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, background: color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {icon}
                    </div>
                    <span style={{ flex: 1, fontSize: 15, fontWeight: 500, color: tp.text_color }}>{label}</span>
                    <span style={{ fontSize: 18, fontWeight: 700, color: tp.text_color }}>{value}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      ) : (
        <p style={{ color: 'var(--tg-theme-destructive-text-color,#ff3b30)', textAlign: 'center', overflowWrap: 'break-word', wordBreak: 'break-word' }}>
          {t('ref_error')}
        </p>
      )}

    </div>
  )
}
