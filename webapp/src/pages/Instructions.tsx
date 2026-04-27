import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

interface Instruction {
  id:       string
  titleKey: string
  stepKeys: string[]
  link?:    { label: string; url: string }
}

const AWG_INSTRUCTIONS: Instruction[] = [
  { id: 'ios', titleKey: 'device_ios', stepKeys: ['instr_ios_1', 'instr_ios_2', 'instr_ios_3', 'instr_ios_4'], link: { label: 'App Store', url: 'https://apps.apple.com/app/amneziavpn/id1600529126' } },
  { id: 'android', titleKey: 'device_android', stepKeys: ['instr_android_1', 'instr_android_2', 'instr_android_3', 'instr_android_4', 'instr_android_5'], link: { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=org.amnezia.vpn' } },
  { id: 'windows', titleKey: 'device_windows', stepKeys: ['instr_windows_1', 'instr_windows_2', 'instr_windows_3', 'instr_windows_4', 'instr_windows_5'], link: { label: 'GitHub Releases', url: 'https://github.com/amnezia-vpn/amnezia-client/releases' } },
  { id: 'macos', titleKey: 'device_macos', stepKeys: ['instr_macos_1', 'instr_macos_2', 'instr_macos_3', 'instr_macos_4', 'instr_macos_5'], link: { label: 'App Store', url: 'https://apps.apple.com/app/amneziavpn/id1600529126' } },
  { id: 'androidtv', titleKey: 'device_androidtv', stepKeys: ['instr_androidtv_1', 'instr_androidtv_2', 'instr_androidtv_3', 'instr_androidtv_4', 'instr_androidtv_5'], link: { label: 'GitHub (APK)', url: 'https://github.com/amnezia-vpn/amnezia-client/releases' } },
]

const VLESS_INSTRUCTIONS: Instruction[] = [
  { id: 'smarttube', titleKey: 'device_smarttube', stepKeys: ['instr_smarttube_1', 'instr_smarttube_2', 'instr_smarttube_3', 'instr_smarttube_4'] },
  { id: 'v2rayng', titleKey: 'device_v2rayng', stepKeys: ['instr_v2rayng_1', 'instr_v2rayng_2', 'instr_v2rayng_3', 'instr_v2rayng_4'], link: { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=com.v2ray.ang' } },
  { id: 'streisand', titleKey: 'device_streisand', stepKeys: ['instr_streisand_1', 'instr_streisand_2', 'instr_streisand_3', 'instr_streisand_4'], link: { label: 'App Store', url: 'https://apps.apple.com/app/streisand/id6450534064' } },
]

function DeviceIcon({ id }: { id: string }) {
  if (id === 'ios' || id === 'streisand') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/>
      <line x1="9" y1="18" x2="15" y2="18" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
  if (id === 'android' || id === 'v2rayng') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="5" y="1" width="14" height="22" rx="2" stroke="#fff" strokeWidth="2"/>
      <line x1="9" y1="17" x2="15" y2="17" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
      <line x1="12" y1="5" x2="12" y2="2" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
      <line x1="12" y1="19" x2="12" y2="22" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
  if (id === 'windows') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="3" width="8" height="8" stroke="#fff" strokeWidth="2"/>
      <rect x="13" y="3" width="8" height="8" stroke="#fff" strokeWidth="2"/>
      <rect x="3" y="13" width="8" height="8" stroke="#fff" strokeWidth="2"/>
      <rect x="13" y="13" width="8" height="8" stroke="#fff" strokeWidth="2"/>
    </svg>
  )
  if (id === 'macos') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <path d="M17 5.5C17 4.1 15.9 3 14.5 3c-.8 0-1.5.3-2 .8-.5-.5-1.2-.8-2-.8C9.1 3 8 4.1 8 5.5c0 .4.1.7.3 1.1-.5.4-.8 1-.8 1.6 0 .4.1.7.2 1C7.1 9.7 6.6 10 6 10c-.3 0-.5-.1-.7-.2-.2.3-.3.6-.3 1 0 .8.6 1.5 1.4 1.8-.1.4-.2.8-.2 1.2 0 1 .4 1.9 1 2.6.6.6 1.4 1 2.3 1 1 0 1.8-.4 2.3-1 .5.6 1.3 1 2.3 1 1 0 1.8-.4 2.3-1 .5.6 1.3 1 2.3 1 1 0 1.8-.4 2.3-1 .5.6 1 1 1.7 1 .6 0 1.1-.4 1.1-1 0-.6-.4-1-1.1-1-.6 0-1.2-.4-1.6-1-.5-.7-1.2-1.8-1.2-3 0-1.2.5-2.1 1.3-2.8.7-.6 1.6-.9 2.6-.9.5 0 1 .1 1.4.2.2-.6.2-1.2.2-1.8 0-.6-.1-1.2-.2-1.8-.4.1-.9.2-1.4.2-1 0-1.9-.4-2.6-1-.8-.7-1.3-1.7-1.3-2.8 0-.3 0-.6.1-.9.5-.3.9-.7.9-1.3 0-.3-.1-.6-.4-.9-.3-.4-.7-.6-1.2-.6-.4 0-.8.2-1.2.5-.4.4-.7.9-.8 1.5-.1.6-.2 1.3-.2 2 0 .7.1 1.3.2 2-.2.3-.3.5-.5.8z" stroke="#fff" strokeWidth="1.2"/>
    </svg>
  )
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="2" y="4" width="20" height="14" rx="2" stroke="#fff" strokeWidth="2"/>
      <line x1="8" y1="21" x2="16" y2="21" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
      <line x1="12" y1="18" x2="12" y2="21" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
      <line x1="7" y1="8" x2="17" y2="8" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
}

const DEVICE_COLORS: Record<string, string> = {
  ios: 'bg-[#007aff]', android: 'bg-success', windows: 'bg-[#0078d4]',
  macos: 'bg-[#888]', androidtv: 'bg-purple', smarttube: 'bg-purple',
  v2rayng: 'bg-success', streisand: 'bg-[#007aff]',
}

function AccordionGroup({ items, accentColor, t }: { items: Instruction[]; accentColor: string; t: ReturnType<typeof useT> }) {
  const [open, setOpen] = useState<string | null>(null)

  return (
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
      {items.map((item, i) => {
        const isOpen = open === item.id
        const color = DEVICE_COLORS[item.id] ?? accentColor
        return (
          <div key={item.id}>
            <button
              onClick={() => { setOpen(isOpen ? null : item.id); WebApp.HapticFeedback.selectionChanged() }}
              className={`w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-center gap-[14px] ${(isOpen || i < items.length - 1) ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
            >
              <div className={`w-10 h-10 rounded-[10px] shrink-0 ${color} flex items-center justify-center`}>
                <DeviceIcon id={item.id} />
              </div>
              <span className="flex-1 text-[15px] font-semibold text-[var(--tg-theme-text-color)] text-left">
                {t(item.titleKey as any)}
              </span>
              <svg width="7" height="12" viewBox="0 0 7 12" fill="none" className={`shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}>
                <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            {isOpen && (
              <div className={`py-3 px-4 pl-[70px] ${i < items.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                <ol className="m-0 pl-4 leading-[1.9]">
                  {item.stepKeys.map((stepKey, si) => (
                    <li key={si} className="text-[13px] text-[var(--tg-theme-text-color)] mb-0.5">{t(stepKey as any)}</li>
                  ))}
                </ol>
                {item.link && (
                  <a href={item.link.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-[5px] mt-2.5 text-[13px] text-[var(--tg-theme-link-color,#2481cc)] no-underline">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    {item.link.label}
                  </a>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function Instructions() {
  const nav = useNavigate()
  const t   = useT()

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)' }}>
      <div className="px-1 pt-1.5 pb-0.5">
        <div className="text-2xl font-extrabold text-[var(--tg-theme-text-color)] mb-1">{t('instr_title')}</div>
        <div className="text-[13px] text-[var(--tg-theme-hint-color)]">{t('instr_sub')}</div>
      </div>

      <span className="section-title">{t('instr_awg')}</span>
      <div className="text-xs text-[var(--tg-theme-hint-color)] -mt-1 mx-1 mb-1">
        {t('instr_awg_desc')}
      </div>
      <AccordionGroup items={AWG_INSTRUCTIONS} accentColor="bg-success" t={t} />

      <span className="section-title pt-2">{t('instr_vless')}</span>
      <div className="text-xs text-[var(--tg-theme-hint-color)] -mt-1 mx-1 mb-1">
        {t('instr_vless_desc')}
      </div>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        <div className="py-[13px] px-4 flex items-center gap-[14px]">
          <div className="w-10 h-10 rounded-[10px] shrink-0 bg-purple flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div className="flex-1">
            <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">{t('instr_vless_soon')}</div>
            <div className="text-xs text-[var(--tg-theme-hint-color)] mt-px">{t('instr_vless_soon_sub')}</div>
          </div>
        </div>
      </div>
      <AccordionGroup items={VLESS_INSTRUCTIONS} accentColor="bg-purple" t={t} />
    </div>
  )
}