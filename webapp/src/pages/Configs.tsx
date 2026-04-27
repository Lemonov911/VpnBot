import { useEffect, useState, useLayoutEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getUserConfigs, getConfigDownloadUrl, getConfigQrUrl, getVpnServers,
  activateSlot, revokeConfig,
  type VpnConfig, type VpnServer,
} from '../api'
import { useT, usePlural, type TKey } from '../i18n'

const REVOKE_STYLE = `
@keyframes revoke-progress {
  0%   { width: 0%;   margin-left: 0; }
  50%  { width: 70%;  margin-left: 15%; }
  100% { width: 0%;   margin-left: 100%; }
}
`

function RevokeStyle() {
  useLayoutEffect(() => {
    const el = document.createElement('style')
    el.textContent = REVOKE_STYLE
    document.head.appendChild(el)
    return () => { document.head.removeChild(el) }
  }, [])
  return null
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
  } catch { return iso }
}

const PLAN_KEY: Record<string, string> = {
  vpn_start:   'vpn_plan_start',
  vpn_popular: 'vpn_plan_popular',
  vpn_pro:     'vpn_plan_pro',
  vpn_family:  'vpn_plan_family',
  vpn_1m:      'configs_plan_1m',
  vpn_3m:      'configs_plan_3m',
  vpn_1y:      'configs_plan_1y',
}

const PROTO_COLOR: Record<string, string> = {
  awg:   '#27ae60',
  vless: '#8e44ad',
}
const PROTO_LABEL: Record<string, string> = {
  awg:   'VPN',
  vless: 'Smart TV',
}

function QrModal({ url, onClose }: { url: string; onClose: () => void }) {
  const t = useT()
  const p = usePlural()
  const tp = WebApp.themeParams
  return (
    <>
      <div onClick={onClose} style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 200,
      }} />
      <div style={{
        position: 'fixed', bottom: 0, left: 0, right: 0,
        background: tp.bg_color ?? '#1c1c1e',
        borderRadius: '20px 20px 0 0',
        padding: '20px 24px 40px',
        zIndex: 201, textAlign: 'center',
      }}>
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: tp.hint_color ?? '#888', opacity: 0.4,
          margin: '0 auto 20px',
        }} />
        <div style={{ fontWeight: 700, fontSize: 17, color: tp.text_color, marginBottom: 6 }}>
          {t('configs_qr_title')}
        </div>
        <div style={{ fontSize: 13, color: tp.hint_color, marginBottom: 20 }}>
          {t('configs_qr_sub')}
        </div>
        <img
          src={url}
          alt={t('configs_qr_title')}
          style={{
            width: 220, height: 220,
            borderRadius: 12,
            background: '#fff',
            padding: 8,
            display: 'block',
            margin: '0 auto 20px',
          }}
        />
        <button onClick={onClose} style={{
          width: '100%', padding: '12px 0', borderRadius: 14, border: 'none',
          background: 'var(--section-bg)', color: tp.text_color,
          fontSize: 15, cursor: 'pointer',
        }}>
          {t('configs_close')}
        </button>
      </div>
    </>
  )
}

function ServerPicker({
  servers,
  protocol,
  onSelect,
  onClose,
  activating,
}: {
  servers:    VpnServer[]
  protocol:   string
  onSelect:   (serverId: number) => void
  onClose:    () => void
  activating: boolean
}) {
  const t = useT()
  const p = usePlural()
  const tp    = WebApp.themeParams
  const color = PROTO_COLOR[protocol] ?? '#888'
  const label = PROTO_LABEL[protocol] ?? protocol.toUpperCase()

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          zIndex: 200,
        }}
      />

      <div style={{
        position: 'fixed', bottom: 0, left: 0, right: 0,
        background: tp.bg_color ?? '#1c1c1e',
        borderRadius: '20px 20px 0 0',
        padding: '20px 16px 36px',
        zIndex: 201,
      }}>
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: tp.hint_color ?? '#888',
          opacity: 0.4, margin: '0 auto 20px',
        }} />

        <h3 style={{ margin: '0 0 6px', fontSize: 17, fontWeight: 600, color: tp.text_color }}>
          {t('configs_pick_server')}
        </h3>
        <p style={{ margin: '0 0 16px', fontSize: 13, color: tp.hint_color }}>
          {t('configs_proto')} <span style={{ color, fontWeight: 600 }}>{label}</span>
        </p>

        {activating ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: tp.hint_color, fontSize: 14 }}>
            {t('configs_activating')}
          </div>
        ) : (
          <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 14, overflow: 'hidden', marginBottom: 8 }}>
            {servers.map((srv, i) => (
              <button
                key={srv.id}
                onClick={() => onSelect(srv.id)}
                style={{
                  width: '100%', padding: '13px 16px',
                  border: 'none', background: 'transparent',
                  color: tp.text_color, fontSize: 15,
                  cursor: 'pointer', textAlign: 'left',
                  display: 'flex', alignItems: 'center', gap: 12,
                  borderBottom: i < servers.length - 1 ? '1px solid rgba(128,128,128,0.1)' : 'none',
                }}
              >
                <span style={{ fontSize: 22, flexShrink: 0 }}>{srv.location}</span>
                <span style={{ flex: 1, fontWeight: 500 }}>{srv.name}</span>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none" style={{ flexShrink: 0 }}>
                  <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            ))}
          </div>
        )}

        {!activating && (
          <button
            onClick={onClose}
            style={{
              width: '100%', padding: '12px 0', borderRadius: 14,
              border: 'none', background: 'transparent',
              color: tp.hint_color, fontSize: 15, cursor: 'pointer', marginTop: 4,
            }}
          >
            {t('configs_cancel')}
          </button>
        )}
      </div>
    </>
  )
}

function ProtoIcon({ protocol }: { protocol: string }) {
  if (protocol === 'awg') return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
        stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M9 12l2 2 4-4" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <rect x="3" y="3" width="18" height="18" rx="3" stroke="#fff" strokeWidth="2"/>
      <path d="M8 12h8M12 8v8" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
}

function SlotCard({
  slot, isLast, onActivate, onRevoke,
}: {
  slot: VpnConfig & { slot_num: number }
  isLast: boolean
  onActivate: (id: number, serverId: number) => Promise<void>
  onRevoke:   (id: number) => Promise<void>
}) {
  const t = useT()
  const p = usePlural()
  const tp      = WebApp.themeParams
  const color   = PROTO_COLOR[slot.protocol] ?? '#888'
  const label   = PROTO_LABEL[slot.protocol] ?? slot.protocol.toUpperCase()
  const isEmpty = slot.status === 'empty'

  const [activating,     setActivating]     = useState(false)
  const [revoking,       setRevoking]       = useState(false)
  const [showPicker,     setShowPicker]     = useState(false)
  const [showQr,         setShowQr]         = useState(false)
  const [servers,        setServers]        = useState<VpnServer[]>([])
  const [loadingServers, setLoadingServers] = useState(false)

  const handleAddClick = async () => {
    if (slot.protocol === 'vless') return
    setLoadingServers(true)
    try {
      const list = await getVpnServers(slot.protocol)
      setServers(list)
      setShowPicker(true)
    } finally {
      setLoadingServers(false)
    }
  }

  const handleSelectServer = async (serverId: number) => {
    setActivating(true)
    try {
      await onActivate(slot.id, serverId)
      setShowPicker(false)
    } finally {
      setActivating(false)
    }
  }

  const handleRevoke = () => {
    WebApp.showPopup(
      {
        title: t('configs_revoke_confirm'),
        message: `${label} #${slot.slot_num} ${t('configs_revoke_msg2')}`,
        buttons: [
          { id: 'cancel', type: 'cancel' },
          { id: 'ok', type: 'destructive', text: t('configs_revoke_btn2') },
        ],
      },
      async (btn) => {
        if (btn === 'ok') {
          WebApp.HapticFeedback.impactOccurred('medium')
          setRevoking(true)
          try { await onRevoke(slot.id) }
          finally { setRevoking(false) }
        }
      },
    )
  }

  const borderBottom = !isLast ? '1px solid rgba(128,128,128,0.1)' : 'none'

  return (
    <>
      <div style={{ borderBottom }}>
        <div style={{
          padding: '13px 16px',
          display: 'flex', alignItems: 'center', gap: 14,
        }}>
          <div style={{
            width: 40, height: 40, borderRadius: 12, flexShrink: 0,
            background: isEmpty ? `${color}33` : color,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            position: 'relative',
          }}>
            <ProtoIcon protocol={slot.protocol} />
            {!isEmpty && (
              <span style={{
                position: 'absolute', bottom: -3, right: -3,
                width: 12, height: 12, borderRadius: '50%',
                background: '#27ae60', border: '2px solid var(--bg, #fff)',
              }} />
            )}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: tp.text_color }}>
              {label} · #{slot.slot_num}
            </div>
            <div style={{ fontSize: 12, color: tp.hint_color, marginTop: 1 }}>
              {isEmpty
                ? (slot.protocol === 'vless' ? `🚧 ${t('configs_soon')}` : t('configs_not_activated'))
                : (slot.peer_name ?? `config_${slot.id}`)}
            </div>
          </div>

          {slot.protocol === 'vless' && isEmpty ? (
            <span style={{ fontSize: 11, color: tp.hint_color, fontWeight: 500 }}>{t('configs_soon')}</span>
          ) : isEmpty ? (
            <button
              onClick={handleAddClick}
              disabled={loadingServers}
              style={{
                padding: '7px 14px', borderRadius: 10, border: 'none',
                background: color, color: '#fff',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
                opacity: loadingServers ? 0.6 : 1, flexShrink: 0,
              }}
            >
              {loadingServers ? '...' : t('configs_add_btn')}
            </button>
          ) : revoking ? (
            <span style={{ fontSize: 12, color: tp.hint_color }}>{t('configs_revoking')}</span>
          ) : (
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setShowQr(true) }}
                style={{
                  padding: '7px 14px', borderRadius: 10, border: 'none',
                  background: color, color: '#fff',
                  fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 5,
                }}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <rect x="3" y="3" width="7" height="7" rx="1" stroke="#fff" strokeWidth="2"/>
                  <rect x="14" y="3" width="7" height="7" rx="1" stroke="#fff" strokeWidth="2"/>
                  <rect x="3" y="14" width="7" height="7" rx="1" stroke="#fff" strokeWidth="2"/>
                  <path d="M14 14h2v2h-2zM18 14h2v2h-2zM14 18h2v2h-2zM18 18h2v2h-2z" fill="#fff"/>
                </svg>
                QR
              </button>
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); window.open(getConfigDownloadUrl(slot.id), '_blank') }}
                style={{
                  width: 36, height: 36, borderRadius: 10, border: 'none',
                  background: `${color}18`, color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: 'pointer', flexShrink: 0,
                }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              <button
                onClick={handleRevoke}
                style={{
                  width: 36, height: 36, borderRadius: 10, border: 'none',
                  background: 'rgba(255,59,48,0.1)',
                  color: 'var(--tg-theme-destructive-text-color,#ff3b30)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: 'pointer', flexShrink: 0,
                }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
          )}
        </div>

        {revoking && (
          <div style={{ padding: '0 16px 12px 70px' }}>
            <div style={{ height: 3, borderRadius: 2, background: `${color}22`, overflow: 'hidden' }}>
              <div style={{ height: '100%', background: color, borderRadius: 2, animation: 'revoke-progress 1.4s ease-in-out infinite' }} />
            </div>
          </div>
        )}
      </div>

      {showPicker && (
        <ServerPicker
          servers={servers} protocol={slot.protocol}
          onSelect={handleSelectServer}
          onClose={() => !activating && setShowPicker(false)}
          activating={activating}
        />
      )}
      {showQr && <QrModal url={getConfigQrUrl(slot.id)} onClose={() => setShowQr(false)} />}
    </>
  )
}

function SubscriptionGroup({
  slots, onActivate, onRevoke,
}: {
  subscriptionId: number
  slots: (VpnConfig & { slot_num: number; subscription_id: number })[]
  onActivate: (id: number, serverId: number) => Promise<void>
  onRevoke:   (id: number) => Promise<void>
}) {
  const t = useT()
  const p = usePlural()
  const tp    = WebApp.themeParams
  const first = slots[0]

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 4px 8px' }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: tp.text_color }}>
          {PLAN_KEY[first.plan] ? t(PLAN_KEY[first.plan] as TKey) : first.plan}
        </span>
        <span style={{ fontSize: 12, color: tp.hint_color }}>
          {t('configs_until')} {formatDate(first.expires_at)}
        </span>
      </div>

      <div style={{ background: 'var(--section-bg)', border: '1px solid var(--card-border)', borderRadius: 16, overflow: 'hidden' }}>
        {slots.map((slot, i) => (
          <SlotCard
            key={slot.id}
            slot={slot}
            isLast={i === slots.length - 1}
            onActivate={onActivate}
            onRevoke={onRevoke}
          />
        ))}
      </div>
    </div>
  )
}

type RawSlot = VpnConfig & { slot_num: number; subscription_id: number }

export default function Configs() {
  const t = useT()
  const p = usePlural()
  const nav = useNavigate()
  const tp  = WebApp.themeParams

  const [slots,    setSlots]    = useState<RawSlot[]>([])
  const [loading,  setLoading]  = useState(true)
  const [errMsg,  setErrMsg]   = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    WebApp.BackButton.onClick(() => nav('/vpn'))
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(() => nav('/vpn')) }
  }, [nav])

  const load = () => {
    setLoading(true)
    getUserConfigs()
      .then(data => setSlots(data as RawSlot[]))
      .catch(() => setErrMsg(t('configs_err_load')))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const handleActivate = async (configId: number, serverId: number) => {
    try {
      await activateSlot(configId, serverId)
      WebApp.HapticFeedback.notificationOccurred('success')
      load()
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : t('configs_err_activate'))
    }
  }

  const handleRevoke = async (configId: number) => {
    try {
      await revokeConfig(configId)
      setSlots(prev => prev.map(s =>
        s.id === configId
          ? { ...s, status: 'empty', peer_name: null }
          : s
      ))
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : t('configs_err_revoke'))
    }
  }

  const bySubscription = slots.reduce<Record<number, RawSlot[]>>((acc, s) => {
    const key = s.subscription_id
    if (!acc[key]) acc[key] = []
    acc[key].push(s)
    return acc
  }, {})

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 90px)' }}>
      <RevokeStyle />
      <div style={{ padding: '6px 4px 2px' }}>
        <div style={{ fontWeight: 800, fontSize: 24, color: tp.text_color, marginBottom: 4 }}>{t('configs_title')}</div>
        <div style={{ fontSize: 13, color: tp.hint_color, display: 'flex', gap: 12 }}>
          <span style={{ color: '#27ae60' }}>{t('configs_legend_vpn')}</span>
          <span style={{ color: '#8e44ad' }}>{t('configs_legend_tv')}</span>
        </div>
      </div>

      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[1,2,3].map(i => (
            <div key={i} className="skeleton" style={{ height: 90, borderRadius: 14 }} />
          ))}
        </div>
      )}

      {!loading && slots.length === 0 && !errMsg && (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <div style={{
            width: 64, height: 64, borderRadius: 20, margin: '0 auto 16px',
            background: 'var(--section-bg)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 30,
          }}>🔒</div>
          <div style={{ fontWeight: 600, fontSize: 17, color: tp.text_color, marginBottom: 6 }}>{t('configs_no_sub')}</div>
          <p style={{ color: tp.hint_color, fontSize: 13, marginBottom: 24 }}>{t('configs_no_sub_sub')}</p>
          <button className="btn" style={{ padding: '11px 32px' }} onClick={() => nav('/vpn/plans')}>{t('configs_buy')}</button>
        </div>
      )}

      {Object.entries(bySubscription).map(([subId, subSlots]) => (
        <SubscriptionGroup
          key={subId}
          subscriptionId={Number(subId)}
          slots={subSlots}
          onActivate={handleActivate}
          onRevoke={handleRevoke}
        />
      ))}

      {errMsg && (
        <p style={{
          color: 'var(--tg-theme-destructive-text-color, #ff3b30)',
          textAlign: 'center', fontSize: 14, marginTop: 12,
        }}>
          {errMsg}
        </p>
      )}
    </div>
  )
}