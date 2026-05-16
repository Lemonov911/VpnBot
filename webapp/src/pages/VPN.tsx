import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  createVpnInvoice, createVpnInvoiceCrypto, createVpnInvoiceCryptomus, createVpnInvoiceLavatop, cancelLavatopRenewal,
  getActiveSubscription, getUserConfigs, getVpnStatus,
  type Subscription, type VpnConfig, type VpnServerStatus,
} from '../api'
import { useT, usePlural } from '../i18n'
import PaymentSheet, { PLANS, VISIBLE_PLANS, starsPlanKey, type Plan, type PayMethod, type StarsPeriod } from '../components/PaymentSheet'
import PostPayOnboarding from '../components/PostPayOnboarding'
import { SubscriptionUrlCard } from '../components/SubscriptionUrlCard'

const PLAN_ICONS: Record<string, { bg: string; icon: JSX.Element }> = {
  // v2 — по скорости
  vpn_base: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_max:  { bg: '#af52de', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M13 2L3 14h7v8l10-12h-7V2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  // legacy
  vpn_start:   { bg: '#5ac8fa', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_popular: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_pro:     { bg: '#5856d6', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="#ffffff33" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_family:  { bg: '#ff2d55', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3" stroke="#fff" strokeWidth="2"/><path d="M3 19c0-3 2.686-5 6-5s6 2 6 5" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M21 19c0-2.5-1.8-4-4-4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
}

const PLAN_TW: Record<string, { bg: string; shadow: string }> = {
  vpn_base: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  /* glow-pulse — дышащая тень на Max-иконке.  См. index.css. */
  vpn_max:  { bg: 'bg-[#af52de]',  shadow: 'glow-pulse' },
  // legacy
  vpn_start:   { bg: 'bg-info',       shadow: 'shadow-[0_4px_12px_rgba(90,200,250,0.55)]' },
  vpn_popular: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  vpn_pro:     { bg: 'bg-[#5856d6]',   shadow: 'shadow-[0_4px_12px_rgba(88,86,214,0.55)]' },
  vpn_family:  { bg: 'bg-[#ff2d55]',   shadow: 'shadow-[0_4px_12px_rgba(255,45,85,0.55)]' },
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) }
  catch { return iso }
}

function ExpiryBar({ daysLeft, t }: { daysLeft: number; t: ReturnType<typeof useT> }) {
  const pct = Math.max(4, Math.min(100, Math.round(daysLeft / 30 * 100)))
  const barColor = daysLeft <= 5 ? 'bg-danger' : daysLeft <= 10 ? 'bg-warning' : 'bg-success'
  const textColor = daysLeft <= 5 ? 'text-danger' : daysLeft <= 10 ? 'text-warning' : 'text-success'
  return (
    <div className="mt-2.5">
      <div className="flex justify-between text-[11px] mb-1">
        <span className="text-[var(--tg-theme-hint-color,#707579)] opacity-70">{t('vpn_expiry_label')}</span>
        <span className={`font-semibold ${textColor}`}>{daysLeft} {t('vpn_expiry_left')}</span>
      </div>
      <div className="h-1 rounded-full bg-gray-500/15">
        <div className={`h-full rounded-full transition-[width] duration-400 ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function SlotDots({ active, total, color }: { active: number; total: number; color: string }) {
  const dotColor = color === '#27ae60' ? 'bg-success' : 'bg-purple'
  return (
    <span className="inline-flex gap-1 items-center">
      {Array.from({ length: total }).map((_, i) => (
        <span key={i} className={`w-2 h-2 rounded-full transition-colors duration-200 ${i < active ? dotColor : 'bg-gray-500/20'}`} />
      ))}
    </span>
  )
}

function SkeletonPage() {
  return (
    <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">
      <div className="h-4" />
      <div className="skeleton h-40 rounded-[18px]" />
      <div className="skeleton h-[60px] rounded-xl" />
      <div className="skeleton h-[60px] rounded-xl" />
      <div className="skeleton h-[60px] rounded-xl" />
    </div>
  )
}

export default function VPN() {
  const nav = useNavigate()
  const t   = useT()
  const p   = usePlural()

  const PLAN_NAMES: Record<string, string> = {
    vpn_base:    t('vpn_plan_base'),
    vpn_max:     t('vpn_plan_max'),
    vpn_trial:   t('vpn_plan_trial'),
    vpn_start:   t('vpn_plan_start'),
    vpn_popular: t('vpn_plan_popular'),
    vpn_pro:     t('vpn_plan_pro'),
    vpn_family:  t('vpn_plan_family'),
  }

  const [sub,        setSub]        = useState<Subscription | null | undefined>(undefined)
  const [configs,    setConfigs]    = useState<VpnConfig[] | null>(null)
  const [status,     setStatus]     = useState<VpnServerStatus[] | null>(null)
  const [sheetPlan,  setSheetPlan]  = useState<Plan | null>(null)
  const [buyLoading, setBuyLoading] = useState<string | null>(null)
  const [paid,       setPaid]       = useState(false)
  const [cancelLoading, setCancelLoading] = useState(false)
  const [postPayOpen, setPostPayOpen]     = useState(false)

  const handleCancelRenewal = async () => {
    if (cancelLoading || !sub) return
    // Подтверждение через нативный confirm — экономнее чем модалка, юзеру понятно
    const ok = window.confirm(t('vpn_cancel_renewal_confirm' as never))
    if (!ok) return
    setCancelLoading(true)
    try {
      await cancelLavatopRenewal()
      WebApp.HapticFeedback.notificationOccurred('success')
      // Подтянуть свежее состояние sub — auto_renew теперь false
      const fresh = await getActiveSubscription().catch(() => null)
      setSub(fresh)
      const msg = (t('vpn_cancel_renewal_done' as never))
        .replace('{date}', fresh ? formatDate(fresh.expires_at) : '')
      WebApp.showAlert(msg)
    } catch {
      WebApp.HapticFeedback.notificationOccurred('error')
      WebApp.showAlert(t('vpn_cancel_renewal_err' as never))
    } finally {
      setCancelLoading(false)
    }
  }

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/')
    WebApp.BackButton.onClick(goBack)
    Promise.all([
      getActiveSubscription().catch(() => null),
      getUserConfigs().catch(() => [] as VpnConfig[]),
      getVpnStatus().catch(() => null),
    ]).then(([s, c, st]) => { setSub(s); setConfigs(c as VpnConfig[]); setStatus(st) })
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const handleBuy = async (plan: Plan, method: PayMethod, starsPeriod?: StarsPeriod, recurring?: boolean) => {
    setSheetPlan(null)
    if (buyLoading) return
    WebApp.HapticFeedback.impactOccurred('light')
    setBuyLoading(plan.key)
    try {
      if (method === 'stars') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const isRecurring = (starsPeriod ?? '1m') === '1m' && !!recurring
        const { invoice_url } = await createVpnInvoice(planKey, isRecurring)
        let callbackFired = false
        // Safety timeout: если Telegram закроется или сеть упадёт до окончания
        // платежа — openInvoice callback может не сработать. Через 5 минут
        // принудительно снимаем loading, иначе кнопка зависнет.
        const guardId = setTimeout(() => {
          if (!callbackFired) setBuyLoading(null)
        }, 5 * 60 * 1000)
        WebApp.openInvoice(invoice_url, s => {
          callbackFired = true
          clearTimeout(guardId)
          setBuyLoading(null)
          if (s === 'paid') { WebApp.HapticFeedback.notificationOccurred('success'); setPaid(true) }
        })
      } else if (method === 'cryptomus') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceCryptomus(planKey, 'RUB')
        setBuyLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else if (method === 'lavatop') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceLavatop(planKey)
        setBuyLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else {
        // CryptoBot (method='crypto') — multi-period one-time invoice
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceCrypto(planKey, 'RUB')
        setBuyLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      }
    } catch {
      setBuyLoading(null)
    }
  }

  if (sub === undefined) return <SkeletonPage />

  if (paid) {
    return (
      <div className="page">
        <div className="center">
          <div className="w-[72px] h-[72px] rounded-[22px] mb-2 bg-success/12 flex items-center justify-center text-[36px]">✅</div>
          <div className="font-extrabold text-[22px] text-[var(--tg-theme-text-color,#000)] mb-1.5">{t('vpn_done_title')}</div>
          <p className="text-[var(--tg-theme-hint-color,#707579)] text-sm mb-5">{t('vpn_done_sub')}</p>
          <button className="btn w-full mb-2.5" onClick={() => nav('/configs')}>{t('vpn_to_configs')}</button>
          {/* UX audit P0: пейщики получали меньше guidance чем триал-юзеры.
              Добавляем Happ install chips чтобы они знали какую программу ставить. */}
          <div className="grid grid-cols-2 gap-2 w-full mb-2.5">
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink('https://apps.apple.com/app/happ-proxy-utility/id6504287215') }}
              className="py-1.5 rounded-[10px] bg-[var(--tg-theme-bg-color,#fff)] text-[var(--tg-theme-text-color)] text-[11px] font-medium cursor-pointer"
              style={{ borderWidth: 1, borderStyle: 'solid', borderColor: 'var(--card-border)' }}
            >
              🍎 {t('trial_install_ios')}
            </button>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink('https://play.google.com/store/apps/details?id=com.happproxy') }}
              className="py-1.5 rounded-[10px] bg-[var(--tg-theme-bg-color,#fff)] text-[var(--tg-theme-text-color)] text-[11px] font-medium cursor-pointer"
              style={{ borderWidth: 1, borderStyle: 'solid', borderColor: 'var(--card-border)' }}
            >
              🤖 {t('trial_install_android')}
            </button>
          </div>
          <button className="btn w-full !bg-[var(--tg-theme-section-bg-color,#f1f1f1)] !text-[var(--tg-theme-text-color,#000)]"
            onClick={() => { setPaid(false); getActiveSubscription().then(setSub) }}>
            {t('vpn_to_plans')}
          </button>
        </div>
      </div>
    )
  }

  if (sub?.status === 'expired') {
    const planName = PLAN_NAMES[sub.plan] ?? sub.plan
    return (
      <>
        <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">
          <div className="fade-in bg-[var(--tg-theme-section-bg-color,#f1f1f1)] rounded-2xl py-4 px-[18px] border border-[var(--card-border)]">
            <div className="flex justify-between items-start">
              <div>
                <div className="text-[11px] text-[var(--tg-theme-hint-color,#707579)] mb-0.5">{t('vpn_expired_title')}</div>
                <div className="font-bold text-[22px] text-[var(--tg-theme-text-color,#000)]">{planName}</div>
                <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-0.5">{t('vpn_expires')} {formatDate(sub.expires_at)}</div>
              </div>
              <span className="bg-danger/12 text-danger text-[11px] font-bold px-2.5 py-1 rounded-[20px] mt-0.5 shrink-0">{t('vpn_expired_badge')}</span>
            </div>
            <p className="text-sm text-[var(--tg-theme-hint-color,#707579)] mt-3 mb-2">{t('vpn_expired_sub')}</p>
            <p className="text-[12px] text-[var(--tg-theme-hint-color,#707579)] mb-3.5 leading-[1.4]">
              {t('vpn_expired_sub_hint')}
            </p>
            <button onClick={() => nav('/vpn/plans')} className="w-full py-2.5 rounded-[10px] border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-sm font-semibold cursor-pointer">
              {t('vpn_expired_renew')}
            </button>
          </div>

          <div className="section-title">{t('vpn_choose')}</div>

          {VISIBLE_PLANS.map((plan, i) => {
            const pi = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_base
            const tw = PLAN_TW[plan.key] ?? PLAN_TW.vpn_base
            const isHit = plan.badge === 'hit'
            return (
              <div key={plan.key} className={`fade-in fade-in-${i + 1} rounded-2xl border-2 p-[14px_16px] flex items-center gap-3.5 ${
                isHit ? 'border-primary/50 bg-primary/[0.03]' : 'border-transparent bg-[var(--tg-theme-section-bg-color,#f1f1f1)]'
              }`}>
                <div className={`w-11 h-11 rounded-[13px] shrink-0 flex items-center justify-center ${tw.bg} ${tw.shadow}`}>
                  {pi.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-[7px] mb-[3px] flex-wrap">
                    <span className="font-bold text-base text-[var(--tg-theme-text-color,#000)]">{PLAN_NAMES[plan.key] ?? t(plan.nameKey as never)}</span>
                    {isHit && (
                      <span className="bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[10px] font-bold px-[7px] py-[2px] rounded-[20px]">{t('plans_hit')}</span>
                    )}
                  </div>
                  <div className="text-[13px] text-[var(--tg-theme-hint-color,#707579)]">
                    <span className="font-semibold text-[var(--tg-theme-text-color,#000)]">{plan.rub} ₽</span>
                    <span className="opacity-40 mx-1">·</span>
                    <span className="text-xs">
                      ⚡ {plan.speedMbps} Mbps<span className="opacity-40 mx-1">·</span>
                      📱 {plan.vless} VLESS
                      {plan.wg ? (
                        <>
                          <span className="opacity-40 mx-1">·</span>
                          🔐 {plan.wg} WireGuard
                        </>
                      ) : null}
                    </span>
                  </div>
                </div>
                <button
                  disabled={buyLoading === plan.key}
                  onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }}
                  className={`py-2 px-4 rounded-xl border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[13px] font-semibold cursor-pointer shrink-0 ${buyLoading === plan.key ? 'opacity-60' : ''}`}
                >
                  {buyLoading === plan.key ? '…' : `${plan.rub} ₽`}
                </button>
              </div>
            )
          })}
        </div>

        {sheetPlan && (
          <PaymentSheet
            plan={sheetPlan}
            onClose={() => setSheetPlan(null)}
            onPay={(method, period, recurring) => handleBuy(sheetPlan, method, period, recurring)}
            /* Эти PaymentSheet'ы рендерятся в ветках sub===null и
               sub.status==='expired' — триал-юзеру они недоступны
               (триал имеет status='active'). Hardcode false. */
            hasActiveTrial={false}
            defaultMethod="crypto"
          />
        )}

        {/* Loading overlay — пока ждём ответа от платёжного API. */}
        {buyLoading && !sheetPlan && (
          <div className="fixed inset-0 z-[150] bg-black/60 backdrop-blur-sm flex items-center justify-center px-6">
            <div className="bg-[var(--tg-theme-bg-color,#fff)] rounded-2xl py-7 px-8 flex flex-col items-center gap-3 max-w-[280px]">
              <div className="w-9 h-9 rounded-full border-[3px] border-[var(--tg-theme-button-color,#2481cc)] border-t-transparent animate-spin" />
              <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color,#000)] text-center">
                {t('pay_loading' as never)}
              </div>
            </div>
          </div>
        )}

        {postPayOpen && (
          <PostPayOnboarding
            onClose={() => setPostPayOpen(false)}
            onGoConfigs={() => { setPostPayOpen(false); nav('/configs') }}
          />
        )}
      </>
    )
  }

  if (sub === null) {
    return (
      <>
        <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">

          <div className="section-title">
            {t('vpn_choose')}
          </div>

          {VISIBLE_PLANS.map((plan, i) => {
            const pi = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_base
            const tw = PLAN_TW[plan.key] ?? PLAN_TW.vpn_base
            const isHit = plan.badge === 'hit'
            return (
              <div key={plan.key} className={`fade-in fade-in-${i + 1} rounded-2xl border-2 p-[14px_16px] flex items-center gap-3.5 ${
                isHit ? 'border-primary/50 bg-primary/[0.03]' : 'border-transparent bg-[var(--tg-theme-section-bg-color,#f1f1f1)]'
              }`}>
                <div className={`w-11 h-11 rounded-[13px] shrink-0 flex items-center justify-center ${tw.bg} ${tw.shadow}`}>
                  {pi.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-[7px] mb-[3px] flex-wrap">
                    <span className="font-bold text-base text-[var(--tg-theme-text-color,#000)]">{PLAN_NAMES[plan.key] ?? t(plan.nameKey as never)}</span>
                    {isHit && (
                      <span className="bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[10px] font-bold px-[7px] py-[2px] rounded-[20px]">{t('plans_hit')}</span>
                    )}
                  </div>
                  <div className="text-[13px] text-[var(--tg-theme-hint-color,#707579)]">
                    <span className="font-semibold text-[var(--tg-theme-text-color,#000)]">{plan.rub} ₽</span>
                    <span className="opacity-40 mx-1">·</span>
                    <span className="text-xs">
                      ⚡ {plan.speedMbps} Mbps<span className="opacity-40 mx-1">·</span>
                      📱 {plan.vless} VLESS
                      {plan.wg ? (
                        <>
                          <span className="opacity-40 mx-1">·</span>
                          🔐 {plan.wg} WireGuard
                        </>
                      ) : null}
                    </span>
                  </div>
                </div>
                <button
                  disabled={buyLoading === plan.key}
                  onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }}
                  className={`py-2 px-4 rounded-xl border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-[13px] font-semibold cursor-pointer shrink-0 ${buyLoading === plan.key ? 'opacity-60' : ''}`}
                >
                  {buyLoading === plan.key ? '…' : `${plan.rub} ₽`}
                </button>
              </div>
            )
          })}

          <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-xl py-3 px-4 text-xs text-[var(--tg-theme-hint-color,#707579)] leading-[1.7]">
            <span className="text-success font-semibold">{t('plans_legend_dev')}</span> {t('plans_legend_dev_s')}
          </div>

          <div className="section-title">
            {t('vpn_how_title')}
          </div>
          <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-2xl overflow-hidden divide-y divide-gray-500/10">
            {[
              { num: '1', color: 'bg-primary', title: t('vpn_how1'), sub: t('vpn_how1_sub') },
              { num: '2', color: 'bg-success', title: t('vpn_how2'), sub: t('vpn_how2_sub') },
              { num: '3', color: 'bg-purple', title: t('vpn_how3'), sub: t('vpn_how3_sub') },
            ].map(({ num, color, title, sub }) => (
              <div key={num} className="py-[13px] px-4 flex items-center gap-3.5">
                <div className={`w-9 h-9 rounded-[10px] shrink-0 ${color} flex items-center justify-center font-extrabold text-base text-white`}>{num}</div>
                <div>
                  <div className="text-sm font-semibold text-[var(--tg-theme-text-color,#000)] leading-[1.3]">{title}</div>
                  <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-[1px]">{sub}</div>
                </div>
              </div>
            ))}
          </div>

        </div>

        {sheetPlan && (
          <PaymentSheet
            plan={sheetPlan}
            onClose={() => setSheetPlan(null)}
            onPay={(method, period, recurring) => handleBuy(sheetPlan, method, period, recurring)}
            /* Эти PaymentSheet'ы рендерятся в ветках sub===null и
               sub.status==='expired' — триал-юзеру они недоступны
               (триал имеет status='active'). Hardcode false. */
            hasActiveTrial={false}
            defaultMethod="crypto"
          />
        )}

        {/* Loading overlay — пока ждём ответа от платёжного API. */}
        {buyLoading && !sheetPlan && (
          <div className="fixed inset-0 z-[150] bg-black/60 backdrop-blur-sm flex items-center justify-center px-6">
            <div className="bg-[var(--tg-theme-bg-color,#fff)] rounded-2xl py-7 px-8 flex flex-col items-center gap-3 max-w-[280px]">
              <div className="w-9 h-9 rounded-full border-[3px] border-[var(--tg-theme-button-color,#2481cc)] border-t-transparent animate-spin" />
              <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color,#000)] text-center">
                {t('pay_loading' as never)}
              </div>
            </div>
          </div>
        )}

        {postPayOpen && (
          <PostPayOnboarding
            onClose={() => setPostPayOpen(false)}
            onGoConfigs={() => { setPostPayOpen(false); nav('/configs') }}
          />
        )}
      </>
    )
  }

  const planName    = PLAN_NAMES[sub.plan] ?? sub.plan
  const pendingName = sub.pending_plan ? (PLAN_NAMES[sub.pending_plan] ?? sub.pending_plan) : null
  const isGrace     = sub.status === 'grace'
  const isExpiring  = !isGrace && sub.days_remaining <= 7

  // "Устройства" = per-device файлы (AWG + plain WG). VLESS теперь общая подписка
  // и не считается «устройством» — один sub-URL юзер импортирует на любое число
  // устройств.  Поэтому здесь только AWG/WG-слоты.
  const deviceTotal  = configs?.filter(c => c.protocol === 'awg' || c.protocol === 'wg').length ?? 0
  const deviceActive = configs?.filter(c => (c.protocol === 'awg' || c.protocol === 'wg') && c.status === 'active').length ?? 0

  return (
    <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)] gap-2.5">

      {isGrace && (
        <div className="fade-in rounded-xl py-3 px-3.5 flex justify-between items-center border bg-danger/10 border-danger/30">
          <div className="min-w-0 pr-2">
            <div className="text-[13px] font-semibold text-danger">
              🐢 {t('vpn_grace_banner_title')}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-0.5">
              {t('vpn_grace_banner_body')
                .replace('{days}', String(sub.grace_days_left ?? 0))
                .replace('{plural}', p(sub.grace_days_left ?? 0, { ru: [t('vpn_day_left_1'), t('vpn_day_left_2'), t('days')], en: ['day', 'days'] }))
              }
            </div>
          </div>
          <button onClick={() => nav('/vpn/plans')} className="px-3.5 py-1.5 rounded-lg border-none bg-danger text-white text-xs font-semibold cursor-pointer shrink-0">
            {t('vpn_renew')}
          </button>
        </div>
      )}

      {isExpiring && (
        <div className={`fade-in rounded-xl py-[10px] px-3.5 flex justify-between items-center border ${
          sub.days_remaining <= 3 ? 'bg-danger/10 border-danger/30' : 'bg-warning/10 border-warning/30'
        }`}>
          <div>
            <div className={`text-[13px] font-semibold ${sub.days_remaining <= 3 ? 'text-danger' : 'text-warning'}`}>
              {sub.days_remaining <= 3 ? t('vpn_expiry_banner_1') : t('vpn_expiry_banner_3')}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-0.5">
              {/* usePlural уже включает число в результат («2 дня»), поэтому
                  отдельно sub.days_remaining не выводим — иначе «Осталось 2 2 дня». */}
              {t('vpn_days_left')} {p(sub.days_remaining, { ru: [t('vpn_day_left_1'), t('vpn_day_left_2'), t('days')], en: ['day', 'days'] })}
            </div>
          </div>
          <button onClick={() => nav('/vpn/plans')} className={`px-3.5 py-1.5 rounded-lg border-none text-white text-xs font-semibold cursor-pointer shrink-0 ${sub.days_remaining <= 3 ? 'bg-danger' : 'bg-warning'}`}>
            {t('vpn_renew')}
          </button>
        </div>
      )}

      <div className="fade-in-1 fade-in bg-[var(--tg-theme-section-bg-color,#f1f1f1)] rounded-2xl py-4 px-[18px] border border-[var(--card-border)]">
        <div className="flex justify-between items-start">
          <div>
            <div className="text-[11px] text-[var(--tg-theme-hint-color,#707579)] mb-0.5">{t('vpn_active_label')}</div>
            <div className="font-bold text-[22px] text-[var(--tg-theme-text-color,#000)]">{planName}</div>
            <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-0.5">{t('vpn_expires')} {formatDate(sub.expires_at)}</div>
          </div>
          <span className={`text-[11px] font-bold px-2.5 py-1 rounded-[20px] mt-0.5 shrink-0 ${
            isGrace ? 'bg-danger/13 text-danger' : 'bg-success/13 text-success'
          }`}>
            {isGrace ? t('vpn_grace_badge') : t('vpn_active_badge')}
          </span>
        </div>

        {deviceTotal > 0 && (
          <div className="mt-3.5">
            <div className="text-[10px] text-[var(--tg-theme-hint-color,#707579)] mb-1 uppercase tracking-[0.4px]">{t('vpn_slots_devs')}</div>
            <SlotDots active={deviceActive} total={deviceTotal} color="#06b6d4" />
            <div className="text-[11px] text-[var(--tg-theme-hint-color,#707579)] mt-[3px]">{deviceActive} / {deviceTotal} {t('vpn_connected')}</div>
          </div>
        )}

        <ExpiryBar daysLeft={sub.days_remaining} t={t} />

        {pendingName && (
          <div className="mt-3 p-[8px_10px] rounded-lg bg-warning/10 flex items-center gap-2">
            <span className="text-sm">⏳</span>
            <span className="text-xs text-warning">
              {t('vpn_pending_change')} <b>«{pendingName}»</b> {t('vpn_pending_next')}
            </span>
          </div>
        )}

        {/* Auto-renewal status. Показывается для Lava recurring (parent_contract_id)
            И для Stars subscription (payment_provider='stars' + auto_renew=True).
            Cancel-flow отличается:
              - Lava: дёргаем backend → Lava API cancel + disable_auto_renew
              - Stars: можем только подсказать юзеру отменить в Telegram UI
                (Settings → Stars and Premium → подписка → Cancel)
        */}
        {sub.auto_renew && (sub.payment_provider === 'lavatop' || sub.payment_provider === 'stars') && (
          <div className="mt-3">
            <div className="p-[10px_12px] rounded-lg bg-success/10 border border-success/20 flex items-start gap-2.5">
              <span className="text-base shrink-0">🔁</span>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-semibold text-success">
                  {t('vpn_autorenew_on' as never)}
                </div>
                <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-0.5">
                  {(t('vpn_autorenew_next' as never)).replace('{date}', formatDate(sub.expires_at))}
                </div>
                {sub.payment_provider === 'lavatop' ? (
                  <button
                    onClick={() => handleCancelRenewal()}
                    disabled={cancelLoading}
                    className="mt-1.5 text-[11px] underline text-[var(--tg-theme-link-color,#2481cc)] disabled:opacity-60"
                  >
                    {cancelLoading ? '...' : t('vpn_cancel_renewal' as never)}
                  </button>
                ) : (
                  <div className="mt-1.5 text-[11px] text-[var(--tg-theme-hint-color)]">
                    {t('vpn_cancel_renewal_stars_hint' as never)}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        {sub.payment_provider === 'lavatop' && sub.parent_contract_id && !sub.auto_renew && (
          <div className="mt-3 p-[8px_10px] rounded-lg bg-[var(--tg-theme-section-bg-color)]/60 flex items-center gap-2">
            <span className="text-sm">❎</span>
            <span className="text-[11px] text-[var(--tg-theme-hint-color)]">
              {t('vpn_autorenew_off' as never)}
            </span>
          </div>
        )}

        <button onClick={() => nav('/vpn/plans')} className="press-fb mt-3.5 w-full py-2.5 rounded-[10px] border-none bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] text-sm font-semibold cursor-pointer">
          {t('vpn_change')}
        </button>
      </div>

      {sub.sub_url && <SubscriptionUrlCard subUrl={sub.sub_url} />}

      <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-2xl overflow-hidden divide-y divide-gray-500/10">
        {[
          {
            color: 'bg-success',
            icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><rect x="9" y="3" width="6" height="4" rx="1" stroke="#fff" strokeWidth="2"/><path d="M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg>,
            title: t('vpn_my_configs'), sub: t('vpn_wg_profiles'),
            action: () => { WebApp.HapticFeedback.impactOccurred('light'); nav('/configs') },
          },
          {
            color: 'bg-purple',
            icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="#fff" strokeWidth="2"/><path d="M12 8v4l3 3" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg>,
            title: t('vpn_instr'), sub: t('vpn_connect_guide'),
            action: () => { WebApp.HapticFeedback.impactOccurred('light'); nav('/instructions') },
          },
        ].map(({ color, icon, title, sub, action }) => (
          <button key={title} onClick={action} className="w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-center gap-3.5">
            <div className={`w-9 h-9 rounded-[10px] shrink-0 ${color} flex items-center justify-center`}>{icon}</div>
            <div className="flex-1 text-left">
              <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color,#000)]">{title}</div>
              <div className="text-xs text-[var(--tg-theme-hint-color,#707579)] mt-[1px]">{sub}</div>
            </div>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        ))}
      </div>

    </div>
  )
}

