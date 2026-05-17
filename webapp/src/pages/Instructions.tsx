import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { useT, type TKey } from '../i18n'

/* ── Apps ────────────────────────────────────────────────────────────────── */

interface AppLink { label: string; url: string }
interface AppDef {
  id:       'happ' | 'amnezia-vpn' | 'amnezia-wg'
  name:     string
  blurb:    TKey
  links:    Record<'ios' | 'android' | 'desktop', AppLink | null>
  steps:    TKey[]         // platform-agnostic, 4 steps
  color:    string         // bg-class
  iconKey:  'link' | 'shield' | 'bolt'
}

const APPS: AppDef[] = [
  {
    id: 'happ',
    name: 'Happ',
    blurb: 'instr_app_happ_blurb',
    color: 'bg-purple',
    iconKey: 'link',
    links: {
      ios:     { label: 'App Store',   url: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215' },
      android: { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=com.happproxy' },
      desktop: { label: 'happ.su',     url: 'https://happ.su' },
    },
    steps: ['instr_happ_s1', 'instr_happ_s2', 'instr_happ_s3', 'instr_happ_s4'],
  },
  {
    id: 'amnezia-vpn',
    name: 'Amnezia VPN',
    blurb: 'instr_app_amnezia_blurb',
    color: 'bg-cyan-500',
    iconKey: 'shield',
    links: {
      ios:     { label: 'App Store',   url: 'https://apps.apple.com/app/amneziavpn/id1600529900' },
      android: { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=org.amnezia.vpn' },
      desktop: { label: 'amnezia.org', url: 'https://amnezia.org/downloads' },
    },
    steps: ['instr_amnezia_s1', 'instr_amnezia_s2', 'instr_amnezia_s3', 'instr_amnezia_s4'],
  },
  {
    id: 'amnezia-wg',
    name: 'AmneziaWG',
    blurb: 'instr_app_awg_blurb',
    color: 'bg-emerald-500',
    iconKey: 'bolt',
    links: {
      ios:     { label: 'App Store',   url: 'https://apps.apple.com/app/amneziawg/id6478942365' },
      android: { label: 'Google Play', url: 'https://play.google.com/store/apps/details?id=org.amnezia.awg' },
      desktop: null,  // нет desktop-сборки
    },
    steps: ['instr_amnezia_s1', 'instr_amnezia_s2', 'instr_amnezia_s3', 'instr_amnezia_s4'],
  },
]

/* ── UI helpers ──────────────────────────────────────────────────────────── */

function AppIcon({ k }: { k: AppDef['iconKey'] }) {
  if (k === 'link') return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M10 14a3.5 3.5 0 0 0 4.95 0L19 10a3.5 3.5 0 0 0-4.95-4.95L13 6" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
      <path d="M14 10a3.5 3.5 0 0 0-4.95 0L5 14a3.5 3.5 0 0 0 4.95 4.95L11 18" stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
  if (k === 'shield') return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" stroke="#fff" strokeWidth="2" strokeLinejoin="round"/>
    </svg>
  )
  // bolt
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M13 2L3 14h7v8l10-12h-7V2z" stroke="#fff" strokeWidth="2" strokeLinejoin="round"/>
    </svg>
  )
}

function DownloadChip({ link, icon }: { link: AppLink; icon: '🍎' | '▶' | '🖥' }) {
  return (
    <button
      onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(link.url) }}
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[12px] font-medium bg-[var(--tg-theme-button-color,#2481cc)]/12 text-[var(--tg-theme-button-color,#2481cc)] border-none cursor-pointer"
    >
      <span>{icon}</span>{link.label}
    </button>
  )
}

function AppCard({ app, open, onToggle, t }: {
  app: AppDef
  open: boolean
  onToggle: () => void
  t: ReturnType<typeof useT>
}) {
  return (
    <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full border-none bg-transparent py-[14px] px-4 cursor-pointer flex items-center gap-[14px] text-left"
      >
        <div className={`w-10 h-10 rounded-[11px] shrink-0 ${app.color} flex items-center justify-center`}>
          <AppIcon k={app.iconKey} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)]">{app.name}</div>
          <div className="text-[11.5px] text-[var(--tg-theme-hint-color)] mt-px line-clamp-2 leading-[1.35]">
            {t(app.blurb)}
          </div>
        </div>
        <svg width="7" height="12" viewBox="0 0 7 12" fill="none"
             className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}>
          <path d="M1 1l5 5-5 5" stroke="rgba(128,128,128,0.4)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      {open && (
        <div className="border-t border-solid border-[var(--card-border)] px-4 py-3.5 space-y-3">
          <div>
            <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] uppercase tracking-wider mb-1.5">
              {t('instr_download_label' as TKey)}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {app.links.ios     && <DownloadChip link={app.links.ios}     icon="🍎" />}
              {app.links.android && <DownloadChip link={app.links.android} icon="▶" />}
              {app.links.desktop && <DownloadChip link={app.links.desktop} icon="🖥" />}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-semibold text-[var(--tg-theme-hint-color)] uppercase tracking-wider mb-1.5">
              {t('instr_steps_label' as TKey)}
            </div>
            <ol className="list-decimal pl-5 space-y-1 text-[13px] text-[var(--tg-theme-text-color)] leading-[1.5]">
              {app.steps.map((sk, i) => (
                <li key={i}>{t(sk)}</li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function Instructions() {
  const nav = useNavigate()
  const t   = useT()
  const [openId, setOpenId] = useState<AppDef['id'] | null>('happ')

  useEffect(() => {
    WebApp.BackButton.show()
    const goBack = () => nav('/vpn')
    WebApp.BackButton.onClick(goBack)
    return () => { WebApp.BackButton.hide(); WebApp.BackButton.offClick(goBack) }
  }, [nav])

  return (
    <div className="page">
      {/* Hero — flow для Happ (основной кейс 9/10).  AmneziaWG в accordion ниже. */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-4">
        <div className="flex items-baseline justify-between mb-3 gap-2 flex-wrap">
          <div className="text-[17px] font-bold text-[var(--tg-theme-text-color)]">
            {t('instr_hero_title')}
          </div>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-purple bg-purple/10 px-2 py-0.5 rounded">
            Happ
          </span>
        </div>
        <ol className="space-y-3 list-none p-0 m-0">
          {([
            ['1', 'bg-primary',  'instr_step1', 'instr_step1_sub'],
            ['2', 'bg-purple',   'instr_step2', 'instr_step2_sub'],
            ['3', 'bg-success',  'instr_step3', 'instr_step3_sub'],
          ] as const).map(([n, bg, title, sub]) => (
            <li key={n} className="flex items-start gap-3">
              <div className={`w-9 h-9 rounded-full shrink-0 flex items-center justify-center text-white text-[15px] font-bold ${bg} mt-px`}>{n}</div>
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)]">{t(title)}</div>
                <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-px">{t(sub)}</div>
                {/* Шаг 1 — кнопки скачать Happ прямо здесь, чтоб юзер не
                    скроллил до accordion'а ниже. На step 2/3 — text-only. */}
                {n === '1' && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    <DownloadChip link={APPS[0].links.ios!}     icon="🍎" />
                    <DownloadChip link={APPS[0].links.android!} icon="▶" />
                    <DownloadChip link={APPS[0].links.desktop!} icon="🖥" />
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-3 pt-2.5 border-t border-[var(--card-border)] text-[11.5px] text-[var(--tg-theme-hint-color)] leading-[1.4]">
          {t('instr_hero_amnezia_note' as TKey)}
        </div>
      </div>

      {/* Pick-your-app explainer */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-3 mt-2">
        <div className="text-[14px] font-semibold text-[var(--tg-theme-text-color)] mb-1.5">
          🧭 {t('instr_pick_title' as TKey)}
        </div>
        <div className="text-[12.5px] text-[var(--tg-theme-hint-color)] leading-[1.5]">
          {t('instr_pick_desc' as TKey)}
        </div>
      </div>

      {/* Apps — accordion */}
      <span className="section-title pt-1">{t('instr_apps_title' as TKey)}</span>
      <div className="flex flex-col gap-2">
        {APPS.map(app => (
          <AppCard
            key={app.id}
            app={app}
            open={openId === app.id}
            onToggle={() => { setOpenId(openId === app.id ? null : app.id); WebApp.HapticFeedback.selectionChanged() }}
            t={t}
          />
        ))}
      </div>

      {/* Smart TV / alt clients */}
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl px-4 py-3 mt-2">
        <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color)] mb-1">
          💡 {t('instr_alt_title')}
        </div>
        <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-[1.5] mb-3">
          {t('instr_alt_desc')}
        </div>
        <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color)] mb-1">
          📺 {t('instr_tv_title')}
        </div>
        <div className="text-[12px] text-[var(--tg-theme-hint-color)] leading-[1.5]">
          {t('instr_tv_desc')}
        </div>
      </div>
    </div>
  )
}
