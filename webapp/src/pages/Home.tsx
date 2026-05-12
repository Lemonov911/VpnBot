import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getActiveSubscription, getUserStats,
  getTrialStatus, claimTrial,
  type Subscription, type UserStats, type TrialStatus,
} from '../api'
import { useT, usePlural } from '../i18n'

export default function Home() {
  const nav    = useNavigate()
  const t      = useT()
  const p      = usePlural()

  const [sub,       setSub]       = useState<Subscription | null | undefined>(undefined)
  const [stats,     setStats]     = useState<UserStats | null>(null)
  const [trial,     setTrial]     = useState<TrialStatus | null>(null)
  const [claiming,  setClaiming]  = useState(false)
  const [trialErr,  setTrialErr]  = useState('')
  const [trialDone, setTrialDone] = useState(false)

  useEffect(() => {
    getActiveSubscription().catch(() => null).then(setSub)
    getUserStats().catch(() => null).then(s => setStats(s))
    getTrialStatus().catch(() => null).then(s => setTrial(s))
  }, [])

  const handleClaimTrial = async () => {
    setClaiming(true)
    setTrialErr('')
    WebApp.HapticFeedback.impactOccurred('medium')
    try {
      await claimTrial()
      WebApp.HapticFeedback.notificationOccurred('success')
      setTrialDone(true)
      // refresh subscription card — теперь юзер с активным trial
      getActiveSubscription().catch(() => null).then(setSub)
      setTrial({ eligible: false, duration_days: trial?.duration_days ?? 3 })
    } catch (e: unknown) {
      WebApp.HapticFeedback.notificationOccurred('error')
      const err = e as { message?: string }
      const msg = err.message || ''
      if (msg.includes('active_subscription'))     setTrialErr(t('trial_err_active'))
      else if (msg.includes('already_claimed'))    setTrialErr(t('trial_err_used'))
      else if (msg.includes('no_server'))          setTrialErr(t('trial_err_no_server'))
      else                                          setTrialErr(t('trial_err_generic'))
    } finally {
      setClaiming(false)
    }
  }

  const planLabel = (key: string) => {
    const map: Record<string, string> = {
      vpn_base:    t('vpn_plan_base'),
      vpn_max:     t('vpn_plan_max'),
      vpn_trial:   t('vpn_plan_trial'),
      vpn_start:   t('vpn_plan_start'),
      vpn_popular: t('vpn_plan_popular'),
      vpn_pro:     t('vpn_plan_pro'),
      vpn_family:  t('vpn_plan_family'),
    }
    return map[key] ?? key
  }

  const hasStats = stats && (stats.stars_spent > 0 || stats.bonus_days > 0 || stats.invited > 0)

  const quickActions = [
    {
      color: '#27ae60',
      shadow: '0 4px 10px rgba(39,174,96,0.28)',
      label: t('home_configs'),
      action: () => nav('/configs'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
        <rect x="9" y="3" width="6" height="4" rx="1" stroke="#fff" strokeWidth="2"/>
        <path d="M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>,
    },
    {
      color: '#8e44ad',
      shadow: '0 4px 10px rgba(142,68,173,0.28)',
      label: t('home_guide'),
      action: () => nav('/instructions'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M9 7h6M9 11h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>,
    },
    {
      color: '#e67e22',
      shadow: '0 4px 10px rgba(230,126,34,0.28)',
      label: t('home_support'),
      action: () => nav('/support'),
      icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
          stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>,
    },
  ]

  return (
    <>
      <div className="page gap-3">

        {/* ── Trial CTA banner — shown only if eligible & no active sub ── */}
        {trial?.eligible && sub === null && !trialDone && (
          <div className="fade-in rounded-[20px] p-4 bg-gradient-to-br from-[#16a34a] to-[#0ea5e9] text-white shadow-[0_8px_24px_rgba(14,165,233,0.35)]">
            <div className="text-base font-bold mb-1">{t('trial_banner_title')}</div>
            <div className="text-[12px] opacity-90 mb-3 leading-snug">{t('trial_banner_sub')}</div>
            <button
              onClick={handleClaimTrial}
              disabled={claiming}
              className="w-full py-2.5 rounded-[12px] border-none bg-white/95 text-[#16a34a] text-sm font-bold cursor-pointer disabled:opacity-60"
            >
              {claiming ? t('trial_claiming') : t('trial_banner_btn')}
            </button>
            {trialErr && (
              <div className="mt-2 text-[11px] bg-white/15 rounded px-2 py-1">{trialErr}</div>
            )}
          </div>
        )}

        {trialDone && (
          <div className="fade-in rounded-[20px] p-4 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)]">
            <div className="text-base font-bold mb-1 text-[var(--tg-theme-text-color)]">{t('trial_success_title')}</div>
            <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-snug mb-3">{t('trial_success_sub')}</div>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/plans') }}
              className="w-full py-2 rounded-[10px] border-none bg-primary/[0.13] text-primary text-xs font-bold cursor-pointer"
            >
              {t('trial_success_upgrade')}
            </button>
          </div>
        )}

        {/* ── Service cards ── */}
        <div className="grid grid-cols-2 gap-2.5">

          {/* VPN card */}
          {sub === undefined ? (
            <div className="skeleton h-[178px] rounded-[20px]" />
          ) : (
            <div className="fade-in rounded-[20px] overflow-hidden bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex flex-col">
              <div className="h-[3px] bg-gradient-to-r from-primary to-[#5856d6] shrink-0" />
              <div className="px-[14px] pt-[14px] pb-4 flex flex-col flex-1 min-h-[158px]">
                <div className="w-[42px] h-[42px] rounded-[13px] bg-gradient-to-br from-primary to-[#5856d6] flex items-center justify-center mb-[11px] shrink-0 shadow-[0_4px_14px_rgba(36,129,204,0.4)]">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
                      stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    {sub && <path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>}
                  </svg>
                </div>

                <div className="text-[10px] font-bold uppercase tracking-[0.7px] mb-1.5 text-[var(--tg-theme-hint-color)]">
                  VPN
                </div>

                {sub ? (
                  <>
                    <div className="flex items-center gap-[5px] mb-[3px]">
                      <span className="w-[7px] h-[7px] rounded-full bg-success shrink-0 block" />
                      <span className="text-xs font-bold text-success">
                        {t('home_active')}
                      </span>
                    </div>
                    <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">
                      {planLabel(sub.plan)}
                    </div>
                    <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
                      {p(sub.days_remaining, { ru: [t('home_days_left_1'), t('home_days_left_2'), t('days')], en: ['day', 'days'] })}
                    </div>
                    <div className="flex-1 min-h-[20px]" />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      className="w-full py-2 rounded-[10px] border-none bg-primary/[0.13] text-primary text-xs font-bold cursor-pointer"
                    >
                      {t('home_manage')} →
                    </button>
                  </>
                ) : (
                  <>
                    <div className="flex items-center gap-[5px] mb-[3px]">
                      <span className="w-[7px] h-[7px] rounded-full bg-gray-500/35 shrink-0 block" />
                      <span className="text-xs font-semibold text-[var(--tg-theme-hint-color)]">
                        {t('home_no_sub')}
                      </span>
                    </div>
                    <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
                      {t('home_sub_from')}
                    </div>
                    <div className="flex-1 min-h-[20px]" />
                    <button
                      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/vpn') }}
                      className="w-full py-2 rounded-[10px] border-none bg-gradient-to-br from-primary to-[#5856d6] text-white text-xs font-bold cursor-pointer"
                    >
                      {t('home_buy_vpn')}
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* eSIM card */}
          <div className="fade-in rounded-[20px] overflow-hidden bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] flex flex-col">
            <div className="h-[3px] bg-gradient-to-r from-success to-[#00b4d8] shrink-0" />
            <div className="px-[14px] pt-[14px] pb-4 flex flex-col flex-1 min-h-[158px]">
              <div className="w-[42px] h-[42px] rounded-[13px] bg-gradient-to-br from-success to-[#00b4d8] flex items-center justify-center mb-[11px] shadow-[0_4px_14px_rgba(39,174,96,0.4)]">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                  <rect x="5" y="2" width="14" height="20" rx="3" stroke="#fff" strokeWidth="2"/>
                  <path d="M9 8h6M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round"/>
                </svg>
              </div>

              <div className="text-[10px] font-bold uppercase tracking-[0.7px] mb-1.5 text-[var(--tg-theme-hint-color)]">
                eSIM
              </div>

              <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">
                {t('home_esim_title')}
              </div>
              <div className="text-[11px] text-[var(--tg-theme-hint-color)]">
                {t('home_esim_sub')}
              </div>

              <div className="flex-1 min-h-[20px]" />
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/esim') }}
                className="w-full py-2 rounded-[10px] border-none bg-gradient-to-br from-success to-[#00b4d8] text-white text-xs font-bold cursor-pointer"
              >
                {t('home_esim_browse')}
              </button>
            </div>
          </div>
        </div>

        {/* ── Quick actions ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
          {quickActions.map(({ color, shadow, label, action, icon }) => (
            <button
              key={label}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); action() }}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                gap: 8, padding: '14px 6px 12px',
                background: 'var(--tg-theme-section-bg-color)',
                border: '1px solid var(--card-border)',
                borderRadius: 16, cursor: 'pointer',
                minHeight: 86,
              }}
            >
              <div style={{
                width: 44, height: 44, borderRadius: 13, flexShrink: 0,
                background: color, boxShadow: shadow,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {icon}
              </div>
              <span style={{
                fontSize: 11, fontWeight: 600,
                color: 'var(--tg-theme-text-color)',
                lineHeight: 1.2, textAlign: 'center',
              }}>{label}</span>
            </button>
          ))}
        </div>

        {/* ── Referral banner ── */}
        <div
          onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); nav('/referral') }}
          className="bg-[var(--tg-theme-section-bg-color)] rounded-2xl py-[14px] px-4 flex items-center gap-3.5 cursor-pointer border-[1.5px] border-warning/20"
        >
          <div className="w-11 h-11 rounded-[13px] shrink-0 bg-warning flex items-center justify-center shadow-[0_4px_12px_rgba(230,126,34,0.35)]">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path d="M20 12v10H4V12" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M22 7H2v5h20V7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 22V7" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
              <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-[var(--tg-theme-text-color)] mb-[2px]">
              {t('home_invite')}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color)]">
              {t('home_invite_sub')}
            </div>
          </div>
          <svg width="7" height="12" viewBox="0 0 7 12" fill="none">
            <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </div>

        {/* ── Stats ── */}
        {hasStats && (
          <div className="fade-in grid grid-cols-3 gap-2">
            {[
              { value: `${stats!.stars_spent} ⭐`, label: t('home_stars_spent_label'), show: stats!.stars_spent > 0 },
              { value: `+${stats!.bonus_days}${t('day')}`,     label: t('home_bonus_label'),        show: stats!.bonus_days > 0  },
              { value: String(stats!.invited),       label: t('home_invited_label'),      show: stats!.invited > 0     },
            ].filter(x => x.show).map(({ value, label }) => (
              <div key={label} className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] px-2 py-3 text-center">
                <div className="text-base font-extrabold text-[var(--tg-theme-text-color)]">{value}</div>
                <div className="text-[10px] text-[var(--tg-theme-hint-color)] mt-[3px] leading-[1.3]">{label}</div>
              </div>
            ))}
          </div>
        )}

      </div>
    </>
  )
}