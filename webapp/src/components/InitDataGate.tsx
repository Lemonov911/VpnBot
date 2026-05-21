import type { ReactNode } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

/**
 * Gate-компонент: показывает «открой в Telegram» если WebApp.initData пустой
 * (страница открыта в обычном браузере, deep-link без Mini App контекста).
 * Используется на auth'ных страницах (VPN, Configs, Referral) чтобы не
 * показывать пустые UI при отсутствии HMAC-подписи.
 */
export function InitDataGate({ children }: { children: ReactNode }) {
  const t = useT()
  if (!WebApp.initData) {
    return (
      <div className="page pt-2">
        <div className="rounded-[20px] p-6 bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] text-center">
          <div className="text-4xl mb-3">🔒</div>
          <div className="text-base font-bold text-[var(--tg-theme-text-color)] mb-2">
            {t('plans_gated_title')}
          </div>
          <div className="text-[13px] text-[var(--tg-theme-hint-color)] leading-snug mb-4">
            {t('plans_gated_sub')}
          </div>
          <a
            href={`https://t.me/${import.meta.env.VITE_BOT_USERNAME ?? 'maxvpnesim_bot'}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block px-5 py-2.5 rounded-[12px] bg-gradient-to-br from-primary to-[#5856d6] text-white text-sm font-bold no-underline"
          >
            {t('plans_gated_btn')}
          </a>
        </div>
      </div>
    )
  }
  return <>{children}</>
}
