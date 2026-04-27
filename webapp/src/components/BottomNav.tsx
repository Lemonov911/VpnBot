import { useLocation, useNavigate } from 'react-router-dom'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

const TABS = [
  { path: '/',        key: 'nav_home'    as const, icon: HomeIcon    },
  { path: '/vpn',     key: 'nav_vpn'     as const, icon: ShieldIcon  },
  { path: '/esim',    key: 'nav_esim'    as const, icon: SimIcon     },
  { path: '/support', key: 'nav_support' as const, icon: HelpIcon    },
  { path: '/referral',key: 'nav_ref'     as const, icon: FriendsIcon },
]

function iconColor(active: boolean, dark: boolean) {
  if (dark) return active ? '#fff' : 'rgba(255,255,255,0.50)'
  return active ? '#1c1c1e' : 'rgba(0,0,0,0.35)'
}

function iconFill(active: boolean, dark: boolean) {
  if (!active) return 'none'
  return dark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.07)'
}

function HomeIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M3 12L12 3l9 9" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M5 10v11h14V10" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        fill={iconFill(active, dark)}/>
      <path d="M9 21V13h6v8" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function ShieldIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"
        stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        fill={iconFill(active, dark)}/>
      <path d="M9 12l2 2 4-4" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function SimIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <rect x="5" y="2" width="14" height="20" rx="2" stroke={c} strokeWidth="2"
        fill={iconFill(active, dark)}/>
      <path d="M9 8h6M9 12h6M9 16h4" stroke={c} strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

function HelpIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
        stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        fill={iconFill(active, dark)}/>
      <path d="M12 8v1m0 4h.01" stroke={c} strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
}

function FriendsIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <circle cx="9" cy="7" r="3.5" stroke={c} strokeWidth="2"
        fill={iconFill(active, dark)}/>
      <path d="M2 20c0-3.314 3.134-6 7-6s7 2.686 7 6" stroke={c} strokeWidth="2" strokeLinecap="round"/>
      <path d="M19 11c1.657 0 3 1.343 3 3" stroke={c} strokeWidth="1.8" strokeLinecap="round" opacity="0.7"/>
      <circle cx="17" cy="7.5" r="2.5" stroke={c} strokeWidth="1.8" fill="none" opacity="0.7"/>
    </svg>
  )
}

export default function BottomNav() {
  const location = useLocation()
  const nav      = useNavigate()
  const t        = useT()
  const dark     = WebApp.colorScheme === 'dark'

  const active = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)

  return (
    <div className="fixed bottom-0 left-0 right-0 z-[100] pb-[env(safe-area-inset-bottom)] bg-transparent">
      {/* Glass pill */}
      <div className="mx-3 mb-2.5 rounded-[28px] bg-white/[0.72] dark:bg-[rgba(40,40,46,0.72)] backdrop-blur-[40px] saturate-[180%] border-[0.5px] border-black/[0.08] dark:border-white/[0.14] shadow-[inset_0_1px_0_rgba(255,255,255,0.90),_inset_0_-1px_0_rgba(0,0,0,0.04),_0_8px_32px_rgba(0,0,0,0.10),_0_2px_8px_rgba(0,0,0,0.06)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.10),_inset_0_-1px_0_rgba(0,0,0,0.12),_0_8px_32px_rgba(0,0,0,0.35),_0_2px_8px_rgba(0,0,0,0.18)] overflow-hidden">
        {/* Top sheen */}
        <div className="absolute top-0 left-0 right-0 h-1/2 bg-gradient-to-b from-white/55 to-transparent dark:from-white/[0.07] rounded-t-[28px] pointer-events-none" />

        <div className="flex h-[62px] relative">
          {TABS.map(({ path, key, icon: Icon }) => {
            const isActive = active(path)
            return (
              <button
                key={path}
                onClick={() => {
                  if (!isActive) {
                    WebApp.HapticFeedback.selectionChanged()
                    nav(path)
                  }
                }}
                className="flex-1 border-none bg-transparent flex flex-col items-center justify-center gap-1 cursor-pointer py-1.5 relative"
              >
                {/* Active icon bubble */}
                <div className={`w-[42px] h-[30px] rounded-[10px] flex items-center justify-center transition-all duration-200 ${isActive ? 'bg-black/[0.07] dark:bg-white/[0.14] border-[0.5px] border-black/[0.12] dark:border-white/[0.28] shadow-[inset_0_1px_0_rgba(255,255,255,0.8),_0_2px_6px_rgba(0,0,0,0.06)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.2),_0_2px_6px_rgba(0,0,0,0.12)]' : 'bg-transparent border-[0.5px] border-transparent shadow-none'}`}>
                  <Icon active={isActive} dark={dark} />
                </div>

                <span className={`text-[10px] leading-none transition-colors duration-200 ${isActive ? 'font-semibold text-[#1c1c1e] dark:text-white' : 'font-normal text-black/35 dark:text-white/45'}`}>
                  {t(key)}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}