import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getUserConfigs, getConfigDownloadUrl, getConfigQrUrl, getVpnServers,
  activateSlot, revokeConfig,
  type VpnConfig, type VpnServer,
} from '../api'
import { useT, useLang, type TKey } from '../i18n'

function formatDate(iso: string, lang: string): string {
  try {
    return new Date(iso).toLocaleDateString(lang === 'en' ? 'en-US' : 'ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
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

const PROTO_BG: Record<string, string> = {
  awg:   'bg-success',
  vless: 'bg-purple',
}
const PROTO_BG_DIM: Record<string, string> = {
  awg:   'bg-success/20',
  vless: 'bg-purple/20',
}
const PROTO_TEXT: Record<string, string> = {
  awg:   'text-success',
  vless: 'text-purple',
}
const PROTO_LABEL: Record<string, string> = {
  awg:   'VPN',
  vless: 'Smart TV',
}

function QrModal({ url, onClose }: { url: string; onClose: () => void }) {
  const t = useT()
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/65 z-[200]" />
      <div className="fixed bottom-0 left-0 right-0 bg-[var(--tg-theme-bg-color,#1c1c1e)] rounded-t-[20px] px-6 pt-5 pb-10 z-[201] text-center">
        <div className="w-9 h-1 rounded-sm bg-[var(--tg-theme-hint-color,#888)] opacity-40 mx-auto mb-5" />
        <div className="font-bold text-[17px] text-[var(--tg-theme-text-color)] mb-1.5">
          {t('configs_qr_title')}
        </div>
        <div className="text-[13px] text-[var(--tg-theme-hint-color)] mb-5">
          {t('configs_qr_sub')}
        </div>
        <img
          src={url}
          alt={t('configs_qr_title')}
          className="w-[220px] h-[220px] rounded-xl bg-white p-2 block mx-auto mb-5"
        />
        <button onClick={onClose} className="w-full py-3 rounded-[14px] border-none bg-[var(--tg-theme-section-bg-color)] text-[var(--tg-theme-text-color)] text-[15px] cursor-pointer">
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
  const color = PROTO_TEXT[protocol] ?? 'text-[#888]'
  const label = PROTO_LABEL[protocol] ?? protocol.toUpperCase()

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/50 z-[200]" />

      <div className="fixed bottom-0 left-0 right-0 bg-[var(--tg-theme-bg-color,#1c1c1e)] rounded-t-[20px] pt-5 px-4 pb-9 z-[201]">
        <div className="w-9 h-1 rounded-sm bg-[var(--tg-theme-hint-color,#888)] opacity-40 mx-auto mb-5" />

        <h3 className="m-0 mb-1.5 text-[17px] font-semibold text-[var(--tg-theme-text-color)]">
          {t('configs_pick_server')}
        </h3>
        <p className="m-0 mb-4 text-[13px] text-[var(--tg-theme-hint-color)]">
          {t('configs_proto')} <span className={`${color} font-semibold`}>{label}</span>
        </p>

        {activating ? (
          <div className="text-center py-6 text-[var(--tg-theme-hint-color)] text-sm">
            {t('configs_activating')}
          </div>
        ) : (
          <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-[14px] overflow-hidden mb-2">
            {servers.map((srv, i) => (
              <button
                key={srv.id}
                onClick={() => onSelect(srv.id)}
                className={`w-full py-[13px] px-4 border-none bg-transparent text-[var(--tg-theme-text-color)] text-[15px] cursor-pointer text-left flex items-center gap-3 ${i < servers.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
              >
                <span className="text-[22px] shrink-0">{srv.location}</span>
                <span className="flex-1 font-medium">{srv.name}</span>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none" className="shrink-0">
                  <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            ))}
          </div>
        )}

        {!activating && (
          <button
            onClick={onClose}
            className="w-full py-3 rounded-[14px] border-none bg-transparent text-[var(--tg-theme-hint-color)] text-[15px] cursor-pointer mt-1"
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
  const bg      = PROTO_BG[slot.protocol] ?? 'bg-[#888]'
  const bgDim   = PROTO_BG_DIM[slot.protocol] ?? 'bg-[#8888]/20'
  const label    = PROTO_LABEL[slot.protocol] ?? slot.protocol.toUpperCase()
  const isEmpty  = slot.status === 'empty'

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

  return (
    <>
      <div className={isLast ? '' : 'border-b border-solid border-[var(--card-border)]'}>
        <div className="py-[13px] px-4 flex items-center gap-[14px]">
          <div className={`w-10 h-10 rounded-xl shrink-0 flex items-center justify-center relative ${isEmpty ? bgDim : bg}`}>
            <ProtoIcon protocol={slot.protocol} />
            {!isEmpty && (
              <span className="absolute -bottom-[3px] -right-[3px] w-3 h-3 rounded-full bg-success border-2 border-[var(--tg-theme-bg-color,#fff)]" />
            )}
          </div>

          <div className="flex-1 min-w-0">
            <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">
              {label} · #{slot.slot_num}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">
              {isEmpty
                ? (slot.protocol === 'vless' ? `🚧 ${t('configs_soon')}` : t('configs_not_activated'))
                : (slot.peer_name ?? `config_${slot.id}`)}
            </div>
          </div>

          {slot.protocol === 'vless' && isEmpty ? (
            <span className="text-[11px] text-[var(--tg-theme-hint-color)] font-medium">{t('configs_soon')}</span>
          ) : isEmpty ? (
            <button
              onClick={handleAddClick}
              disabled={loadingServers}
              className={`${bg} text-white text-[13px] font-semibold cursor-pointer rounded-[10px] py-[7px] px-[14px] border-none shrink-0 ${loadingServers ? 'opacity-60' : ''}`}
            >
              {loadingServers ? '...' : t('configs_add_btn')}
            </button>
          ) : revoking ? (
            <span className="text-xs text-[var(--tg-theme-hint-color)]">{t('configs_revoking')}</span>
          ) : (
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); setShowQr(true) }}
                className={`${bg} text-white text-[13px] font-semibold cursor-pointer rounded-[10px] py-[7px] px-[14px] border-none flex items-center gap-[5px]`}
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
                className={`w-9 h-9 rounded-[10px] border-none ${bgDim} flex items-center justify-center cursor-pointer shrink-0`}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              <button
                onClick={handleRevoke}
                className="w-9 h-9 rounded-[10px] border-none bg-danger/10 text-[var(--tg-theme-destructive-text-color,#ff3b30)] flex items-center justify-center cursor-pointer shrink-0"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
          )}
        </div>

        {revoking && (
          <div className="pl-[70px] pr-4 pb-3">
            <div className="h-[3px] rounded-sm bg-success/10 overflow-hidden">
              <div className="h-full rounded-sm bg-success animate-revoke-progress" />
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
  slots, onActivate, onRevoke, lang,
}: {
  subscriptionId: number
  slots: (VpnConfig & { slot_num: number; subscription_id: number })[]
  onActivate: (id: number, serverId: number) => Promise<void>
  onRevoke:   (id: number) => Promise<void>
  lang: string
}) {
  const t = useT()
  const first = slots[0]

  return (
    <div className="mb-2">
      <div className="flex justify-between items-center px-1 pb-2">
        <span className="text-[13px] font-bold text-[var(--tg-theme-text-color)]">
          {PLAN_KEY[first.plan] ? t(PLAN_KEY[first.plan] as TKey) : first.plan}
        </span>
        <span className="text-xs text-[var(--tg-theme-hint-color)]">
          {t('configs_until')} {formatDate(first.expires_at, lang)}
        </span>
      </div>

      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
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
  const { lang } = useLang()
  const nav = useNavigate()

  const [slots,    setSlots]    = useState<RawSlot[]>([])
  const [loading,  setLoading]  = useState(true)
  const [errMsg,  setErrMsg]   = useState('')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
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
      <div className="px-1 pt-1.5 pb-0.5">
        <div className="text-2xl font-extrabold text-[var(--tg-theme-text-color)] mb-1">{t('configs_title')}</div>
        <div className="text-[13px] text-[var(--tg-theme-hint-color)] flex gap-3">
          <span className="text-success">{t('configs_legend_vpn')}</span>
          <span className="text-purple">{t('configs_legend_tv')}</span>
        </div>
      </div>

      {loading && (
        <div className="flex flex-col gap-[10px]">
          {[1,2,3].map(i => (
            <div key={i} className="skeleton h-[90px] rounded-[14px]" />
          ))}
        </div>
      )}

      {!loading && slots.length === 0 && !errMsg && (
        <div className="text-center py-10">
          <div className="w-16 h-16 rounded-[20px] mx-auto mb-4 bg-[var(--tg-theme-section-bg-color)] flex items-center justify-center text-[30px]">🔒</div>
          <div className="font-semibold text-[17px] text-[var(--tg-theme-text-color)] mb-1.5">{t('configs_no_sub')}</div>
          <p className="text-[var(--tg-theme-hint-color)] text-[13px] mb-6">{t('configs_no_sub_sub')}</p>
          <button className="btn py-[11px] px-8" onClick={() => nav('/vpn/plans')}>{t('configs_buy')}</button>
        </div>
      )}

      {Object.entries(bySubscription).map(([subId, subSlots]) => (
        <SubscriptionGroup
          key={subId}
          subscriptionId={Number(subId)}
          slots={subSlots}
          onActivate={handleActivate}
          onRevoke={handleRevoke}
          lang={lang}
        />
      ))}

      {errMsg && (
        <p className="text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-center text-sm mt-3">
          {errMsg}
        </p>
      )}
    </div>
  )
}