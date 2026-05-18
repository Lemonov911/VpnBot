import WebApp from '@twa-dev/sdk'
import { useNavigate } from 'react-router-dom'

const APP_LINKS = [
  { label: '🍎 Happ — iOS',     url: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215' },
  { label: '🤖 Happ — Android', url: 'https://play.google.com/store/apps/details?id=com.happproxy' },
  { label: '🛡 AmneziaWG — iOS', url: 'https://apps.apple.com/app/amneziawg/id6478942365' },
  { label: '🤖 AmneziaWG — Android', url: 'https://play.google.com/store/apps/details?id=org.amnezia.awg' },
]

export default function TrialSuccessSheet({ onClose, days }: { onClose: () => void; days?: number }) {
  const nav = useNavigate()

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-[100] bg-black/60" />
      <div className="fixed inset-x-0 bottom-0 z-[101] bg-[var(--tg-theme-bg-color,#1c1c1e)] rounded-t-[20px] p-5 pb-[calc(env(safe-area-inset-bottom)+20px)] shadow-[0_-4px_30px_rgba(0,0,0,0.25)]">

        <div className="w-9 h-1 rounded-sm bg-gray-500/30 -mt-2 mx-auto mb-4" />

        <div className="flex items-center gap-2.5 mb-4">
          <span className="text-[26px]">🎁</span>
          <div>
            <div className="font-bold text-[17px] text-[var(--tg-theme-text-color,#fff)]">
              Триал активирован!
            </div>
            <div className="text-[12px] text-[var(--tg-theme-hint-color,#8e8e93)]">
              {days ?? 3} дня бесплатно — AmneziaWG + VLESS
            </div>
          </div>
        </div>

        {/* Split */}
        <div className="grid grid-cols-2 gap-2.5 mb-3">

          {/* Left — скачать приложения */}
          <div className="bg-[var(--tg-theme-section-bg-color,#2c2c2e)] rounded-[14px] p-3 flex flex-col gap-2">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--tg-theme-hint-color,#8e8e93)] mb-0.5">
              📲 Установить
            </div>
            {APP_LINKS.map(app => (
              <button
                key={app.url}
                onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); WebApp.openLink(app.url) }}
                className="w-full py-2 px-2.5 rounded-[8px] text-[11.5px] font-medium text-left cursor-pointer"
                style={{
                  background: 'var(--tg-theme-bg-color, #1c1c1e)',
                  color: 'var(--tg-theme-text-color, #fff)',
                  border: '1px solid var(--card-border, rgba(255,255,255,0.08))',
                }}
              >
                {app.label}
              </button>
            ))}
          </div>

          {/* Right — конфиги */}
          <button
            onClick={() => { WebApp.HapticFeedback.impactOccurred('medium'); onClose(); nav('/configs') }}
            className="rounded-[14px] flex flex-col items-center justify-center gap-2 cursor-pointer border-none"
            style={{ background: 'var(--color-primary, #2481cc)' }}
          >
            <span className="text-[38px]">📁</span>
            <div className="text-[14px] font-bold text-white leading-tight">Мои конфиги</div>
            <div className="text-[11px] text-white/75 leading-snug px-2 text-center">
              Скачать AWG‑файл и VLESS‑ссылку
            </div>
          </button>

        </div>

        <button
          onClick={onClose}
          className="w-full py-2 text-[12px] cursor-pointer bg-transparent border-none"
          style={{ color: 'var(--tg-theme-hint-color, #8e8e93)' }}
        >
          Разберусь позже
        </button>
      </div>
    </>
  )
}
