import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  createVpnInvoice, createVpnInvoiceCrypto, createVpnInvoiceCryptomus, createVpnInvoiceLavatop, getActiveSubscription, changeSubscriptionPlan,
  type Subscription,
} from '../api'
import PaymentSheet, { PLANS, VISIBLE_PLANS, starsPlanKey, type Plan, type PayMethod, type StarsPeriod } from '../components/PaymentSheet'
import PostPayOnboarding from '../components/PostPayOnboarding'
import { useT } from '../i18n'
import type { TKey } from '../i18n'

function calcUpgradePrice(curRub: number, newRub: number, daysLeft: number): number {
  return Math.max(1, Math.round((newRub - curRub) * daysLeft / 30))
}

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
  /* glow-pulse — дышащая тень на Max-плитке, рекомендованный план.
     Не моргает, едва заметно (см. @keyframes glow-pulse в index.css). */
  vpn_max:  { bg: 'bg-[#af52de]',  shadow: 'glow-pulse' },
  // legacy
  vpn_start:   { bg: 'bg-info',       shadow: 'shadow-[0_4px_12px_rgba(90,200,250,0.55)]' },
  vpn_popular: { bg: 'bg-primary',    shadow: 'shadow-[0_4px_12px_rgba(36,129,204,0.55)]' },
  vpn_pro:     { bg: 'bg-[#5856d6]',   shadow: 'shadow-[0_4px_12px_rgba(88,86,214,0.55)]' },
  vpn_family:  { bg: 'bg-[#ff2d55]',   shadow: 'shadow-[0_4px_12px_rgba(255,45,85,0.55)]' },
}

const PLAN_NAME_KEY: Record<string, TKey> = {
  vpn_base:    'vpn_plan_base',
  vpn_max:     'vpn_plan_max',
  vpn_start:   'vpn_plan_start',
  vpn_popular: 'vpn_plan_popular',
  vpn_pro:     'vpn_plan_pro',
  vpn_family:  'vpn_plan_family',
}

function PlanCard({
  plan, mode, upgradePrice, loading, isPending, onClick, animDelay,
}: {
  plan: Plan; mode: 'buy' | 'current' | 'upgrade' | 'downgrade' | 'pending'
  upgradePrice: number; loading: boolean; isPending: boolean
  onClick: () => void; animDelay?: number
}) {
  const t = useT()
  const isHit = plan.badge === 'hit' && mode === 'buy'
  const isCurrent = mode === 'current'
  const planIcon = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_base
  const tw = PLAN_TW[plan.key] ?? PLAN_TW.vpn_base

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
          <span className="text-xs">
            ⚡ {plan.speedMbps} Mbps<span className="opacity-40 mx-1">·</span>
            {plan.awg ? (
              <>
                🛡 {plan.awg} AmneziaWG
                <span className="opacity-40 mx-1">·</span>
              </>
            ) : null}
            📱 {plan.vless} VLESS
            {plan.wg ? (
              <>
                <span className="opacity-40 mx-1">·</span>
                🔐 {plan.wg} WireGuard
              </>
            ) : null}
          </span>
        </div>
        {/* Soft-cap трафика и throttle после него — показываем явно,
            иначе юзер ловит замедление на 500 ГБ и винит сервис. */}
        <div className="text-[10px] text-[var(--tg-theme-hint-color,#707579)] mt-0.5 opacity-80">
          {t('plans_fair_use')
            .replace('{cap}', String(plan.softCapGb))
            .replace('{throttle}', String(plan.throttleMbps))}
        </div>
      </div>

      <div className="flex flex-col items-end gap-0.5 shrink-0 max-w-[110px]">
        {btn}
        {/* `whitespace-nowrap` убран — на 320px-устройствах подпись «за остаток
            текущего тарифа» шире 100px и переполняла карточку.  Теперь wrap. */}
        {mode === 'upgrade' && upgradePrice > 0 && (
          <div className="text-[9px] text-[var(--tg-theme-hint-color,#707579)] text-right leading-[1.2]">
            {t('plans_upgrade_hint')}
          </div>
        )}
      </div>
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
  // Onboarding overlay показывается после openLink (Lava/Cryptomus/CryptoBot) —
  // юзер ушёл в браузер платить, мы показываем «что произойдёт дальше»
  // чтобы он не запутался при возврате.
  const [postPayOpen, setPostPayOpen] = useState(false)

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    // Защита от unmount-race: если юзер быстро уходит со страницы,
    // pending fetch не должен setState на unmounted component.
    let cancelled = false
    getActiveSubscription().then(sub => {
      if (cancelled) return
      setSub(sub)
      const preselect = (location.state as { planKey?: string } | null)?.planKey
      if (preselect && !sub) {
        const plan = PLANS.find(p => p.key === preselect)
        if (plan) setSheetPlan(plan)
      }
    }).catch(() => { if (!cancelled) setSub(null) })
    return () => {
      cancelled = true
      WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack)
    }
  }, [nav, location.state])

  const handleBuy = async (plan: Plan, method: PayMethod, starsPeriod?: StarsPeriod, recurring?: boolean) => {
    setSheetPlan(null)
    if (loading) return
    WebApp.HapticFeedback.impactOccurred('light')
    setLoading(plan.key); setPageStatus('idle')
    try {
      if (method === 'stars') {
        // Multi-period Stars: подставляем suffixed plan_key (vpn_base_3m / _6m / _12m)
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        // recurring=true → Telegram Stars subscription (30-day cycle). Только для 1m.
        const isRecurring = (starsPeriod ?? '1m') === '1m' && !!recurring
        const { invoice_url } = await createVpnInvoice(planKey, isRecurring)
        let callbackFired = false
        // Safety timeout: если юзер закроет Telegram до окончания платежа
        // или сеть упадёт — openInvoice callback может не сработать, кнопка
        // зависнет «загрузка». Через 5 минут принудительно снимаем loading.
        const guardId = setTimeout(() => {
          if (!callbackFired) setLoading(null)
        }, 5 * 60 * 1000)
        WebApp.openInvoice(invoice_url, (s) => {
          callbackFired = true
          clearTimeout(guardId)
          setLoading(null)
          if (s === 'paid') { WebApp.HapticFeedback.notificationOccurred('success'); setPageStatus('paid') }
          else if (s !== 'cancelled') { setPageStatus('error'); setErrMsg(t('plans_error_payment')) }
        })
      } else if (method === 'cryptomus') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceCryptomus(planKey, 'RUB')
        setLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else if (method === 'lavatop') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceLavatop(planKey)
        setLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else {
        const { pay_url } = await createVpnInvoiceCrypto(plan.key, 'RUB')
        setLoading(null)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      }
    } catch (e) {
      setLoading(null); setPageStatus('error')
      const msg = e instanceof Error ? e.message : t('plans_error_server')
      setErrMsg(msg)
      // Lava/CryptoBot 4xx часто специфичны (email отклонён, etc.) —
      // показываем модалкой чтобы юзер точно увидел. Generic-ошибки
      // оставляем тихим баннером внизу.
      if (method === 'lavatop' || (e instanceof Error && /email|карт|payment/i.test(e.message))) {
        WebApp.showAlert(msg)
      }
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

          {/* Happ-чипы — раньше были только на success-экране VPN.tsx, юзер
              покупавший из /vpn/plans их не видел, попадал в /configs без
              подсказки куда поставить Happ.  Теперь параллельно для обоих
              путей. */}
          <div className="flex gap-2 w-full justify-center">
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink('https://apps.apple.com/app/happ-proxy-utility/id6504287215') }}
              className="flex-1 py-2 px-3 rounded-[10px] border border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color)] text-[12px] text-[var(--tg-theme-text-color)] flex items-center justify-center gap-1.5">
              <span className="text-[14px]">🍎</span> App Store
            </button>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink('https://play.google.com/store/apps/details?id=com.happproxy') }}
              className="flex-1 py-2 px-3 rounded-[10px] border border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color)] text-[12px] text-[var(--tg-theme-text-color)] flex items-center justify-center gap-1.5">
              <span className="text-[14px]">▶</span> Google Play
            </button>
          </div>

          <button className="btn w-full mb-2.5" onClick={() => nav('/configs')}>{t('plans_my_configs')}</button>
          <button className="btn w-full !bg-[var(--tg-theme-section-bg-color,#f1f1f1)] !text-[var(--tg-theme-text-color,#000)]"
            onClick={() => {
              // setPageStatus('idle') в .finally(), чтобы даже на ошибке
              // юзер не застрял на success-экране. Catch на null → SkeletonPage.
              getActiveSubscription()
                .then(setSub)
                .catch(() => setSub(null))
                .finally(() => setPageStatus('idle'))
            }}>
            {t('plans_back')}
          </button>
        </div>
      </div>
    )
  }

  if (sub === undefined) return <SkeletonPage />

  // Если страница открыта вне Telegram (нет initData) — не показываем прайс.
  // У конкурентов цены доступны только после `/start` в боте — это и сигнал
  // о "не палёво" (анти-сканер рекламы 149-ФЗ), и lock-in воронки.
  if (!WebApp.initData) {
    return (
      <div className="page pt-2">
        <div className="rounded-[20px] p-6 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] text-center">
          <div className="text-4xl mb-3">🔒</div>
          <div className="text-base font-bold text-[var(--tg-theme-text-color)] mb-2">
            {t('plans_gated_title')}
          </div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)] leading-snug mb-4">
            {t('plans_gated_sub')}
          </div>
          <a
            href="https://t.me/maxvpnesim_bot"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block px-5 py-2.5 rounded-[12px] bg-gradient-to-br from-primary to-[#5856d6] text-white text-sm font-bold no-underline"
          >
            {t('plans_gated_btn')}
          </a>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="page pb-[calc(env(safe-area-inset-bottom)+96px)]">
        {/* Триал не в PLANS (нечего покупать) → curPlan find() возвращал бы
            undefined и мы fallback'или на VISIBLE_PLANS[0] = vpn_base, что
            делало vpn_base показанным как «Ваш» а vpn_max как «+20 ₽».
            Для триала фактически нет «текущего платного» — это первая покупка.
            Поэтому показываем как для null-sub: все mode='buy'.

            Expired статус — то же самое: подписка истекла, юзер должен видеть
            обычные «Купить», а не «Понизить»/«Ваш» от прошлого тарифа. */}
        {(sub === null || sub.plan === 'vpn_trial' || sub.status === 'expired') ? (
          VISIBLE_PLANS.map((plan, i) => (
            <PlanCard key={plan.key} plan={plan} mode="buy"
              upgradePrice={0} loading={loading === plan.key}
              isPending={false} animDelay={i + 1}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }} />
          ))
        ) : (
          (() => {
            const curPlan = PLANS.find(p => p.key === sub.plan) ?? VISIBLE_PLANS[0]
            const planList = VISIBLE_PLANS
            return planList.map((plan, i) => {
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

      {/* Full-screen overlay пока ждём ответа от Lava/CryptoBot/Cryptomus.
          Тонкая spinner-кнопка на тарифной карточке слишком слабая обратная
          связь — юзер думает «ничего не произошло». Big modal убирает
          сомнения и резко закрывается когда openLink триггерит браузер. */}
      {loading && (
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

      {sheetPlan && (
        <PaymentSheet
          plan={sheetPlan}
          onClose={() => setSheetPlan(null)}
          onPay={(method, period, recurring) => handleBuy(sheetPlan, method, period, recurring)}
          /* Юзер кликнул кнопку с ценой в ₽ — preselect ₽-метод чтобы не было
             когнитивного диссонанса «нажал 200 ₽, открылось 145 ⭐». */
          defaultMethod="crypto"
          hasActiveTrial={sub?.plan === 'vpn_trial'}
        />
      )}
    </>
  )
}

function Legend() {
  const t = useT()
  return (
    <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-xl py-3 px-4 mt-2 text-xs text-[var(--tg-theme-hint-color,#707579)] leading-[1.7]">
      <span className="text-success font-semibold">{t('plans_legend_dev')}</span> {t('plans_legend_dev_s')}
    </div>
  )
}