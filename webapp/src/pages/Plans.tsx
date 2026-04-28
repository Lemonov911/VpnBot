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

const PLAN_TW: Record<string, { bg: string; shadow: string }> = {
  vpn_start:   { bg: 'bg-info',       shadow: 'shadow-[0_4px_12px_rgba(90,200,250,0.55)]' },
  vpn_popular: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  vpn_pro:     { bg: 'bg-[#5856d6]',   shadow: 'shadow-[0_4px_12px_rgba(88,86,214,0.55)]' },
  vpn_family:  { bg: 'bg-[#ff2d55]',   shadow: 'shadow-[0_4px_12px_rgba(255,45,85,0.55)]' },
}

const PLAN_NAME_KEY: Record<string, TKey> = {
  vpn_start: 'vpn_plan_start',
  vpn_popular: 'vpn_plan_popular',
  vpn_pro: 'vpn_plan_pro',
  vpn_family: 'vpn_plan_family',
}

function PlanCard({
  plan, mode, upgradePrice, loading, isPending, onClick, animDelay,
}: {
  plan: Plan; mode: 'buy' | 'current' | 'upgrade' | 'downgrade' | 'pending'
  upgradePrice: number; loading: boolean; isPending: boolean
  onClick: () => void; animDelay?: number
}) {
  const t = useT()
  const p = usePlural()
  const isHit = plan.badge === 'hit' && mode === 'buy'
  const isCurrent = mode === 'current'
  const planIcon = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_start
  const tw = PLAN_TW[plan.key] ?? PLAN_TW.vpn_start

  const borderClass = isCurrent
    ? 'border-2 border-[var(--tg-theme-button-color,#2481cc)]'
    : isPending
      ? 'border-2 border-warning/45'
      : isHit
        ? 'border-2 border-primary/50'
        : 'border-2 border-transparent'

  const bgClass = isCurrent
    ? 'bg-primary/[0.04]'
    : isHit
      ? 'bg-primary/[0.03]'
      : 'bg-[var(--tg-theme-section-bg-color,#f1f1f1)]'

  let btn: React.ReactNode = null
  if (mode === 'buy') {
    btn = (
      <button className="btn !min-w-[84px] !text-[13px]" disabled={loading} onClick={onClick}>
        {loading ? '…' : `${plan.rub} ₽`}
      </button>
    )
  } else if (mode === 'current') {
    btn = (
      <span className="text-xs font-bold px-3 py-[5px] rounded-[20px] bg-primary/10 text-[var(--tg-theme-button-color,#2481cc)]">
        {t('plans_yours')}
      </span>
    )
  } else if (mode === 'upgrade') {
    btn = <button className="btn !min-w-[84px] !text-[13px]" disabled={loading} onClick={onClick}>{loading ? '…' : `+${upgradePrice} ₽`}</button>
  } else if (mode === 'pending') {
    btn = (
      <button disabled={loading} onClick={onClick} className="px-3.5 py-[7px] rounded-[10px] border-none cursor-pointer bg-warning/15 text-warning text-[13px] font-semibold">
        {loading ? '…' : t('plans_cancel')}
      </button>
    )
  } else {
    btn = (
      <button disabled={loading} onClick={onClick} className="px-3.5 py-[7px] rounded-[10px] border-[1.5px] border-gray-500/20 bg-transparent text-[var(--tg-theme-hint-color,#707579)] text-[13px] font-medium cursor-pointer">
        {loading ? '…' : t('plans_downgrade')}
      </button>
    )
  }

  return (
    <div
      className={`fade-in${animDelay ? ` fade-in-${animDelay}` : ''} rounded-2xl ${borderClass} ${bgClass} p-[14px_16px] flex items-center gap-3.5`}
    >
      <div className={`w-11 h-11 rounded-[13px] shrink-0 flex items-center justify-center ${tw.bg} ${tw.shadow}`}>
        {planIcon.icon}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-[7px] mb-[3px] flex-wrap">
          <span className="font-bold text-base text-[var(--tg-theme-text-color,#000)]">{t(PLAN_NAME_KEY[plan.key])}</span>
          {isHit && (
            <span className="bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[10px] font-bold px-[7px] py-[2px] rounded-[20px]">{t('plans_hit')}</span>
          )}
          {isPending && (
            <span className="bg-warning/15 text-warning text-[10px] font-bold px-[7px] py-[2px] rounded-[20px]">{t('plans_next_month')}</span>
          )}
        </div>
        <div className="text-[13px] text-[var(--tg-theme-hint-color,#707579)]">
          <span className="font-semibold text-[var(--tg-theme-text-color,#000)]">{plan.rub} ₽</span>
          <span className="opacity-40 mx-1">·</span>
          <span className="text-xs">📱 {p(plan.awg, { ru: ['устройство', 'устройства', 'устройств'], en: ['device', 'devices'] })}{plan.vless > 0 ? ` · ${t('plans_smarttv')}` : ''}</span>
        </div>
      </div>

      {btn}
    </div>
  )
}

function SkeletonPage() {
  return (
    <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-3">
      <div className="h-2" />
      {[140, 80, 80, 80, 80].map((h, i) => (
        <div key={i} className="skeleton rounded-xl" style={{ height: h }} />
      ))}
    </div>
  )
}

type PageStatus = 'idle' | 'paid' | 'error'

export default function Plans() {
  const nav      = useNavigate()
  const location = useLocation()
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
          <div className="w-[72px] h-[72px] rounded-[22px] mb-1 bg-success/12 flex items-center justify-center text-[36px]">✅</div>
          <div className="font-extrabold text-[22px] text-[var(--tg-theme-text-color,#000)]">{t('plans_done')}</div>
          <p className="text-[var(--tg-theme-hint-color,#707579)] text-sm">{t('plans_done_sub')}</p>
          <button className="btn w-full mb-2.5" onClick={() => nav('/configs')}>{t('plans_my_configs')}</button>
          <button className="btn w-full !bg-[var(--tg-theme-section-bg-color,#f1f1f1)] !text-[var(--tg-theme-text-color,#000)]"
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
      <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)]">
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
          <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center text-sm">{errMsg}</p>
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
  return (
    <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-xl py-3 px-4 mt-2 text-xs text-[var(--tg-theme-hint-color,#707579)] leading-[1.7]">
      <span className="text-success font-semibold">{t('plans_legend_dev')}</span> {t('plans_legend_dev_s')}<br />
      <span className="text-purple font-semibold">{t('plans_legend_tv')}</span> {t('plans_legend_tv_s')}
    </div>
  )
}