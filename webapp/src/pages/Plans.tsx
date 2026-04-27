import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  createVpnInvoice, createVpnInvoiceCrypto, getActiveSubscription, changeSubscriptionPlan,
  type Subscription,
} from '../api'
import PaymentSheet, { PLANS, type Plan, type PayMethod } from '../components/PaymentSheet'
import { useT, usePlural } from '../i18n'
import type { TKey } from '../i18n'

function calcUpgradePrice(curRub: number, newRub: number, daysLeft: number): number {
  return Math.max(1, Math.round((newRub - curRub) * daysLeft / 30))
}

const PLAN_ICONS: Record<string, { bg: string; icon: JSX.Element }> = {
  vpn_start:   { bg: '#5ac8fa', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_popular: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_pro:     { bg: '#5856d6', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="#ffffff33" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_family:  { bg: '#ff2d55', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3" stroke="#fff" strokeWidth="2"/><path d="M3 19c0-3 2.686-5 6-5s6 2 6 5" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M21 19c0-2.5-1.8-4-4-4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
}

const PLAN_NAME_KEY: Record<string, TKey> = {
  vpn_start: 'vpn_plan_start',
  vpn_popular: 'vpn_plan_popular',
  vpn_pro: 'vpn_plan_pro',
  vpn_family: 'vpn_plan_family',
}

// ── Plan card ─────────────────────────────────────────────────────────────────

function PlanCard({
  plan, mode, upgradePrice, loading, isPending, onClick, animDelay,
}: {
  plan: Plan; mode: 'buy' | 'current' | 'upgrade' | 'downgrade' | 'pending'
  upgradePrice: number; loading: boolean; isPending: boolean
  onClick: () => void; animDelay?: number
}) {
  const t = useT()
  const p = usePlural()
  const tp     = WebApp.themeParams
  const accent = 'var(--tg-theme-button-color, #2481cc)'
  const isHit  = plan.badge === 'hit' && mode === 'buy'
  const isCurrent = mode === 'current'
  const planIcon = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_start

  let btn: React.ReactNode = null
  if (mode === 'buy') {
    btn = (
      <button className="btn" disabled={loading} onClick={onClick} style={{ minWidth: 84, fontSize: 13 }}>
        {loading ? '…' : `${plan.rub} ₽`}
      </button>
    )
  } else if (mode === 'current') {
    btn = (
      <span style={{ fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 20, background: `${accent}18`, color: accent }}>
        {t('plans_yours')}
      </span>
    )
  } else if (mode === 'upgrade') {
    btn = <button className="btn" disabled={loading} onClick={onClick} style={{ minWidth: 84, fontSize: 13 }}>{loading ? '…' : `+${upgradePrice} ₽`}</button>
  } else if (mode === 'pending') {
    btn = (
      <button disabled={loading} onClick={onClick} style={{
        padding: '7px 14px', borderRadius: 10, border: 'none', cursor: 'pointer',
        background: 'rgba(230,126,34,0.15)', color: '#e67e22', fontSize: 13, fontWeight: 600,
      }}>{loading ? '…' : t('plans_cancel')}</button>
    )
  } else {
    btn = (
      <button disabled={loading} onClick={onClick} style={{
        padding: '7px 14px', borderRadius: 10,
        border: '1.5px solid rgba(128,128,128,0.2)',
        background: 'transparent', color: tp.hint_color,
        fontSize: 13, fontWeight: 500, cursor: 'pointer',
      }}>{loading ? '…' : t('plans_downgrade')}</button>
    )
  }

  return (
    <div
      className={`fade-in${animDelay ? ` fade-in-${animDelay}` : ''}`}
      style={{
        borderRadius: 16,
        border: isCurrent
          ? `2px solid ${accent}`
          : isPending
            ? '2px solid rgba(230,126,34,0.45)'
            : isHit
              ? `2px solid ${accent}88`
              : '2px solid transparent',
        background: isCurrent ? `${accent}0a` : isHit ? `${accent}06` : 'var(--section-bg)',
        padding: '14px 16px',
        display: 'flex', alignItems: 'center', gap: 14,
      }}
    >
      <div style={{
        width: 44, height: 44, borderRadius: 13, flexShrink: 0,
        background: planIcon.bg,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: `0 4px 12px ${planIcon.bg}55`,
      }}>
        {planIcon.icon}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 16, color: tp.text_color }}>{t(PLAN_NAME_KEY[plan.key])}</span>
          {isHit && (
            <span style={{ background: accent, color: tp.button_text_color ?? '#fff', fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20 }}>{t('plans_hit')}</span>
          )}
          {isPending && (
            <span style={{ background: 'rgba(230,126,34,0.15)', color: '#e67e22', fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20 }}>{t('plans_next_month')}</span>
          )}
        </div>
        <div style={{ fontSize: 13, color: tp.hint_color }}>
          <span style={{ fontWeight: 600, color: tp.text_color }}>{plan.rub} ₽</span>
          <span style={{ opacity: 0.4, margin: '0 4px' }}>·</span>
          <span style={{ fontSize: 12 }}>📱 {p(plan.awg, { ru: ['устройство', 'устройства', 'устройств'], en: 'devices' })}{plan.vless > 0 ? ` · ${t('plans_smarttv')}` : ''}</span>
        </div>
      </div>

      {btn}
    </div>
  )
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonPage() {
  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)', gap: 12 }}>
      <div style={{ height: 8 }} />
      {[140, 80, 80, 80, 80].map((h, i) => (
        <div key={i} className="skeleton" style={{ height: h }} />
      ))}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

type PageStatus = 'idle' | 'paid' | 'error'

export default function Plans() {
  const nav      = useNavigate()
  const location = useLocation()
  const tp       = WebApp.themeParams
  const t        = useT()

  const [sub,        setSub]        = useState<Subscription | null | undefined>(undefined)
  const [loading,    setLoading]    = useState<string | null>(null)
  const [pageStatus, setPageStatus] = useState<PageStatus>('idle')
  const [errMsg,     setErrMsg]     = useState('')
  const [sheetPlan,  setSheetPlan]  = useState<Plan | null>(null)

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    getActiveSubscription().then(sub => {
      setSub(sub)
      const preselect = (location.state as { planKey?: string } | null)?.planKey
      if (preselect && !sub) {
        const plan = PLANS.find(p => p.key === preselect)
        if (plan) setSheetPlan(plan)
      }
    }).catch(() => setSub(null))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav, location.state])

  const handleBuy = async (plan: Plan, method: PayMethod) => {
    setSheetPlan(null)
    if (loading) return
    WebApp.HapticFeedback.impactOccurred('light')
    setLoading(plan.key); setPageStatus('idle')
    try {
      if (method === 'stars') {
        const { invoice_url } = await createVpnInvoice(plan.key)
        WebApp.openInvoice(invoice_url, (s) => {
          setLoading(null)
          if (s === 'paid') { WebApp.HapticFeedback.notificationOccurred('success'); setPageStatus('paid') }
          else if (s !== 'cancelled') { setPageStatus('error'); setErrMsg(t('plans_error_payment')) }
        })
      } else {
        const { pay_url } = await createVpnInvoiceCrypto(plan.key, 'RUB')
        setLoading(null)
        WebApp.openLink(pay_url)
      }
    } catch (e) {
      setLoading(null); setPageStatus('error')
      setErrMsg(e instanceof Error ? e.message : t('plans_error_server'))
    }
  }

  const handleChange = async (plan: Plan) => {
    if (loading || !sub) return
    WebApp.HapticFeedback.impactOccurred('light')
    setLoading(plan.key); setPageStatus('idle')
    try {
      const res = await changeSubscriptionPlan(plan.key)
      if (res.invoice_url) {
        setLoading(null)
        WebApp.openLink(res.invoice_url)
      } else if (res.scheduled) {
        WebApp.HapticFeedback.notificationOccurred('success')
        setSub(prev => prev ? { ...prev, pending_plan: plan.key } : prev)
        setLoading(null)
      } else if (res.cancelled) {
        WebApp.HapticFeedback.impactOccurred('light')
        setSub(prev => prev ? { ...prev, pending_plan: null } : prev)
        setLoading(null)
      } else { setLoading(null) }
    } catch (e) {
      setLoading(null); setPageStatus('error')
      setErrMsg(e instanceof Error ? e.message : t('plans_error_server'))
    }
  }

  if (pageStatus === 'paid') {
    return (
      <div className="page">
        <div className="center">
          <div style={{
            width: 72, height: 72, borderRadius: 22, marginBottom: 4,
            background: 'rgba(39,174,96,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 36,
          }}>✅</div>
          <div style={{ fontWeight: 800, fontSize: 22, color: tp.text_color }}>{t('plans_done')}</div>
          <p style={{ color: tp.hint_color, fontSize: 14 }}>{t('plans_done_sub')}</p>
          <button className="btn" style={{ width: '100%', marginBottom: 10 }} onClick={() => nav('/configs')}>{t('plans_my_configs')}</button>
          <button className="btn" style={{ width: '100%', background: 'var(--section-bg)', color: tp.text_color }}
            onClick={() => { setPageStatus('idle'); getActiveSubscription().then(setSub) }}>
            {t('plans_back')}
          </button>
        </div>
      </div>
    )
  }

  if (sub === undefined) return <SkeletonPage />

  return (
    <>
      <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)' }}>
        <div style={{ padding: '6px 4px 2px' }}>
          <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>{t('plans_title')}</div>
          <div style={{ fontSize: 13, color: tp.hint_color }}>{t('plans_sub')}</div>
        </div>

        {sub === null ? (
          PLANS.map((plan, i) => (
            <PlanCard key={plan.key} plan={plan} mode="buy"
              upgradePrice={0} loading={loading === plan.key}
              isPending={false} animDelay={i + 1}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }} />
          ))
        ) : (
          (() => {
            const curPlan = PLANS.find(p => p.key === sub.plan)!
            return PLANS.map((plan, i) => {
              const isPending = sub.pending_plan === plan.key
              let mode: 'current' | 'upgrade' | 'downgrade' | 'pending'
              if (plan.key === curPlan.key) mode = 'current'
              else if (plan.stars > curPlan.stars) mode = 'upgrade'
              else if (isPending) mode = 'pending'
              else mode = 'downgrade'

              return (
                <PlanCard key={plan.key} plan={plan} mode={mode}
                  upgradePrice={mode === 'upgrade' ? calcUpgradePrice(curPlan.rub, plan.rub, sub.days_remaining) : 0}
                  loading={loading === plan.key} isPending={isPending} animDelay={i + 1}
                  onClick={() => handleChange(plan)} />
              )
            })
          })()
        )}

        {pageStatus === 'error' && (
          <p style={{ color: 'var(--tg-theme-destructive-text-color, #ff3b30)', textAlign: 'center', fontSize: 14 }}>{errMsg}</p>
        )}
        <Legend />
      </div>

      {sheetPlan && (
        <PaymentSheet
          plan={sheetPlan}
          onClose={() => setSheetPlan(null)}
          onPay={(method) => handleBuy(sheetPlan, method)}
        />
      )}
    </>
  )
}

function Legend() {
  const t = useT()
  const tp = WebApp.themeParams
  return (
    <div style={{
      background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 12,
      padding: '12px 16px', marginTop: 8, fontSize: 12,
      color: tp.hint_color, lineHeight: 1.7,
    }}>
      <span style={{ color: '#27ae60', fontWeight: 600 }}>{t('plans_legend_dev')}</span> {t('plans_legend_dev_s')}<br />
      <span style={{ color: '#8e44ad', fontWeight: 600 }}>{t('plans_legend_tv')}</span> {t('plans_legend_tv_s')}
    </div>
  )
}