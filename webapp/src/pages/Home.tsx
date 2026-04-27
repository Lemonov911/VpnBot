import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getActiveSubscription, getUserStats,
  type Subscription, type UserStats,
} from '../api'
import { useT, usePlural } from '../i18n'

export default function Home() {
  const nav    = useNavigate()
  const tp     = WebApp.themeParams
  const t      = useT()
  const p      = usePlural()

  const [sub,       setSub]       = useState<Subscription | null | undefined>(undefined)
  const [stats,     setStats]     = useState<UserStats | null>(null)
  useEffect(() => {
    getActiveSubscription().catch(() => null).then(setSub)
    getUserStats().catch(() => null).then(s => setStats(s))
  }, [])

  const planLabel = (key: string) => {
    const map: Record<string, string> = {
      vpn_start:   t('vpn_plan_start'),
      vpn_popular: t('vpn_plan_popular'),
      vpn_pro:     t('vpn_plan_pro'),
      vpn_family:  t('vpn_plan_family'),
    }
    return map[key] ?? key
  }

  const hasStats = stats && (stats.stars_spent > 0 || stats.bonus_days > 0 || stats.invited > 0)

  return (
    <>
      <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)', gap: 12 }}>

        {/* ── Header ── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0 2px' }}>
          <img
            src={import.meta.env.BASE_URL + 'logo.png'}
            alt="MAX"
            style={{ width: 40, height: 40, borderRadius: 11, flexShrink: 0, objectFit: 'cover' }}
          />
          <div>
            <div style={{ fontWeight: 800, fontSize: 20, color: tp.text_color, letterSpacing: -0.3, lineHeight: 1.2 }}>
              MAX VPN & eSIM
            </div>
            <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 1 }}>
              {t('home_hero_sub').split('\n')[0]}
            </div>
          </div>
        </div>

        {/* ── Service cards ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>

          {/* VPN card */}
          {sub === undefined ? (
            <div className="skeleton" style={{ height: 178, borderRadius: 20 }} />
          ) : (
            <div className="fade-in" style={{
              borderRadius: 20, overflow: 'hidden',
              background: 'var(--section-bg)',
              border: '1px solid var(--card-border)',
              display: 'flex', flexDirection: 'column',
            }}>
              <div style={{ height: 3, background: 'linear-gradient(90deg, #2481cc, #5856d6)', flexShrink: 0 }} />
              <div style={{ padding: '14px 14px 16px', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 158 }}>
                <div style={{
                  width: 42, height: 42, borderRadius: 13,
                  background: 'linear-gradient(135deg, #2481cc, #5856d6)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  marginBottom: 11, flexShrink: 0,
                  boxShadow: '0 4px 14px rgba(36,129,204,0.4)',
                }}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
                      stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    {sub && <path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>}
                  </svg>
                </div>

                <div style={{ fontSize: 10, fontWeight: 700, color: tp.hint_color, textTransform: 'uppercase', letterSpacing: 0.7, marginBottom: 6 }}>
                  VPN
                </div>

                {sub ? (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 3 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#27ae60', display: 'block', flexShrink: 0 }} />
                      <span style={{ fontSize: 12, fontWeight: 700, color: '#27ae60' }}>
                        {t('home_active')}
                      </span>
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: tp.text_color, marginBottom: 2 }}>
                      {planLabel(sub.plan)}
                    </div>
                    <div style={{ fontSize: 11, color: tp.hint_color }}>
                      {p(sub.days_remaining, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: t('home_active_days') })}
                    </div>
                    <div style={{ flex: 1, minHeight: 20 }} />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      style={{
                        width: '100%', padding: '8px 0', borderRadius: 10, border: 'none',
                        background: 'rgba(36,129,204,0.13)', color: '#2481cc',
                        fontSize: 12, fontWeight: 700, cursor: 'pointer',
                      }}
                    >
                      {t('home_manage')} →
                    </button>
                  </>
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 3 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'rgba(128,128,128,0.35)', display: 'block', flexShrink: 0 }} />
                      <span style={{ fontSize: 12, fontWeight: 600, color: tp.hint_color }}>
                        {t('home_no_sub')}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: tp.hint_color }}>
                      {t('home_sub_from')}
                    </div>
                    <div style={{ flex: 1, minHeight: 20 }} />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      style={{
                        width: '100%', padding: '8px 0', borderRadius: 10, border: 'none',
                        background: 'linear-gradient(135deg, #2481cc, #5856d6)',
                        color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                      }}
                    >
                      {t('home_buy_vpn')}
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* eSIM card */}
          <div className="fade-in" style={{
            borderRadius: 20, overflow: 'hidden',
            background: 'var(--section-bg)',
            border: '1px solid var(--card-border)',
            display: 'flex', flexDirection: 'column',
          }}>
            <div style={{ height: 3, background: 'linear-gradient(90deg, #27ae60, #00b4d8)', flexShrink: 0 }} />
            <div style={{ padding: '14px 14px 16px', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 158 }}>
              <div style={{
                width: 42, height: 42, borderRadius: 13,
                background: 'linear-gradient(135deg, #27ae60, #00b4d8)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: 11,
                boxShadow: '0 4px 14px rgba(39,174,96,0.4)',
              }}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  <rect x="5" y="2" width="14" height="20" rx="3" stroke="#fff" strokeWidth="2"/>
                  <path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round"/>
                </svg>
              </div>

              <div style={{ fontSize: 10, fontWeight: 700, color: tp.hint_color, textTransform: 'uppercase', letterSpacing: 0.7, marginBottom: 6 }}>
                eSIM
              </div>

              <div style={{ fontSize: 14, fontWeight: 700, color: tp.text_color, marginBottom: 2 }}>
                {t('home_esim_title')}
              </div>
              <div style={{ fontSize: 11, color: tp.hint_color }}>
                {t('home_esim_sub')}
              </div>

              <div style={{ flex: 1, minHeight: 20 }} />
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/esim') }}
                style={{
                  width: '100%', padding: '8px 0', borderRadius: 10, border: 'none',
                  background: 'linear-gradient(135deg, #27ae60, #00b4d8)',
                  color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                }}
              >
                {t('home_esim_browse')}
              </button>
            </div>
          </div>
        </div>

        {/* ── Quick actions ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
          {[
            {
              color: '#27ae60', label: t('home_configs'),
              action: () => nav('/configs'),
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
                <rect x="9" y="3" width="6" height="4" rx="1" stroke="#fff" strokeWidth="2"/>
                <path d="M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>,
            },
            {
              color: '#8e44ad', label: t('home_guide'),
              action: () => nav('/instructions'),
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="#fff" strokeWidth="2"/>
                <path d="M12 8v4l3 3" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
              </svg>,
            },
            {
              color: '#e67e22', label: t('home_support'),
              action: () => nav('/support'),
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
                  stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>,
            },
          ].map(({ color, label, action, icon }) => (
            <button
              key={label}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); action() }}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                padding: '14px 6px 12px',
                background: 'var(--section-bg)', border: '1px solid var(--card-border)',
                borderRadius: 16, cursor: 'pointer',
              }}
            >
              <div style={{
                width: 44, height: 44, borderRadius: 13, background: color,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                boxShadow: `0 4px 10px ${color}44`,
              }}>
                {icon}
              </div>
              <span style={{ fontSize: 12, fontWeight: 600, color: tp.text_color, lineHeight: 1.2 }}>{label}</span>
            </button>
          ))}
        </div>

        {/* ── Referral banner ── */}
        <div
          onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/referral') }}
          style={{
            background: 'var(--section-bg)', borderRadius: 16,
            padding: '14px 16px',
            display: 'flex', alignItems: 'center', gap: 14,
            cursor: 'pointer',
            border: '1.5px solid rgba(230,126,34,0.2)',
          }}
        >
          <div style={{
            width: 44, height: 44, borderRadius: 13, flexShrink: 0,
            background: '#e67e22',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 4px 12px rgba(230,126,34,0.35)',
          }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path d="M20 12v10H4V12" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M22 7H2v5h20V7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 22V7" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
              <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: tp.text_color, marginBottom: 2 }}>
              {t('home_invite')}
            </div>
            <div style={{ fontSize: 12, color: tp.hint_color }}>
              {t('home_invite_sub')}
            </div>
          </div>
          <svg width="7" height="12" viewBox="0 0 7 12" fill="none">
            <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>

        {/* ── Stats ── */}
        {hasStats && (
          <div className="fade-in" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {[
              { value: `${stats!.stars_spent} ⭐`, label: t('home_stars_spent_label'), show: stats!.stars_spent > 0 },
              { value: `+${stats!.bonus_days}${t('day')}`,     label: t('home_bonus_label'),        show: stats!.bonus_days > 0  },
              { value: String(stats!.invited),       label: t('home_invited_label'),      show: stats!.invited > 0     },
            ].filter(x => x.show).map(({ value, label }) => (
              <div key={label} style={{
                background: 'var(--section-bg)', border: '1px solid var(--card-border)',
                borderRadius: 14, padding: '12px 8px', textAlign: 'center',
              }}>
                <div style={{ fontSize: 16, fontWeight: 800, color: tp.text_color }}>{value}</div>
                <div style={{ fontSize: 10, color: tp.hint_color, marginTop: 3, lineHeight: 1.3 }}>{label}</div>
              </div>
            ))}
          </div>
        )}

      </div>
    </>
  )
}