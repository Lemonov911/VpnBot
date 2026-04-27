import { useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT, usePlural } from '../i18n'

export type PayMethod = 'stars' | 'crypto'

export interface Plan {
  key: string; nameKey: string; stars: number; rub: number; usd: number
  awg: number; vless: number; badge?: string
}

export const PLANS: Plan[] = [
  { key: 'vpn_start',   nameKey: 'vpn_plan_start',   stars: 128, rub: 180, usd: 2, awg: 1, vless: 0 },
  { key: 'vpn_popular', nameKey: 'vpn_plan_popular', stars: 214, rub: 270, usd: 3, awg: 2, vless: 0, badge: 'hit' },
  { key: 'vpn_pro',     nameKey: 'vpn_plan_pro',     stars: 342, rub: 450, usd: 5, awg: 3, vless: 1 },
  { key: 'vpn_family',  nameKey: 'vpn_plan_family',   stars: 513, rub: 640, usd: 7, awg: 7, vless: 1 },
]

export default function PaymentSheet({
  plan, onClose, onPay,
}: {
  plan: Plan
  onClose: () => void
  onPay: (method: PayMethod) => void
}) {
  const tp     = WebApp.themeParams
  const t      = useT()
  const p      = usePlural()
  const accent = 'var(--tg-theme-button-color, #2481cc)'
  const [method, setMethod] = useState<PayMethod>('stars')

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, zIndex: 100, background: 'rgba(0,0,0,0.45)' }}
      />
      <div style={{
        position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 101,
        background: tp.bg_color ?? '#fff',
        borderRadius: '20px 20px 0 0',
        padding: '20px 20px calc(env(safe-area-inset-bottom) + 24px)',
        boxShadow: '0 -4px 30px rgba(0,0,0,0.18)',
      }}>
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: 'rgba(128,128,128,0.3)',
          margin: '-8px auto 18px',
        }} />
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontWeight: 700, fontSize: 18, color: tp.text_color }}>
            {t('pay_buy')} «{t(plan.nameKey as any)}»
          </div>
          <div style={{ fontSize: 13, color: tp.hint_color, marginTop: 3 }}>
            {plan.rub} ₽ {t('pay_per_month')} · {p(plan.awg, { ru: (t('plans_devices' as any) as string).split('|') as [string, string, string], en: plan.awg === 1 ? 'device' : 'devices' })}
            {plan.vless > 0 ? ' · TV' : ''}
          </div>
        </div>
        <div style={{ fontSize: 12, fontWeight: 600, color: tp.hint_color, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
          {t('pay_method')}
        </div>
        <div style={{
          background: 'var(--section-bg)',
          border: '1px solid var(--card-border)',
          borderRadius: 14, overflow: 'hidden', marginBottom: 20,
        }}>
          {([
            ['stars',  '⭐', t('pay_method_stars'),    `${plan.stars} ⭐`] as [PayMethod, string, string, string],
            ['crypto', '💎', t('pay_method_crypto'),   `${plan.rub} ₽`] as [PayMethod, string, string, string],
          ]).map(([val, icon, label, price], i) => (
            <div
              key={val}
              onClick={() => setMethod(val)}
              style={{
                padding: '13px 16px',
                display: 'flex', alignItems: 'center', gap: 14,
                cursor: 'pointer',
                borderBottom: i === 0 ? '1px solid rgba(128,128,128,0.1)' : 'none',
                background: method === val ? `${accent}10` : 'transparent',
              }}
            >
              <span style={{ fontSize: 22, width: 32, textAlign: 'center', flexShrink: 0 }}>{icon}</span>
              <span style={{ flex: 1, fontSize: 15, color: tp.text_color, fontWeight: 500 }}>{label}</span>
              <span style={{ fontSize: 13, color: method === val ? accent : tp.hint_color, fontWeight: 600 }}>{price}</span>
              <div style={{
                width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                border: `2px solid ${method === val ? accent : 'rgba(128,128,128,0.35)'}`,
                background: method === val ? accent : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {method === val && <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#fff' }} />}
              </div>
            </div>
          ))}
        </div>
        <button
          className="btn"
          style={{ width: '100%', fontSize: 16, padding: '14px 0' }}
          onClick={() => onPay(method)}
        >
          {method === 'stars' ? `${t('pay_pay_btn')} ${plan.stars} ⭐` : `${t('pay_pay_btn')} ${plan.rub} ₽`}
        </button>
      </div>
    </>
  )
}