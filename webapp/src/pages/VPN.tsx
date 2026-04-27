import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  createVpnInvoice, createVpnInvoiceCrypto,
  getActiveSubscription, getUserConfigs, getVpnStatus,
  type Subscription, type VpnConfig, type VpnServerStatus,
} from '../api'
import { useT, usePlural } from '../i18n'
import PaymentSheet, { PLANS, type Plan, type PayMethod } from '../components/PaymentSheet'



const PLAN_ICONS: Record<string, { bg: string; icon: JSX.Element }> = {
  vpn_start:   { bg: '#5ac8fa', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_popular: { bg: '#2481cc', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_pro:     { bg: '#5856d6', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="#ffffff33" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/><path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg> },
  vpn_family:  { bg: '#ff2d55', icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="9" cy="7" r="3" stroke="#fff" strokeWidth="2"/><path d="M3 19c0-3 2.686-5 6-5s6 2 6 5" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><circle cx="17" cy="7" r="2.5" stroke="#fff" strokeWidth="1.8"/><path d="M21 19c0-2.5-1.8-4-4-4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg> },
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) }
  catch { return iso }
}

function ExpiryBar({ daysLeft, t }: { daysLeft: number; t: ReturnType<typeof useT> }) {
  const pct   = Math.max(4, Math.min(100, Math.round(daysLeft / 30 * 100)))
  const color = daysLeft <= 5 ? '#ff3b30' : daysLeft <= 10 ? '#e67e22' : '#27ae60'
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
        <span style={{ color: 'rgba(128,128,128,0.7)' }}>{t('vpn_expiry_label')}</span>
        <span style={{ color, fontWeight: 600 }}>{daysLeft} {t('vpn_expiry_left')}</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'rgba(128,128,128,0.15)' }}>
        <div style={{ height: '100%', width: `${pct}%`, borderRadius: 2, background: color, transition: 'width 0.4s' }} />
      </div>
    </div>
  )
}

function SlotDots({ active, total, color }: { active: number; total: number; color: string }) {
  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      {Array.from({ length: total }).map((_, i) => (
        <span key={i} style={{
          width: 8, height: 8, borderRadius: '50%',
          background: i < active ? color : 'rgba(128,128,128,0.2)',
          transition: 'background 0.2s',
        }} />
      ))}
    </span>
  )
}

function SkeletonPage() {
  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)', gap: 10 }}>
      <div style={{ height: 16 }} />
      <div className="skeleton" style={{ height: 160, borderRadius: 18 }} />
      <div className="skeleton" style={{ height: 60, borderRadius: 12 }} />
      <div className="skeleton" style={{ height: 60, borderRadius: 12 }} />
      <div className="skeleton" style={{ height: 60, borderRadius: 12 }} />
    </div>
  )
}

export default function VPN() {
  const nav = useNavigate()
  const tp  = WebApp.themeParams
  const t   = useT()
  const p   = usePlural()
  const accent = 'var(--tg-theme-button-color, #2481cc)'

  const PLAN_NAMES: Record<string, string> = {
    vpn_start: t('vpn_plan_start'), vpn_popular: t('vpn_plan_popular'),
    vpn_pro: t('vpn_plan_pro'), vpn_family: t('vpn_plan_family'),
  }

  const [sub,        setSub]        = useState<Subscription | null | undefined>(undefined)
  const [configs,    setConfigs]    = useState<VpnConfig[] | null>(null)
  const [status,     setStatus]     = useState<VpnServerStatus[] | null>(null)
  const [sheetPlan,  setSheetPlan]  = useState<Plan | null>(null)
  const [buyLoading, setBuyLoading] = useState<string | null>(null)
  const [paid,       setPaid]       = useState(false)

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

  const handleBuy = async (plan: Plan, method: PayMethod) => {
    setSheetPlan(null)
    if (buyLoading) return
    WebApp.HapticFeedback.impactOccurred('light')
    setBuyLoading(plan.key)
    try {
      if (method === 'stars') {
        const { invoice_url } = await createVpnInvoice(plan.key)
        WebApp.openInvoice(invoice_url, s => {
          setBuyLoading(null)
          if (s === 'paid') { WebApp.HapticFeedback.notificationOccurred('success'); setPaid(true) }
        })
      } else {
        const { pay_url } = await createVpnInvoiceCrypto(plan.key, 'RUB')
        setBuyLoading(null)
        WebApp.openLink(pay_url)
      }
    } catch {
      setBuyLoading(null)
    }
  }

  if (sub === undefined) return <SkeletonPage />

  // ── Успешная оплата ────────────────────────────────────────────────────────
  if (paid) {
    return (
      <div className="page">
        <div className="center">
          <div style={{
            width: 72, height: 72, borderRadius: 22, marginBottom: 8,
            background: 'rgba(39,174,96,0.12)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 36,
          }}>✅</div>
          <div style={{ fontWeight: 800, fontSize: 22, color: tp.text_color, marginBottom: 6 }}>{t('vpn_done_title')}</div>
          <p style={{ color: tp.hint_color, fontSize: 14, marginBottom: 20 }}>{t('vpn_done_sub')}</p>
          <button className="btn" style={{ width: '100%', marginBottom: 10 }} onClick={() => nav('/configs')}>{t('vpn_to_configs')}</button>
          <button className="btn" style={{ width: '100%', background: 'var(--section-bg)', color: tp.text_color }}
            onClick={() => { setPaid(false); getActiveSubscription().then(setSub) }}>
            {t('vpn_to_plans')}
          </button>
        </div>
      </div>
    )
  }

  // ── Нет подписки — показываем тарифы ──────────────────────────────────────
  if (sub === null) {
    return (
      <>
        <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)', gap: 10 }}>

          {/* Header */}
          <div style={{ padding: '6px 4px 2px' }}>
            <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>VPN</div>
            <div style={{ fontSize: 13, color: tp.hint_color }}>Amnezia WireGuard · 🇺🇸 {t('vpn_server_subtitle')}</div>
          </div>

          {/* Статус серверов */}
          {status !== null && status.length > 0 && (
            <div style={{
              background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 12,
              padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
            }}>
<span style={{ fontSize: 11, color: tp.hint_color }}>{t('vpn_servers')}</span>
              {status.map(s => (
                <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.ok ? '#27ae60' : '#ff3b30', display: 'inline-block' }} />
<span style={{ color: tp.text_color }}>{s.name}</span>
                </span>
              ))}
            </div>
          )}

          {/* План-карточки */}
          <div style={{ fontSize: 12, fontWeight: 700, color: tp.hint_color, textTransform: 'uppercase', letterSpacing: 0.5, padding: '4px 2px 0' }}>
            {t('vpn_choose')}
          </div>

          {PLANS.map((plan, i) => {
            const pi = PLAN_ICONS[plan.key] ?? PLAN_ICONS.vpn_start
            const isHit = plan.badge === 'hit'
            const deviceWord = p(plan.awg, { ru: ['устройство', 'устройства', 'устройств'], en: plan.awg === 1 ? 'device' : 'devices' })
            return (
              <div key={plan.key} className={`fade-in fade-in-${i + 1}`} style={{
                borderRadius: 16,
                border: isHit ? `2px solid ${accent}88` : '2px solid transparent',
                background: isHit ? `${accent}06` : 'var(--section-bg)',
                padding: '14px 16px',
                display: 'flex', alignItems: 'center', gap: 14,
              }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 13, flexShrink: 0,
                  background: pi.bg,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: `0 4px 12px ${pi.bg}55`,
                }}>
                  {pi.icon}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
                    <span style={{ fontWeight: 700, fontSize: 16, color: tp.text_color }}>{PLAN_NAMES[plan.key] ?? t(plan.nameKey as any)}</span>
{isHit && (
                        <span style={{ background: accent, color: tp.button_text_color ?? '#fff', fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20 }}>{t('plans_hit')}</span>
                      )}
                  </div>
                  <div style={{ fontSize: 13, color: tp.hint_color }}>
                    <span style={{ fontWeight: 600, color: tp.text_color }}>{plan.rub} ₽</span>
                    <span style={{ opacity: 0.4, margin: '0 4px' }}>·</span>
                    <span style={{ fontSize: 12 }}>📱 {plan.awg} {deviceWord}{plan.vless > 0 ? ' · 📺 TV' : ''}</span>
                  </div>
                </div>
                <button
                  disabled={buyLoading === plan.key}
                  onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setSheetPlan(plan) }}
                  style={{
                    padding: '8px 16px', borderRadius: 12, border: 'none',
                    background: accent, color: tp.button_text_color ?? '#fff',
                    fontSize: 13, fontWeight: 600, cursor: 'pointer', flexShrink: 0,
                    opacity: buyLoading === plan.key ? 0.6 : 1,
                  }}
                >
                  {buyLoading === plan.key ? '…' : `${plan.rub} ₽`}
                </button>
              </div>
            )
          })}

          {/* Легенда */}
          <div style={{
            background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 12,
            padding: '12px 16px', fontSize: 12, color: tp.hint_color, lineHeight: 1.7,
          }}>
            <span style={{ color: '#27ae60', fontWeight: 600 }}>{t('plans_legend_dev')}</span> {t('plans_legend_dev_s')}<br />
            <span style={{ color: '#8e44ad', fontWeight: 600 }}>{t('plans_legend_tv')}</span> {t('plans_legend_tv_s')}
          </div>

          {/* Как это работает */}
          <div style={{ fontSize: 12, fontWeight: 700, color: tp.hint_color, textTransform: 'uppercase', letterSpacing: 0.5, padding: '4px 2px 0' }}>
            {t('vpn_how_title')}
          </div>
          <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
            {[
              { num: '1', color: '#2481cc', title: t('vpn_how1'), sub: t('vpn_how1_sub') },
              { num: '2', color: '#27ae60', title: t('vpn_how2'), sub: t('vpn_how2_sub') },
              { num: '3', color: '#8e44ad', title: t('vpn_how3'), sub: t('vpn_how3_sub') },
            ].map(({ num, color, title, sub }, i, arr) => (
              <div key={i} style={{
                padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14,
                borderBottom: i < arr.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
              }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 10, flexShrink: 0,
                  background: color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontWeight: 800, fontSize: 16, color: '#fff',
                }}>{num}</div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: tp.text_color, lineHeight: 1.3 }}>{title}</div>
                  <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 1 }}>{sub}</div>
                </div>
              </div>
            ))}
          </div>

        </div>

        {sheetPlan && (
          <PaymentSheet
            plan={sheetPlan}
            onClose={() => setSheetPlan(null)}
            onPay={method => handleBuy(sheetPlan, method)}
          />
        )}
      </>
    )
  }

  // ── Есть подписка — управление ─────────────────────────────────────────────
  const planName    = PLAN_NAMES[sub.plan] ?? sub.plan
  const pendingName = sub.pending_plan ? (PLAN_NAMES[sub.pending_plan] ?? sub.pending_plan) : null
  const isExpiring  = sub.days_remaining <= 7

  const awgTotal    = configs?.filter(c => c.protocol === 'awg').length ?? 0
  const awgActive   = configs?.filter(c => c.protocol === 'awg' && c.status === 'active').length ?? 0
  const vlessTotal  = configs?.filter(c => c.protocol === 'vless').length ?? 0
  const vlessActive = configs?.filter(c => c.protocol === 'vless' && c.status === 'active').length ?? 0

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)', gap: 10 }}>

      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>VPN</div>
        <div style={{ fontSize: 13, color: tp.hint_color }}>Amnezia WireGuard · 🇺🇸</div>
      </div>

      {/* Предупреждение об истечении */}
      {isExpiring && (
        <div className="fade-in" style={{
          background: sub.days_remaining <= 3 ? 'rgba(255,59,48,0.1)' : 'rgba(230,126,34,0.1)',
          border: `1px solid ${sub.days_remaining <= 3 ? 'rgba(255,59,48,0.3)' : 'rgba(230,126,34,0.3)'}`,
          borderRadius: 12, padding: '10px 14px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: sub.days_remaining <= 3 ? '#ff3b30' : '#e67e22' }}>
              {sub.days_remaining <= 3 ? t('vpn_expiry_banner_1') : t('vpn_expiry_banner_3')}
            </div>
            <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 2 }}>
              {t('vpn_days_left')} {sub.days_remaining} {p(sub.days_remaining, { ru: [t('vpn_day_left_1'), t('vpn_day_left_2'), t('days')], en: t('vpn_day_left_2') })}
            </div>
          </div>
          <button onClick={() => nav('/vpn/plans')} style={{
            padding: '6px 14px', borderRadius: 8, border: 'none',
            background: sub.days_remaining <= 3 ? '#ff3b30' : '#e67e22',
            color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0,
          }}>{t('vpn_renew')}</button>
        </div>
      )}

      {/* Статус серверов */}
      {status !== null && (
        <div className="fade-in" style={{
          background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 12,
          padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
        }}>
<span style={{ fontSize: 11, color: tp.hint_color }}>{t('vpn_servers')}</span>
          {status.length === 0
            ? <span style={{ fontSize: 12, color: tp.hint_color }}>{t('vpn_no_data')}</span>
            : status.map(s => (
              <span key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.ok ? '#27ae60' : '#ff3b30', display: 'inline-block' }} />
                <span style={{ color: tp.text_color }}>{s.name}</span>
              </span>
            ))
          }
        </div>
      )}

      {/* Карточка подписки */}
      <div className="fade-in-1 fade-in" style={{
        background: 'var(--section-bg)', borderRadius: 16, padding: '16px 18px',
        border: '1px solid var(--card-border)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontSize: 11, color: tp.hint_color, marginBottom: 2 }}>{t('vpn_active_label')}</div>
            <div style={{ fontWeight: 700, fontSize: 22, color: tp.text_color }}>{planName}</div>
            <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 2 }}>{t('vpn_expires')} {formatDate(sub.expires_at)}</div>
          </div>
          <span style={{
            background: 'rgba(39,174,96,0.13)', color: '#27ae60',
            fontSize: 11, fontWeight: 700, padding: '4px 10px', borderRadius: 20, marginTop: 2, flexShrink: 0,
          }}>{t('vpn_active_badge')}</span>
        </div>

        {(awgTotal > 0 || vlessTotal > 0) && (
          <div style={{ marginTop: 14, display: 'flex', gap: 16 }}>
            {awgTotal > 0 && (
              <div>
                <div style={{ fontSize: 10, color: tp.hint_color, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('vpn_slots_devs')}</div>
                <SlotDots active={awgActive} total={awgTotal} color="#27ae60" />
                <div style={{ fontSize: 11, color: tp.hint_color, marginTop: 3 }}>{awgActive} / {awgTotal} {t('vpn_connected')}</div>
              </div>
            )}
            {vlessTotal > 0 && (
              <div>
                <div style={{ fontSize: 10, color: tp.hint_color, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>{t('vpn_slots_tv')}</div>
                <SlotDots active={vlessActive} total={vlessTotal} color="#8e44ad" />
                <div style={{ fontSize: 11, color: tp.hint_color, marginTop: 3 }}>{vlessActive} / {vlessTotal} {t('vpn_connected')}</div>
              </div>
            )}
          </div>
        )}

        <ExpiryBar daysLeft={sub.days_remaining} t={t} />

        {pendingName && (
          <div style={{
            marginTop: 12, padding: '8px 10px', borderRadius: 8,
            background: 'rgba(230,126,34,0.1)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontSize: 14 }}>⏳</span>
            <span style={{ fontSize: 12, color: '#e67e22' }}>
              {t('vpn_pending_change')} <b>«{pendingName}»</b> {t('vpn_pending_next')}
            </span>
          </div>
        )}

        <button onClick={() => nav('/vpn/plans')} style={{
          marginTop: 14, width: '100%', padding: '10px 0', borderRadius: 10, border: 'none',
          background: 'var(--tg-theme-button-color, #2481cc)',
          color: tp.button_text_color ?? '#fff',
          fontSize: 14, fontWeight: 600, cursor: 'pointer',
        }}>
          {t('vpn_change')}
        </button>
      </div>

      {/* Быстрые действия */}
      <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
        {[
          {
            color: '#27ae60',
            icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/><rect x="9" y="3" width="6" height="4" rx="1" stroke="#fff" strokeWidth="2"/><path d="M9 12h6M9 16h4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round"/></svg>,
            title: t('vpn_my_configs'), sub: t('vpn_wg_profiles'),
            action: () => { WebApp.HapticFeedback.impactOccurred('light'); nav('/configs') },
          },
          {
            color: '#8e44ad',
            icon: <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 22C6.48 22 2 17.52 2 12S6.48 2 12 2s10 4.48 10 10-4.48 10-10 10z" stroke="#fff" strokeWidth="2"/><path d="M12 8v4l3 3" stroke="#fff" strokeWidth="2" strokeLinecap="round"/></svg>,
            title: t('vpn_instr'), sub: t('vpn_connect_guide'),
            action: () => { WebApp.HapticFeedback.impactOccurred('light'); nav('/instructions') },
          },
        ].map(({ color, icon, title, sub, action }, i, arr) => (
          <button key={title} onClick={action} style={{
            width: '100%', border: 'none', background: 'transparent',
            padding: '13px 16px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 14,
            borderBottom: i < arr.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
          }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, flexShrink: 0, background: color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{icon}</div>
            <div style={{ flex: 1, textAlign: 'left' }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: tp.text_color }}>{title}</div>
              <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 1 }}>{sub}</div>
            </div>
            <svg width="7" height="12" viewBox="0 0 7 12" fill="none"><path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        ))}
      </div>

    </div>
  )
}
