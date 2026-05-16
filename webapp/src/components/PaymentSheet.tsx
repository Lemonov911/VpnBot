import { useEffect, useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'
import { getFeatures } from '../api'

export type PayMethod = 'stars' | 'crypto' | 'cryptomus' | 'lavatop'
export type PayPeriod = '1m' | '3m' | '6m' | '12m'

// Multi-period цены — синхронизировано с bot/services/plans.py.
// Stars + Cryptomus поддерживают 1/3/6/12. CryptoBot + Lava — только 1m
// (бэкенд блокирует multi_period для них через 400).
export const STARS_PRICES: Record<string, Record<PayPeriod, number>> = {
  vpn_base: { '1m': 145,  '3m': 370,  '6m': 695,  '12m': 1220 },
  vpn_max:  { '1m': 360,  '3m': 920,  '6m': 1725, '12m': 3025 },
}

// RUB-цены multi-period (для Cryptomus). Та же скидочная лестница:
// 3м −15%, 6м −20%, 12м −30% vs ровно-перемноженной 1м цены.
export const RUB_PRICES: Record<string, Record<PayPeriod, number>> = {
  vpn_base: { '1m': 200, '3m': 510,  '6m': 960,  '12m': 1680 },
  vpn_max:  { '1m': 500, '3m': 1275, '6m': 2400, '12m': 4200 },
}

const MONTHS_IN_PERIOD = { '1m': 1, '3m': 3, '6m': 6, '12m': 12 } as const
const PERIOD_LABEL_RU  = { '1m': '1 мес', '3m': '3 мес', '6m': '6 мес', '12m': '1 год' } as const

// Plan_key суффикс по периоду. 1m остаётся без суффикса (vpn_base / vpn_max),
// остальные — с _3m/_6m/_12m (см. plans.py).
export function starsPlanKey(baseKey: string, period: PayPeriod): string {
  return period === '1m' ? baseKey : `${baseKey}_${period}`
}

// Alias для обратной совместимости + backward-friendly imports
export type StarsPeriod = PayPeriod

export interface Plan {
  key: string; nameKey: string; stars: number; rub: number; usd: number
  vless: number; badge?: string
  awg?: number  // AmneziaWG slots — обходят DPI МТС (главный продукт)
  wg?: number   // plain WireGuard slots (legacy, для роутеров)
  speedMbps: number
  softCapGb: number
  throttleMbps: number
}

export const PLANS: Plan[] = [
  // Слоты синхронизированы с bot/services/plans.py (единый источник правды по
  // тарифам — мы не получаем их с API, потому что они редко меняются и UI
  // должен рендериться даже без подключения к боту).
  { key: 'vpn_base', nameKey: 'vpn_plan_base', stars: 145, rub: 200, usd: 2.2, vless: 1, awg: 2, speedMbps: 60,  softCapGb: 500,  throttleMbps: 5 },
  { key: 'vpn_max',  nameKey: 'vpn_plan_max',  stars: 360, rub: 500, usd: 5.5, vless: 5, awg: 3, speedMbps: 120, softCapGb: 1000, throttleMbps: 15, badge: 'hit' },
]

// alias for callers that imported VISIBLE_PLANS — keep backwards-compat for one cycle
export const VISIBLE_PLANS: Plan[] = PLANS

export default function PaymentSheet({
  plan, onClose, onPay, defaultMethod = 'crypto', hasActiveTrial = false,
}: {
  plan: Plan
  onClose: () => void
  onPay: (method: PayMethod, period?: PayPeriod, recurring?: boolean) => void
  defaultMethod?: PayMethod
  hasActiveTrial?: boolean
}) {
  const t      = useT()
  // Preselect crypto (₽) — юзер на странице тарифов видит цену 200₽, ожидает
  // что нажав «купить» он попадёт в RUB-флоу.  Stars preselected раньше
  // вызывало когнитивный mismatch: «я нажал 200₽, а тут 145⭐».
  const [method, setMethod] = useState<PayMethod>(defaultMethod)
  const [showCryptomus, setShowCryptomus] = useState(false)
  const [showLavatop, setShowLavatop]     = useState(false)
  const [period, setPeriod]               = useState<PayPeriod>('1m')
  // Stars 1m auto-renew (subscription_period=2592000). Только для stars+1m.
  const [recurring, setRecurring]         = useState(true)

  // Какие методы поддерживают multi-period (3/6/12). Lava + CryptoBot — только 1м.
  const methodSupportsMultiPeriod = method === 'stars' || method === 'cryptomus'

  // Цена и discount по текущему методу/периоду
  const starsPrice = STARS_PRICES[plan.key]?.[period] ?? plan.stars
  const rubPrice   = RUB_PRICES[plan.key]?.[period] ?? plan.rub
  const starsBaseMonthly = STARS_PRICES[plan.key]?.['1m'] ?? plan.stars
  const rubBaseMonthly   = RUB_PRICES[plan.key]?.['1m'] ?? plan.rub
  const periodMonths = MONTHS_IN_PERIOD[period]
  const starsDiscountPct = period === '1m' ? 0
    : Math.round((1 - starsPrice / (starsBaseMonthly * periodMonths)) * 100)
  const rubDiscountPct = period === '1m' ? 0
    : Math.round((1 - rubPrice / (rubBaseMonthly * periodMonths)) * 100)
  const discountPct = method === 'stars' ? starsDiscountPct : rubDiscountPct

  // При смене метода на Lava/CryptoBot форсим 1m (они multi-period не умеют)
  useEffect(() => {
    if (!methodSupportsMultiPeriod && period !== '1m') setPeriod('1m')
  }, [method, methodSupportsMultiPeriod, period])

  useEffect(() => {
    let cancelled = false
    getFeatures().then(f => {
      if (cancelled) return
      setShowCryptomus(!!f.cryptomus)
      setShowLavatop(!!f.lavatop)
    })
    return () => { cancelled = true }
  }, [])

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 z-[100] bg-black/45"
      />
      <div className="fixed inset-x-0 bottom-0 z-[101] bg-[var(--tg-theme-bg-color,#fff)] rounded-t-[20px] p-5 pb-[calc(env(safe-area-inset-bottom)+24px)] shadow-[0_-4px_30px_rgba(0,0,0,0.18)]">
        <div className="w-9 h-1 rounded-sm bg-gray-500/30 -mt-2 mx-auto mb-[18px]" />
        <div className="mb-[18px]">
          <div className="font-bold text-lg text-[var(--tg-theme-text-color,#000)]">
            {t('pay_buy')} «{t(plan.nameKey as never)}»
          </div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color,#707579)] mt-[3px]">
            {plan.rub} ₽ {t('pay_per_month')}
            {' · '}{plan.speedMbps} Mbps
            {plan.awg ? ` · ${plan.awg} AmneziaWG` : ''}
            {' · '}{plan.vless} VLESS
            {plan.wg ? ` · ${plan.wg} WireGuard` : ''}
          </div>
          {/* Лимит трафика и throttle — показываем явно, иначе юзер ловит
              throttle на 500 ГБ и винит сервис (UX agent finding #2). */}
          <div className="text-[11px] text-[var(--tg-theme-hint-color,#707579)] mt-1.5">
            {t('pay_fair_use')
              .replace('{cap}', String(plan.softCapGb))
              .replace('{throttle}', String(plan.throttleMbps))}
          </div>
          {/* Trial warning — у юзера активный триал, после покупки он
              закроется без переноса остатка дней. Чтобы юзер не остался в
              шоке «потерял 4 бесплатных дня». */}
          {hasActiveTrial && (
            <div className="mt-3 p-[8px_10px] rounded-lg bg-warning/15 border border-warning/30 text-[11px] text-warning leading-snug">
              ⚠️ {t('pay_trial_warning' as never)}
            </div>
          )}
        </div>
        <div className="text-xs font-semibold text-[var(--tg-theme-hint-color,#707579)] uppercase tracking-[0.5px] mb-2">
          {t('pay_method')}
        </div>
        <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-[14px] overflow-hidden mb-5">
          {(([
            ...(showLavatop
              ? [['lavatop', '💳', t('pay_method_lavatop' as never), `${plan.rub} ₽`]]
              : []),
            ['stars',    '⭐', t('pay_method_stars'),     `${starsPrice} ⭐`],
            ['crypto',   '💎', t('pay_method_crypto'),    `${plan.rub} ₽`],
            ...(showCryptomus
              ? [['cryptomus', '🔗', t('pay_method_cryptomus' as never), `${rubPrice} ₽`]]
              : []),
          ]) as [PayMethod, string, string, string][]).map(([val, icon, label, price], i, arr) => (
            <div key={val}>
              <div
                onClick={() => setMethod(val)}
                className={`py-[13px] px-4 flex items-center gap-3.5 cursor-pointer ${i < arr.length - 1 && method !== val ? 'border-b border-gray-500/10' : ''} ${method === val ? 'bg-primary/[0.06]' : ''}`}
              >
                <span className="text-[22px] w-8 text-center shrink-0">{icon}</span>
                <span className="flex-1 text-[15px] text-[var(--tg-theme-text-color,#000)] font-medium">{label}</span>
                <span className={`text-[13px] font-semibold ${method === val ? 'text-[var(--tg-theme-button-color,#2481cc)]' : 'text-[var(--tg-theme-hint-color,#707579)]'}`}>{price}</span>
                <div className={`w-5 h-5 rounded-full shrink-0 border-2 flex items-center justify-center ${
                  method === val
                    ? 'border-[var(--tg-theme-button-color,#2481cc)] bg-[var(--tg-theme-button-color,#2481cc)]'
                    : 'border-gray-500/35 bg-transparent'
                }`}>
                  {method === val && <div className="w-2 h-2 rounded-full bg-white" />}
                </div>
              </div>
              {/* Period chips появляются под Stars и Cryptomus (методы которые
                  поддерживают multi-period). Lava/CryptoBot — только 1м, чипов нет. */}
              {(val === 'stars' || val === 'cryptomus') && method === val && methodSupportsMultiPeriod && (
                <div className={`px-3 pt-1 pb-3 ${i < arr.length - 1 ? 'border-b border-gray-500/10' : ''}`}>
                  <div className="flex gap-1.5 flex-wrap">
                    {(['1m','3m','6m','12m'] as PayPeriod[]).map(p => {
                      const isStars = val === 'stars'
                      const price = isStars
                        ? (STARS_PRICES[plan.key]?.[p] ?? 0)
                        : (RUB_PRICES[plan.key]?.[p] ?? 0)
                      if (!price) return null
                      const months = MONTHS_IN_PERIOD[p]
                      const monthlyAvg = price / months
                      const baseMonthly = isStars
                        ? (STARS_PRICES[plan.key]?.['1m'] ?? 1)
                        : (RUB_PRICES[plan.key]?.['1m'] ?? 1)
                      const discount = p === '1m' ? 0 : Math.round((1 - price / (baseMonthly * months)) * 100)
                      const priceUnit = isStars ? '⭐' : '₽'
                      return (
                        <button
                          key={p}
                          onClick={() => setPeriod(p)}
                          className={`flex-1 min-w-[64px] py-2 px-2 rounded-[10px] border text-[11px] font-semibold leading-tight cursor-pointer transition-colors ${
                            period === p
                              ? 'border-[var(--tg-theme-button-color,#2481cc)] bg-[var(--tg-theme-button-color,#2481cc)] text-white'
                              : 'border-gray-500/20 bg-[var(--tg-theme-bg-color,#fff)] text-[var(--tg-theme-text-color)]'
                          }`}
                        >
                          <div>{PERIOD_LABEL_RU[p]}</div>
                          <div className={`text-[10px] font-normal mt-0.5 ${period === p ? 'opacity-90' : 'opacity-60'}`}>
                            {price} {priceUnit}
                          </div>
                          {discount > 0 && (
                            <div className={`text-[9px] font-bold mt-0.5 ${period === p ? 'text-white' : 'text-success'}`}>
                              −{discount}%
                            </div>
                          )}
                          <div className={`text-[9px] mt-0.5 ${period === p ? 'opacity-80' : 'opacity-50'}`}>
                            {Math.round(monthlyAvg)}{priceUnit}/мес
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
        {/* Auto-renew toggle для Stars+1m subscription. Telegram поддерживает
            только 30-дневный subscription_period — поэтому для 3/6/12 toggle
            скрыт (длинные периоды всегда one-time). По дефолту ON — экономит
            юзеру клик и улучшает retention; отключить можно одним тапом. */}
        {method === 'stars' && period === '1m' && (
          <div
            onClick={() => setRecurring(r => !r)}
            className="flex items-center gap-3 p-3 rounded-[12px] border border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color,#f1f1f1)] cursor-pointer mb-3"
          >
            <span className="text-base shrink-0">🔁</span>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color,#000)]">
                {t('pay_stars_autorenew' as never)}
              </div>
              <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-0.5">
                {t('pay_stars_autorenew_hint' as never).replace('{stars}', String(starsPrice))}
              </div>
            </div>
            <div className={`relative w-10 h-6 rounded-full shrink-0 transition-colors ${
              recurring ? 'bg-[var(--tg-theme-button-color,#2481cc)]' : 'bg-gray-500/30'
            }`}>
              <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                recurring ? 'translate-x-[18px]' : 'translate-x-0.5'
              }`} />
            </div>
          </div>
        )}
        <button
          className="btn !w-full !text-base !py-3.5"
          onClick={() => onPay(
            method,
            methodSupportsMultiPeriod ? period : undefined,
            method === 'stars' && period === '1m' ? recurring : undefined,
          )}
        >
          {method === 'stars'
            ? `${t('pay_pay_btn')} ${starsPrice} ⭐`
            : method === 'cryptomus'
              ? `${t('pay_pay_btn')} ${rubPrice} ₽`
              : `${t('pay_pay_btn')} ${plan.rub} ₽`}
        </button>
        {discountPct > 0 && (
          <div className="mt-1.5 text-center text-[11px] text-success font-semibold">
            {t('pay_stars_save' as never).replace('{pct}', String(discountPct))}
          </div>
        )}
        {/* После оплаты юзер уходит в CryptoBot / Stars-диалог.  Без подсказки
            что делать дальше — теряются: «я заплатил, а где конфиг?». */}
        <div className="mt-2 text-[11px] text-[var(--tg-theme-hint-color)] text-center px-2">
          {t('pay_after_hint' as never)}
        </div>
        {/* Trust signals — без них юзер на скептиц-рынке (RU VPN) не платит.
            Конкретно: гарантия + 30 дней + что делать если не работает. Без юр.лица
            это «soft guarantee» (мы вернём деньги, потому что репутация важнее
            одной подписки), но писать всё равно надо.
            Оферта-PDF/PP остаётся скрытой до публикации legal-страниц (см. obsidian
            → «Что нужно чтобы начать продавать» #4). */}
        <div className="mt-3 px-1 text-[10.5px] text-[var(--tg-theme-hint-color)] leading-snug text-center">
          ✓ {t('pay_trust_1')}<br />
          ✓ {t('pay_trust_2')}<br />
          ✓ {t('pay_trust_3')}
        </div>
      </div>
    </>
  )
}
