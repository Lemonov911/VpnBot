import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

interface Platform {
  id:       'ios' | 'android' | 'macos' | 'windows'
  titleKey: 'device_ios' | 'device_android' | 'device_macos' | 'device_windows'
  stepKeys: string[]
  link:     { label: string; url: string }
  color:    string
}

const PLATFORMS: Platform[] = [
  {
    id: 'ios',
    titleKey: 'device_ios',
    stepKeys: ['instr_ios_1', 'instr_ios_2', 'instr_ios_3', 'instr_ios_4'],
    link:  { label: 'App Store', url: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215' },
    color: 'bg-[#007aff]',
  },
  {
    id: 'android',
    titleKey: 'device_android',
    stepKeys: ['instr_android_1', 'instr_android_2', 'instr_android_3', 'instr_android_4'],
    link:  { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=com.happproxy' },
    color: 'bg-success',
  },
  {
    id: 'macos',
    titleKey: 'device_macos',
    stepKeys: ['instr_macos_1', 'instr_macos_2', 'instr_macos_3'],
    link:  { label: 'Mac App Store', url: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215' },
    color: 'bg-[#888]',
  },
  {
    id: 'windows',
    titleKey: 'device_windows',
    stepKeys: ['instr_windows_1', 'instr_windows_2', 'instr_windows_3'],
    link:  { label: 'happ.su', url: 'https://happ.su' },
    color: 'bg-[#0078d4]',
  },
]

function PlatformIcon({ id }: { id: Platform['id'] }) {
  if (id === 'ios') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="5" y="2" width="14" height="20" rx="2" stroke="#fff" strokeWidth="2"/>
      <line x1="9" y1="18" x2="15" y2="18" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
  if (id === 'android') return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="5" y="1" width="14" height="22" rx="2" stroke="#fff" strokeWidth="2"/>
      <line x1="9" y1="17" x2="15" y2="17" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
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
  // macOS — laptop silhouette
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none">
      <rect x="3" y="5" width="18" height="12" rx="1.5" stroke="#fff" strokeWidth="2"/>
      <line x1="2" y1="19" x2="22" y2="19" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
}

function StepBadge({ n, color }: { n: number; color: string }) {
  return (
    <div className={`w-9 h-9 rounded-full shrink-0 flex items-center justify-center text-white text-[15px] font-bold ${color}`}>
      {n}
    </div>
  )
}

function ExternalIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function Instructions() {
  const nav = useNavigate()
  const t   = useT()
  const [open, setOpen] = useState<Platform['id'] | null>('ios')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  return (
    <div className="page" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 96px)' }}>

      {/* Hero — 3 steps overview */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-4">
        <div className="text-[17px] font-bold text-[var(--tg-theme-text-color)] mb-3">
          {t('instr_hero_title')}
        </div>
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <StepBadge n={1} color="bg-primary" />
            <div className="flex-1 min-w-0">
              <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)]">{t('instr_step1')}</div>
              <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-px">{t('instr_step1_sub')}</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StepBadge n={2} color="bg-purple" />
            <div className="flex-1 min-w-0">
              <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)]">{t('instr_step2')}</div>
              <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-px">{t('instr_step2_sub')}</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StepBadge n={3} color="bg-success" />
            <div className="flex-1 min-w-0">
              <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)]">{t('instr_step3')}</div>
              <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-px">{t('instr_step3_sub')}</div>
            </div>
          </div>
        </div>
      </div>

      {/* What is Subscription URL */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-3">
        <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)] mb-1.5">
          🔑 {t('instr_suburl_what')}
        </div>
        <div className="text-[13px] text-[var(--tg-theme-text-color)] leading-relaxed mb-3">
          {t('instr_suburl_desc')}
        </div>
        <div className="text-[12px] font-semibold text-[var(--tg-theme-hint-color)] uppercase tracking-wide mb-1">
          {t('instr_suburl_where')}
        </div>
        <div className="text-[13px] text-[var(--tg-theme-text-color)] leading-snug">
          {t('instr_suburl_where_desc')}
        </div>
      </div>

      {/* Per-platform accordion */}
      <span className="section-title pt-1">{t('instr_platforms')}</span>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        {PLATFORMS.map((p, i) => {
          const isOpen = open === p.id
          return (
            <div key={p.id}>
              <button
                onClick={() => { setOpen(isOpen ? null : p.id); WebApp.HapticFeedback.selectionChanged() }}
                className={`w-full border-none bg-transparent py-[13px] px-4 cursor-pointer flex items-center gap-[14px] ${(isOpen || i < PLATFORMS.length - 1) ? 'border-b border-solid border-[var(--card-border)]' : ''}`}
              >
                <div className={`w-10 h-10 rounded-[10px] shrink-0 ${p.color} flex items-center justify-center`}>
                  <PlatformIcon id={p.id} />
                </div>
                <span className="flex-1 text-[15px] font-semibold text-[var(--tg-theme-text-color)] text-left">
                  {t(p.titleKey)}
                </span>
                <svg width="7" height="12" viewBox="0 0 7 12" fill="none"
                     className={`shrink-0 transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}>
                  <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              {isOpen && (
                <div className={`py-3 px-4 pl-[70px] ${i < PLATFORMS.length - 1 ? 'border-b border-solid border-[var(--card-border)]' : ''}`}>
                  <ol className="m-0 pl-4 leading-[1.9]">
                    {p.stepKeys.map((stepKey, si) => (
                      <li key={si} className="text-[13px] text-[var(--tg-theme-text-color)] mb-0.5">
                        {t(stepKey as Parameters<typeof t>[0])}
                      </li>
                    ))}
                  </ol>
                  <a href={p.link.url} target="_blank" rel="noreferrer"
                     className="inline-flex items-center gap-[5px] mt-2.5 text-[13px] text-[var(--tg-theme-link-color,#2481cc)] no-underline">
                    <ExternalIcon />
                    {p.link.label}
                  </a>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Footer notes */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-3 mt-1">
        <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color)] mb-1">
          💡 {t('instr_alt_title')}
        </div>
        <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-relaxed mb-3">
          {t('instr_alt_desc')}
        </div>
        <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color)] mb-1">
          📺 {t('instr_tv_title')}
        </div>
        <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-relaxed">
          {t('instr_tv_desc')}
        </div>
      </div>
    </div>
  )
}
