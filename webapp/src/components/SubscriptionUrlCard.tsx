import { useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT, type TKey } from '../i18n'

/**
 * Карточка с Subscription URL: deep-link в Happ через `happ://add/<base64>`,
 * fallback — копирование URL.  Подписка — главный артефакт VLESS-юзера:
 * один URL, все локации, авто-обновление каждые 12 ч.
 *
 * Используется на VPN-странице (под основной sub-карточкой) и на
 * странице «Мои конфиги» (вместо per-slot VLESS UI).
 */
export function SubscriptionUrlCard({ subUrl }: { subUrl: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const happDeepLink = `happ://add/${btoa(subUrl)}`

  const handleHapp = () => {
    WebApp.HapticFeedback.impactOccurred('medium')
    WebApp.openLink(happDeepLink, { try_instant_view: false })
  }
  const handleCopy = async () => {
    WebApp.HapticFeedback.impactOccurred('light')
    try {
      await navigator.clipboard.writeText(subUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      prompt('Subscription URL:', subUrl)
    }
  }

  return (
    <div className="fade-in-1 fade-in bg-[var(--tg-theme-section-bg-color,#f1f1f1)] rounded-2xl py-4 px-[18px] border border-[var(--card-border)]">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-8 h-8 rounded-[10px] bg-purple flex items-center justify-center shrink-0">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path d="M10 14a3.5 3.5 0 0 0 4.95 0L19 10a3.5 3.5 0 0 0-4.95-4.95L13 6"
                  stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
            <path d="M14 10a3.5 3.5 0 0 0-4.95 0L5 14a3.5 3.5 0 0 0 4.95 4.95L11 18"
                  stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </div>
        <div className="font-semibold text-[15px] text-[var(--tg-theme-text-color,#000)]">
          {t('vpn_sub_url_title' as TKey)}
        </div>
      </div>
      <p className="text-[12px] text-[var(--tg-theme-hint-color,#707579)] mb-3 leading-[1.4]">
        {t('vpn_sub_url_desc' as TKey)}
      </p>
      <div className="text-[10px] font-mono bg-[var(--tg-theme-bg-color,#fff)] border border-[var(--card-border)] rounded-lg px-3 py-2 mb-3 truncate text-[var(--tg-theme-hint-color,#707579)]">
        {subUrl}
      </div>
      <div className="flex gap-2">
        <button
          onClick={handleHapp}
          className="flex-1 py-2.5 rounded-[10px] border-none bg-purple text-white text-sm font-semibold cursor-pointer"
        >
          {t('vpn_sub_url_open_happ' as TKey)}
        </button>
        <button
          onClick={handleCopy}
          className="py-2.5 px-4 rounded-[10px] border border-[var(--card-border)] bg-transparent text-[var(--tg-theme-text-color,#000)] text-sm font-semibold cursor-pointer"
        >
          {copied ? t('vpn_sub_url_copied' as TKey) : t('vpn_sub_url_copy' as TKey)}
        </button>
      </div>
    </div>
  )
}
