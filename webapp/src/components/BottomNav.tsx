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

function HomeIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M3 12L12 3l9 9" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M5 10v11h14V10" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        fill={active ? 'rgba(255,255,255,0.15)' : 'none'}/>
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
        fill={active ? 'rgba(255,255,255,0.15)' : 'none'}/>
      <path d="M9 12l2 2 4-4" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function SimIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <rect x="5" y="2" width="14" height="20" rx="2" stroke={c} strokeWidth="2"
        fill={active ? 'rgba(255,255,255,0.15)' : 'none'}/>
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
        fill={active ? 'rgba(255,255,255,0.15)' : 'none'}/>
      <path d="M12 8v1m0 4h.01" stroke={c} strokeWidth="2" strokeLinecap="round"/>
    </svg>
  )
}

function FriendsIcon({ active, dark }: { active: boolean; dark: boolean }) {
  const c = iconColor(active, dark)
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <circle cx="9" cy="7" r="3.5" stroke={c} strokeWidth="2"
        fill={active ? 'rgba(255,255,255,0.15)' : 'none'}/>
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

  // Pill colors per theme
  const pillBg     = dark ? 'rgba(40,40,46,0.72)'      : 'rgba(255,255,255,0.72)'
  const pillBorder = dark ? 'rgba(255,255,255,0.14)'   : 'rgba(0,0,0,0.08)'
  const sheenFrom  = dark ? 'rgba(255,255,255,0.07)'   : 'rgba(255,255,255,0.55)'
  const shadow     = dark
    ? 'inset 0 1px 0 rgba(255,255,255,0.10), inset 0 -1px 0 rgba(0,0,0,0.12), 0 8px 32px rgba(0,0,0,0.35), 0 2px 8px rgba(0,0,0,0.18)'
    : 'inset 0 1px 0 rgba(255,255,255,0.90), inset 0 -1px 0 rgba(0,0,0,0.04), 0 8px 32px rgba(0,0,0,0.10), 0 2px 8px rgba(0,0,0,0.06)'

  // Active bubble per theme
  const bubbleBg     = dark ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.07)'
  const bubbleBorder = dark ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.12)'
  const bubbleShadow = dark
    ? 'inset 0 1px 0 rgba(255,255,255,0.2), 0 2px 6px rgba(0,0,0,0.12)'
    : 'inset 0 1px 0 rgba(255,255,255,0.8), 0 2px 6px rgba(0,0,0,0.06)'

  // Label colors per theme
  const labelActive   = dark ? '#fff'                  : '#1c1c1e'
  const labelInactive = dark ? 'rgba(255,255,255,0.45)': 'rgba(0,0,0,0.35)'

  return (
    <div style={{
      position: 'fixed',
      bottom: 0, left: 0, right: 0,
      zIndex: 100,
      paddingBottom: 'env(safe-area-inset-bottom)',
      background: 'transparent',
    }}>
      {/* Glass pill */}
      <div style={{
        margin: '0 12px 10px',
        borderRadius: 28,
        background: pillBg,
        backdropFilter: 'blur(40px) saturate(180%)',
        WebkitBackdropFilter: 'blur(40px) saturate(180%)',
        border: `0.5px solid ${pillBorder}`,
        boxShadow: shadow,
        overflow: 'hidden',
      }}>
        {/* Top sheen */}
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: '50%',
          background: `linear-gradient(180deg, ${sheenFrom} 0%, transparent 100%)`,
          borderRadius: '28px 28px 0 0',
          pointerEvents: 'none',
        }} />

        <div style={{ display: 'flex', height: 62, position: 'relative' }}>
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
                style={{
                  flex: 1, border: 'none', background: 'transparent',
                  display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center',
                  gap: 4, cursor: 'pointer', padding: '6px 0',
                  position: 'relative',
                }}
              >
                {/* Active icon bubble */}
                <div style={{
                  width: 42, height: 30, borderRadius: 10,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: isActive ? bubbleBg    : 'transparent',
                  border:     isActive ? `0.5px solid ${bubbleBorder}` : '0.5px solid transparent',
                  boxShadow:  isActive ? bubbleShadow : 'none',
                  transition: 'all 0.2s ease',
                }}>
                  <Icon active={isActive} dark={dark} />
                </div>

                <span style={{
                  fontSize: 10, fontWeight: isActive ? 600 : 400,
                  color: isActive ? labelActive : labelInactive,
                  lineHeight: 1,
                  transition: 'color 0.2s ease',
                }}>
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
