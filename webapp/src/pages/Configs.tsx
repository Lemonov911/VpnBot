import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import {
  getUserConfigs, getConfigDownloadUrl, getConfigQrUrl, getVpnServers,
  activateSlot, revokeConfig, getTrialStatus, claimTrial,
  getActiveSubscription,
  type VpnConfig, type VpnServer, type TrialStatus, type Subscription,
} from '../api'
import { useT, useLang, type TKey } from '../i18n'
import { copyText } from '../utils/clipboard'
import { SubscriptionUrlCard } from '../components/SubscriptionUrlCard'

function formatDate(iso: string, lang: string): string {
  try {
    return new Date(iso).toLocaleDateString(lang === 'en' ? 'en-US' : 'ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
  } catch { return iso }
}

const PLAN_KEY: Record<string, string> = {
  vpn_base:    'vpn_plan_base',
  vpn_max:     'vpn_plan_max',
  vpn_trial:   'vpn_plan_trial',
  vpn_start:   'vpn_plan_start',
  vpn_popular: 'vpn_plan_popular',
  vpn_pro:     'vpn_plan_pro',
  vpn_family:  'vpn_plan_family',
  vpn_1m:      'configs_plan_1m',
  vpn_3m:      'configs_plan_3m',
  vpn_1y:      'configs_plan_1y',
}

// Цветовая палитра по протоколам:
//   vless — фиолетовый (Reality / Xray)
//   wg    — изумрудно-зелёный (plain WireGuard, без обфускации)
//   awg   — синий cyan (AmneziaWG, с обфускацией под МТС DPI)
const PROTO_BG: Record<string, string> = {
  vless: 'bg-purple',
  wg:    'bg-emerald-500',
  awg:   'bg-cyan-500',
}
const PROTO_BG_DIM: Record<string, string> = {
  vless: 'bg-purple/20',
  wg:    'bg-emerald-500/20',
  awg:   'bg-cyan-500/20',
}
const PROTO_TEXT: Record<string, string> = {
  vless: 'text-purple',
  wg:    'text-emerald-500',
  awg:   'text-cyan-500',
}
const PROTO_LABEL: Record<string, string> = {
  vless: 'VLESS · Reality',
  wg:    'WireGuard',
  awg:   'AmneziaWG',
}

const PROTO_HINT: Record<string, string> = {
  vless: 'Любое устройство · шифрует трафик',
  wg:    'WireGuard · Роутер · OpenWrt',
  awg:   'AmneziaWG · iOS · Android · Mac',
}

function QrModal({ url, protocol, onClose }: { url: string; protocol?: string; onClose: () => void }) {
  const t = useT()
  // AWG/WG QR ≠ VLESS QR. Раньше для всех писали «Отсканируй в Happ» — это
  // wrong app для AmneziaWG: AWG QR работает только в Amnezia VPN / AmneziaWG.
  const isWG = protocol === 'awg' || protocol === 'wg'
  const titleKey = isWG ? 'configs_qr_title_wg' : 'configs_qr_title'
  const subKey   = isWG ? 'configs_qr_sub_wg'   : 'configs_qr_sub'
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/65 z-[200]" />
      <div className="fixed bottom-0 left-0 right-0 bg-[var(--tg-theme-bg-color,#1c1c1e)] rounded-t-[20px] px-6 pt-5 pb-10 z-[201] text-center">
        <div className="w-9 h-1 rounded-sm bg-[var(--tg-theme-hint-color,#888)] opacity-40 mx-auto mb-5" />
        <div className="font-bold text-[17px] text-[var(--tg-theme-text-color)] mb-1.5">
          {t(titleKey as never)}
        </div>
        <div className="text-[13px] text-[var(--tg-theme-hint-color)] mb-5">
          {t(subKey as never)}
        </div>
        <img
          src={url}
          alt={t(titleKey as never)}
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


function ProtoIcon({ protocol: _protocol }: { protocol: string }) {
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

  const [activating,       setActivating]       = useState(false)
  const [revoking,         setRevoking]         = useState(false)
  const [showPicker,       setShowPicker]       = useState(false)
  const [showQr,           setShowQr]           = useState(false)
  const [servers,          setServers]          = useState<VpnServer[]>([])
  const [loadingServers,   setLoadingServers]   = useState(false)
  const [copied,           setCopied]           = useState(false)

  const handleAddClick = async () => {
    await loadServers(slot.protocol)
  }

  const loadServers = async (protocol: string) => {
    setLoadingServers(true)
    try {
      const list = await getVpnServers(protocol)
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
    } catch {
      // Parent (Configs) уже выставил errMsg в state.  Закроем picker
      // в любом случае, иначе юзер залипает на «⏳ Создаём конфиг»-spinner
      // не зная что произошёл fail (ошибка-таблетка прячется за модалкой).
    } finally {
      setShowPicker(false)
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
            <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] truncate">
              {/* Когда активен — показываем `#1 · Amsterdam`, без префикса протокола
                  (его уже видно по цветной иконке слева). Когда empty — нужен label. */}
              {isEmpty
                ? `${label} · #${slot.slot_num}`
                : `#${slot.slot_num} · ${slot.server_flag ? slot.server_flag + ' ' : ''}${slot.server_name || slot.label || slot.peer_name || `config_${slot.id}`}`}
            </div>
            <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px truncate">
              {isEmpty
                ? t('configs_not_activated')
                : PROTO_HINT[slot.protocol] ?? label}
            </div>
            {!isEmpty && (slot.rx_bytes > 0 || slot.tx_bytes > 0) && (
              <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-0.5 opacity-70">
                ↓ {slot.rx_human} · ↑ {slot.tx_human}
              </div>
            )}
          </div>

          {isEmpty ? (
            <button
              onClick={handleAddClick}
              disabled={loadingServers}
              className={`${bg} text-white text-[13px] font-semibold cursor-pointer rounded-[10px] py-[7px] px-[14px] border-none shrink-0 ${loadingServers ? 'opacity-60' : ''}`}
            >
              {loadingServers ? '...' : t('configs_add_btn')}
            </button>
          ) : revoking ? (
            <span className="text-xs text-[var(--tg-theme-hint-color)]">{t('configs_revoking')}</span>
          ) : slot.protocol === 'vless' ? (
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => {
                  WebApp.HapticFeedback.impactOccurred('light')
                  copyText(slot.vless_url || '', () => {
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1500)
                  })
                }}
                className={`${bg} text-white text-[13px] font-semibold cursor-pointer rounded-[10px] py-[7px] px-[14px] border-none flex items-center gap-[5px]`}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                {copied ? '✓' : t('configs_copy')}
              </button>
              <button
                onClick={handleRevoke}
                aria-label="revoke"
                className="w-11 h-11 ml-1 rounded-[10px] border border-danger/30 bg-danger/15 text-[var(--tg-theme-destructive-text-color,#ff3b30)] flex items-center justify-center cursor-pointer shrink-0 active:bg-danger active:text-white"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                  <path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            </div>
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
                onClick={() => {
                  WebApp.HapticFeedback.impactOccurred('light')
                  const url = getConfigDownloadUrl(slot.id)
                  const filename = `${slot.peer_name || `vpn_config_${slot.id}`}.conf`
                  // Пробуем downloadFile через нативный объект (SDK-обёртка его может не проксировать)
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  const nativeTg = (window as any).Telegram?.WebApp
                  if (typeof nativeTg?.downloadFile === 'function') {
                    nativeTg.downloadFile({ url, file_name: filename })
                  } else if (typeof (WebApp as unknown as { downloadFile?: unknown }).downloadFile === 'function') {
                    (WebApp as unknown as { downloadFile: (p: { url: string; file_name: string }) => void }).downloadFile({ url, file_name: filename })
                  } else {
                    WebApp.openLink(url)
                  }
                }}
                className={`w-11 h-11 rounded-[10px] border-none ${bg} flex items-center justify-center cursor-pointer shrink-0`}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                </svg>
              </button>
              <button
                onClick={handleRevoke}
                aria-label="revoke"
                className="w-11 h-11 ml-1 rounded-[10px] border border-danger/30 bg-danger/15 text-[var(--tg-theme-destructive-text-color,#ff3b30)] flex items-center justify-center cursor-pointer shrink-0 active:bg-danger active:text-white"
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
      {showQr && <QrModal url={getConfigQrUrl(slot.id)} protocol={slot.protocol} onClose={() => setShowQr(false)} />}
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
  const [sub,      setSub]      = useState<Subscription | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [errMsg,  setErrMsg]   = useState('')
  // Lazy-fetch trial status — нужен только для empty state. Если у юзера
  // уже есть слоты, fetching не нужен.
  const [trial,    setTrial]    = useState<TrialStatus | null>(null)
  const [claiming, setClaiming] = useState(false)

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  const load = () => {
    setLoading(true)
    // Параллельно: конфиги (для AWG/WG slot-листа) + подписка (sub_url для VLESS).
    Promise.all([
      getUserConfigs().catch(() => [] as VpnConfig[]),
      getActiveSubscription().catch(() => null),
    ])
      .then(([data, s]) => {
        setSlots(data as RawSlot[])
        setSub(s)
        // Если ни конфигов, ни подписки — фетчим trial-статус для empty-state CTA
        if ((data as RawSlot[]).length === 0) {
          getTrialStatus().then(setTrial).catch(() => {})
        }
      })
      .catch(() => setErrMsg(t('configs_err_load')))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const handleClaimTrial = async () => {
    setClaiming(true)
    try {
      await claimTrial()
      WebApp.HapticFeedback.notificationOccurred('success')
      load()  // конфиги появились → перезагрузить
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : t('trial_err_generic'))
    } finally {
      setClaiming(false)
    }
  }

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

  // VLESS отдаётся как Subscription URL (multi-location в одном URL, импорт в Happ).
  // Per-slot UI для VLESS больше не нужен — юзер не выбирает локацию руками,
  // Happ сам показывает дропдаун со всеми серверами в подписке.
  // На странице оставляем только AWG/WG — у них per-device .conf файлы.
  const nonVlessSlots = slots.filter(s => s.protocol !== 'vless')
  const bySubscription = nonVlessSlots.reduce<Record<number, RawSlot[]>>((acc, s) => {
    const key = s.subscription_id
    if (!acc[key]) acc[key] = []
    acc[key].push(s)
    return acc
  }, {})

  // Бэк отдаёт sub_url только если у юзера есть active VLESS-конфиги
  // (см. _sub_url_for в webapp_api.py), так что отдельно проверять
  // slots.some(...) не нужно — иначе бывали race-condition'ы когда
  // /api/vpn/configs запаздывал, hasAnyVless=false и карточка скрывалась.
  const showSubCard = !!sub?.sub_url

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)' }}>
      {/* Legend-блок убран — после ухода VLESS в подписочный режим он стал
          монохромным («● VPN — телефон / ноутбук»), занимал место и не нёс
          информации.  Если в будущем вернётся eSIM или ещё протокол —
          возвращаем legend здесь. */}

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
          {/* Если триал доступен — главный CTA это «бесплатно 3 дня», иначе «купить».
              Раньше юзер landing'ом на /configs (deep-link, payment fail) не видел
              триал-опцию вообще, шёл «Купить» или уходил. */}
          {trial?.eligible ? (
            <>
              <button className="btn py-[11px] px-8 mb-3" disabled={claiming} onClick={handleClaimTrial}>
                {claiming ? t('trial_claiming') : `🎁 ${t('trial_banner_btn')}`}
              </button>
              <div>
                <button onClick={() => nav('/vpn/plans')} className="text-[12px] text-[var(--tg-theme-hint-color)] underline border-none bg-transparent cursor-pointer">
                  {t('configs_buy')}
                </button>
              </div>
            </>
          ) : (
            <button className="btn py-[11px] px-8" onClick={() => nav('/vpn/plans')}>{t('configs_buy')}</button>
          )}
        </div>
      )}

      {/* VLESS-подписка сверху: один URL, все локации в Happ-дропдауне */}
      {showSubCard && (
        <div className="mb-3">
          <SubscriptionUrlCard subUrl={sub!.sub_url!} />
          {/* Если у юзера только подписка и НЕТ AWG/WG-слотов (например триал),
              без подсказки страница выглядит куцой («где остальные конфиги?»).
              Объясняем: все устройства активируются по одной ссылке. */}
          {nonVlessSlots.length === 0 && (
            <p className="text-[11px] text-[var(--tg-theme-hint-color)] mt-2 px-1 leading-[1.4]">
              {t('configs_sub_only_hint' as never)}
            </p>
          )}
        </div>
      )}

      {/* AWG / WG слоты ниже — per-device .conf файлы. */}
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