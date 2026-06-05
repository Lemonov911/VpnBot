import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  createVpnInvoice, createVpnInvoiceCrypto, createVpnInvoiceOxapay, createVpnInvoiceLavatop, getActiveSubscription, changeSubscriptionPlan,
  type Subscription,
} from '../api'
import PaymentSheet, { PLANS, VISIBLE_PLANS, starsPlanKey, type Plan, type PayMethod, type StarsPeriod } from '../components/PaymentSheet'
import PostPayOnboarding from '../components/PostPayOnboarding'
import { useT } from '../i18n'
import type { TKey } from '../i18n'
import { HAPP_LINKS } from '../data/happ'
// W3 #7: PLAN_ICONS / PLAN_TW / PLAN_NAME_KEY переехали в общий модуль,
// раньше дублировались между Plans.tsx и VPN.tsx (drift почти случился 22.05).
import { PLAN_ICONS, PLAN_TW, PLAN_NAME_KEY } from '../lib/plans'

function calcUpgradePrice(curRub: number, newRub: number, daysLeft: number): number {
  if (daysLeft <= 0) return newRub
  return Math.max(1, Math.round((newRub - curRub) * daysLeft / 30))
}

// Stars and rub prices for legacy plans — mirrors plans.py VPN_PLANS.
// Used when the user has a legacy plan not present in PLANS[] to calculate
// correct upgrade price and mode (avoid falling back to vpn_base defaults).
const LEGACY_PLAN_PRICES: Record<string, { stars: number; rub: number }> = {
  vpn_start:   { stars: 128,  rub: 180  },
  vpn_popular: { stars: 214,  rub: 270  },
  vpn_pro:     { stars: 342,  rub: 450  },
  vpn_family:  { stars: 513,  rub: 640  },
  vpn_1m:      { stars: 299,  rub: 299  },
  vpn_3m:      { stars: 699,  rub: 699  },
  vpn_1y:      { stars: 1990, rub: 1990 },
}

// W3: React.memo обёртка ниже (`memo(PlanCardImpl)`).  Карточки рендерятся
// в .map() по VISIBLE_PLANS — родитель пересчитывается каждый раз когда
// меняется sub/loading/sheetPlan, без memo все 2 карточки переренди-вало
// каждый тик. Большинство prop'ов стабильны (plan по reference из const PLANS,
// mode/upgradePrice/animDelay меняются редко) — memo выгоден.
function PlanCardImpl({
  plan, mode, upgradePrice, loading, isPending, onClick, animDelay,
}: {
  plan: Plan; mode: 'buy' | 'current' | 'renew' | 'upgrade' | 'downgrade' | 'pending'
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
  } else if (mode === 'renew') {
    // Юзер в grace на ТОМ ЖЕ плане — единственный способ выйти из throttle
    // и продлить.  Без этой кнопки grace-юзер был вынужден или upgrade на
    // более дорогой план, или потерять доступ.
    btn = (
      <button className="btn !min-w-[84px] !text-[13px]" disabled={loading} onClick={onClick}>
        {loading ? '…' : `${plan.rub} ₽`}
      </button>
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
          <span className="font-bold text-base text-[var(--tg-theme-text-color,#000)]">{t(PLAN_NAME_KEY[plan.key] ?? 'vpn_plan_start' as TKey)}</span>
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

// memo()-обёртка PlanCard. Дефолтный shallow check — primitives
// (mode/upgradePrice/loading/isPending/animDelay) и стабильный `plan` ref
// из const PLANS будут совпадать; единственный нестабильный prop = `onClick`
// (inline arrow в .map() родителя), его новизна форсит re-render для той
// конкретной карточки которая получила свежую closure. Тем не менее memo
// окупается: при изменении только sub.days_remaining карточки чьи
// up-stream данные не зависят от sub (например vpn_base.rub в mode='buy')
// не должны пересоздавать DOM-узлы.  TODO(perf): обернуть onClick'и в
// useCallback с зависимостью только от plan.key для полного wins.
const PlanCard = memo(PlanCardImpl)

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
  // Различаем оверлей «открываем оплату» (handleBuy) и «обновляем подписку»
  // (handleChange — downgrade/cancel/upgrade без инвойса). Без флага юзер
  // при downgrade видел бы «Открываем оплату», что неверно.
  const [isPaymentOp, setIsPaymentOp] = useState(false)
  // Pending-payment placeholder — параллель VPN.tsx flow.
  // Если юзер закрыл PostPayOnboarding и вернулся в /vpn/plans без active sub,
  // мы показываем «Проверяем оплату…» вместо повторного списка планов, чтобы
  // он не запутался и не попытался купить ещё раз.
  const [pendingPayment, setPendingPayment] = useState<{plan_key: string, method: string, started_at: number} | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])
  // See VPN.tsx FR10 — monotonic token to drop stale Stars openInvoice
  // callbacks after the 5-min guard has cleared loading and the user has
  // started a fresh purchase.
  const buyTokenRef = useRef(0)

  // Computed once per sub.plan change — not inside .map() on every render.
  const curPlan = useMemo(() => {
    if (!sub || sub.plan === 'vpn_trial' || sub.status === 'expired') return null
    return PLANS.find(p => p.key === sub.plan) ?? {
      ...VISIBLE_PLANS[0],
      key:   sub.plan,
      stars: LEGACY_PLAN_PRICES[sub.plan]?.stars ?? VISIBLE_PLANS[0].stars,
      rub:   LEGACY_PLAN_PRICES[sub.plan]?.rub   ?? VISIBLE_PLANS[0].rub,
    }
  }, [sub])

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    // Защита от unmount-race: если юзер быстро уходит со страницы,
    // pending fetch не должен setState на unmounted component.
    let cancelled = false
    // Pending-payment flag — выставляется после openLink на внешний инвойс
    // (Lava / CryptoBot / OxaPay). Если юзер вернулся в /vpn/plans до прихода
    // вебхука, показываем placeholder вместо обычного списка планов.
    try {
      const raw = localStorage.getItem('pending_payment')
      if (raw) {
        const pp = JSON.parse(raw)
        // FR9: see VPN.tsx — validate shape, drop malformed entries.
        if (
          pp && typeof pp === 'object' &&
          typeof pp.started_at === 'number' &&
          typeof pp.plan_key === 'string' &&
          Date.now() - pp.started_at < 15 * 60 * 1000
        ) {
          setPendingPayment(pp)
        } else {
          localStorage.removeItem('pending_payment')
        }
      }
    } catch {
      try { localStorage.removeItem('pending_payment') } catch { /* noop */ }
    }
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

  // Cleanup: когда sub стал active — снимаем pending-флаг.
  useEffect(() => {
    if (sub && sub.status === 'active' && pendingPayment) {
      try { localStorage.removeItem('pending_payment') } catch { /* noop */ }
      setPendingPayment(null)
    }
  }, [sub, pendingPayment])

  // MD-F-r5: refresh sub when user returns to /vpn/plans. Webhook may have
  // landed while the user was in Lava/CryptoBot/OxaPay; without this the
  // plan grid stays on stale data until the next manual nav.
  useEffect(() => {
    const refresh = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const fresh = await getActiveSubscription()
        if (mountedRef.current) setSub(fresh)
      } catch { /* including 429 / network — preserve current state */ }
    }
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [])

  // Polling: пока pendingPayment висит — каждые 8с refresh subscription.
  // Стопаем по timeout 15 мин или когда sub активен.
  useEffect(() => {
    if (!pendingPayment) return
    const interval = setInterval(async () => {
      try {
        const fresh = await getActiveSubscription()
        if (fresh && fresh.status === 'active') {
          if (mountedRef.current) setSub(fresh)
        } else if (Date.now() - pendingPayment.started_at > 15 * 60 * 1000) {
          try { localStorage.removeItem('pending_payment') } catch { /* noop */ }
          if (mountedRef.current) setPendingPayment(null)
        }
      } catch { /* network blip — retry next tick */ }
    }, 8000)
    return () => clearInterval(interval)
  }, [pendingPayment])

  const handleBuy = async (plan: Plan, method: PayMethod, starsPeriod?: StarsPeriod, recurring?: boolean) => {
    if (loading) {
      // Sheet остаётся открытым, чтобы юзер мог дождаться завершения текущей
      // операции и повторить тап. Haptic = ack что нажатие зарегистрировано.
      WebApp.HapticFeedback.notificationOccurred('warning')
      return
    }
    setSheetPlan(null)
    WebApp.HapticFeedback.impactOccurred('light')
    setIsPaymentOp(true)
    setLoading(plan.key); setPageStatus('idle')
    try {
      if (method === 'stars') {
        // Multi-period Stars: подставляем suffixed plan_key (vpn_base_3m / _6m / _12m)
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        // recurring=true → Telegram Stars subscription (30-day cycle). Только для 1m.
        const isRecurring = (starsPeriod ?? '1m') === '1m' && !!recurring
        const { invoice_url } = await createVpnInvoice(planKey, isRecurring)
        const myToken = ++buyTokenRef.current
        // Safety timeout: если юзер закроет Telegram до окончания платежа
        // или сеть упадёт — openInvoice callback может не сработать, кнопка
        // зависнет «загрузка». Через 5 минут принудительно снимаем loading.
        const guardId = setTimeout(() => {
          if (buyTokenRef.current === myToken && mountedRef.current) setLoading(null)
        }, 5 * 60 * 1000)
        WebApp.openInvoice(invoice_url, (s) => {
          ;(async () => {
            clearTimeout(guardId)
            // FR10: drop late callbacks from a previous invoice — see VPN.tsx.
            if (buyTokenRef.current !== myToken) return
            if (!mountedRef.current) return
            setLoading(null)
            if (s !== 'paid') {
              if (s !== 'cancelled') {
                setPageStatus('error'); setErrMsg(t('plans_error_payment'))
              }
              return
            }
            // MD-F-r3: mirror VPN.tsx MD-F5+MD-F7 — clear pending_payment
            // flag + re-fetch sub to detect cross-method dedup refund. The
            // backend may have refunded these Stars because the user already
            // had an active sub via another payment method; without re-check
            // Plans.tsx would mis-declare success.
            try { localStorage.removeItem('pending_payment') } catch { /* noop */ }
            WebApp.HapticFeedback.notificationOccurred('success')
            try {
              const fresh = await getActiveSubscription()
              if (!mountedRef.current) return
              if (fresh && fresh.status === 'active' && fresh.plan === plan.key) {
                setSub(fresh); setPageStatus('paid')
              } else if (fresh && fresh.status === 'active') {
                setSub(fresh)
                WebApp.showAlert(t('vpn_already_active_refund' as never))
              } else {
                setSub(fresh)
                setPageStatus('paid')  // best-effort optimistic success
              }
            } catch {
              setPageStatus('paid')  // network blip — fall back to optimistic
            }
          })()
        })
      } else if (method === 'oxapay') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceOxapay(planKey, 'RUB')
        setLoading(null)
        const flag = { plan_key: plan.key, method, started_at: Date.now() }
        try { localStorage.setItem('pending_payment', JSON.stringify(flag)) } catch { /* noop */ }
        setPendingPayment(flag)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else if (method === 'lavatop') {
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceLavatop(planKey)
        setLoading(null)
        const flag = { plan_key: plan.key, method, started_at: Date.now() }
        try { localStorage.setItem('pending_payment', JSON.stringify(flag)) } catch { /* noop */ }
        setPendingPayment(flag)
        WebApp.openLink(pay_url)
        setPostPayOpen(true)
      } else {
        // CryptoBot (method='crypto') — multi-period one-time invoice
        const planKey = starsPlanKey(plan.key, starsPeriod ?? '1m')
        const { pay_url } = await createVpnInvoiceCrypto(planKey, 'RUB')
        setLoading(null)
        const flag = { plan_key: plan.key, method, started_at: Date.now() }
        try { localStorage.setItem('pending_payment', JSON.stringify(flag)) } catch { /* noop */ }
        setPendingPayment(flag)
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

  // showConfirm wrapped в Promise — TG WebApp API использует callback.
  // На старых Telegram-клиентах showConfirm не доступен → fallback на
  // window.confirm (или sync true если ни того ни того). Для надёжности
  // на iOS 17+ / Android — нативный TG-диалог.
  const showConfirm = (msg: string): Promise<boolean> => new Promise((resolve) => {
    try {
      if (WebApp.showConfirm) {
        WebApp.showConfirm(msg, resolve)
        return
      }
    } catch { /* ignore */ }
    resolve(typeof window !== 'undefined' && window.confirm ? window.confirm(msg) : true)
  })

  const handleChange = async (plan: Plan, action: 'downgrade' | 'cancel' | 'upgrade') => {
    if (loading || !sub) return

    // Confirm для downgrade и cancel — без него юзер случайным тапом
    // меняет план без обратной связи (баг репортнут 17.05).
    // Upgrade без confirm — там followed by payment screen, юзер успеет
    // отказаться при выставлении инвойса.
    if (action === 'downgrade') {
      const ok = await showConfirm(t('plans_downgrade_confirm').replace('{name}', t(PLAN_NAME_KEY[plan.key] ?? 'vpn_plan_start' as TKey)))
      if (!ok) return
    } else if (action === 'cancel') {
      const ok = await showConfirm(t('plans_cancel_confirm').replace('{name}', t(PLAN_NAME_KEY[plan.key] ?? 'vpn_plan_start' as TKey)))
      if (!ok) return
    }

    WebApp.HapticFeedback.impactOccurred('light')
    setIsPaymentOp(false)
    setLoading(plan.key); setPageStatus('idle')
    try {
      const res = await changeSubscriptionPlan(plan.key)
      if (res.invoice_url) {
        // changeSubscriptionPlan returns a CryptoBot URL — must use openLink,
        // not openInvoice (which only works with native Stars invoice links).
        // setPostPayOpen shows "payment is processing" overlay so user knows
        // the bot is waiting for the CryptoBot webhook.
        setLoading(null)
        WebApp.openLink(res.invoice_url)
        setPostPayOpen(true)
      } else if (res.scheduled) {
        WebApp.HapticFeedback.notificationOccurred('success')
        setSub(prev => prev ? { ...prev, pending_plan: plan.key } : prev)
        setLoading(null)
      } else if (res.cancelled) {
        WebApp.HapticFeedback.impactOccurred('light')
        setSub(prev => prev ? { ...prev, pending_plan: null } : prev)
        setLoading(null)
      } else {
        // Immediate upgrade applied on the server side — no invoice, not scheduled.
        WebApp.HapticFeedback.notificationOccurred('success')
        getActiveSubscription()
          .then(s => { if (mountedRef.current) setSub(s) })
          .catch(() => {})
        setLoading(null)
      }
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
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(HAPP_LINKS.site) }}
              className="flex-1 py-2 px-3 rounded-[10px] border border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color)] text-[12px] text-[var(--tg-theme-text-color)] flex items-center justify-center gap-1.5">
              <span className="text-[14px]">🍎</span> App Store
            </button>
            <button
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(HAPP_LINKS.android) }}
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
                .then(s => { if (mountedRef.current) setSub(s) })
                .catch(() => { if (mountedRef.current) setSub(null) })
                .finally(() => { if (mountedRef.current) setPageStatus('idle') })
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
            href={`https://t.me/${import.meta.env.VITE_BOT_USERNAME ?? 'maxvpnesim_bot'}`}
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
        {/* Pending-payment placeholder — юзер ушёл на внешний инвойс, вебхук
            ещё не вернулся.  Sub может быть null (первая покупка) ИЛИ
            grace/expired (юзер платит renewal из текущего grace-state).
            В обоих случаях нужно показать «Проверяем оплату…» — polling
            (useEffect выше) снимет флаг когда sub станет active. */}
        {pendingPayment && (!sub || sub.status === 'grace' || sub.status === 'expired') && (
          <div className="fade-in rounded-2xl p-4 mb-2 bg-[var(--tg-theme-secondary-bg-color,#f1f1f1)] border border-[var(--card-border)] text-center">
            <div className="text-2xl mb-2">⏳</div>
            <div className="font-medium mb-1 text-[var(--tg-theme-text-color,#000)]">{t('vpn_payment_verifying' as never)}</div>
            <div className="text-sm opacity-70 text-[var(--tg-theme-hint-color,#707579)]">{t('vpn_payment_verifying_sub' as never)}</div>
          </div>
        )}
        {/* Триал не в PLANS (нечего покупать) → curPlan find() возвращал бы
            undefined и мы fallback'или на VISIBLE_PLANS[0] = vpn_base, что
            делало vpn_base показанным как «Ваш» а vpn_max как «+20 ₽».
            Для триала фактически нет «текущего платного» — это первая покупка.
            Поэтому показываем как для null-sub: все mode='buy'.

            Expired статус — то же самое: подписка истекла, юзер должен видеть
            обычные «Купить», а не «Понизить»/«Ваш» от прошлого тарифа. */}
        {(sub === null || sub.plan === 'vpn_trial' || sub.status === 'expired' || !curPlan) ? (
          VISIBLE_PLANS.map((plan, i) => (
            <PlanCard key={plan.key} plan={plan} mode="buy"
              upgradePrice={0} loading={loading === plan.key}
              isPending={false} animDelay={i + 1}
              onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }} />
          ))
        ) : (
          VISIBLE_PLANS.map((plan, i) => {
            const inGrace = sub!.status === 'grace'
            const isPending = sub!.pending_plan === plan.key
            let mode: 'current' | 'renew' | 'upgrade' | 'downgrade' | 'pending'
            // Юзер в grace на текущем плане → показываем «Продлить N₽» вместо «Ваш».
            // Иначе grace-юзер не имеет способа выйти из throttle на тот же тариф.
            if (plan.key === curPlan!.key && inGrace) mode = 'renew'
            else if (plan.key === curPlan!.key) mode = 'current'
            else if (isPending) mode = 'pending'   // перед upgrade: grace-юзер с pending должен видеть Cancel, а не «Улучшить»
            else if (plan.stars > curPlan!.stars) mode = 'upgrade'
            else mode = 'downgrade'

            return (
              <PlanCard key={plan.key} plan={plan} mode={mode}
                upgradePrice={mode === 'upgrade' ? calcUpgradePrice(curPlan!.rub, plan.rub, sub!.days_remaining) : 0}
                loading={loading === plan.key} isPending={isPending} animDelay={i + 1}
                /* renew (юзер в grace на том же плане) идёт через обычный
                   buy-flow с PaymentSheet, не через changeSubscriptionPlan.
                   Backend _deliver_vpn детектит grace + same plan и продлевает
                   существующую sub + делает unthrottle на агенте. */
                onClick={() => {
                  if (mode === 'renew') {
                    WebApp.HapticFeedback.impactOccurred('light')
                    setSheetPlan(plan)
                  } else if (mode === 'pending') {
                    handleChange(plan, 'cancel')
                  } else if (mode === 'upgrade') {
                    handleChange(plan, 'upgrade')
                  } else {
                    handleChange(plan, 'downgrade')
                  }
                }} />
            )
          })
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
              {isPaymentOp ? t('pay_loading' as never) : t('plans_updating' as never)}
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
          defaultMethod="oxapay"
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