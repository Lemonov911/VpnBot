import { useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

export type PayMethod = 'stars' | 'crypto'

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
  plan, onClose, onPay,
}: {
  plan: Plan
  onClose: () => void
  onPay: (method: PayMethod) => void
}) {
  const t      = useT()
  const [method, setMethod] = useState<PayMethod>('stars')

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
        </div>
        <div className="text-xs font-semibold text-[var(--tg-theme-hint-color,#707579)] uppercase tracking-[0.5px] mb-2">
          {t('pay_method')}
        </div>
        <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-[14px] overflow-hidden mb-5">
          {([
            ['stars',  '⭐', t('pay_method_stars'),    `${plan.stars} ⭐`] as [PayMethod, string, string, string],
            ['crypto', '💎', t('pay_method_crypto'),   `${plan.rub} ₽`] as [PayMethod, string, string, string],
          ]).map(([val, icon, label, price], i) => (
            <div
              key={val}
              onClick={() => setMethod(val)}
              className={`py-[13px] px-4 flex items-center gap-3.5 cursor-pointer ${i === 0 ? 'border-b border-gray-500/10' : ''} ${method === val ? 'bg-primary/[0.06]' : ''}`}
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
          ))}
        </div>
        <button
          className="btn !w-full !text-base !py-3.5"
          onClick={() => onPay(method)}
        >
          {method === 'stars' ? `${t('pay_pay_btn')} ${plan.stars} ⭐` : `${t('pay_pay_btn')} ${plan.rub} ₽`}
        </button>
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