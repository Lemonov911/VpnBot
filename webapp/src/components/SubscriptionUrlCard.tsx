import { useEffect, useRef, useState } from 'react'
import WebApp from '@twa-dev/sdk'
import { useT, type TKey } from '../i18n'
import { copyText } from '../utils/clipboard'

/**
 * Главная карточка VLESS: «Ссылка для подключения» — после copy показывает
 * inline-инструкцию что делать дальше (открыть Happ → + → Из подписки → вставить),
 * иначе copy-альтоун — загадка для не-tech юзера.
 */
export function SubscriptionUrlCard({ subUrl }: { subUrl: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)
  const manualTextRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (!manualOpen) return
    const id = window.setTimeout(() => {
      manualTextRef.current?.focus()
      manualTextRef.current?.select()
    }, 50)
    return () => window.clearTimeout(id)
  }, [manualOpen])

  const handleCopy = async () => {
    WebApp.HapticFeedback.impactOccurred('light')
    const ok = await copyText(subUrl, () => {
      setCopied(true)
      // 4 сек вместо 1.5 — юзеру нужно успеть прочитать инструкцию
      setTimeout(() => setCopied(false), 4000)
    })
    if (!ok) {
      WebApp.HapticFeedback.notificationOccurred('warning')
      setManualOpen(true)
    }
  }

  return (
    <>
      <div className="bg-[var(--tg-theme-section-bg-color)] border border-[var(--card-border)] rounded-2xl overflow-hidden">
        <div className="py-[13px] px-4 flex items-center gap-[14px]">
          <div className="w-10 h-10 rounded-xl shrink-0 flex items-center justify-center relative bg-purple">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M10 14a3.5 3.5 0 0 0 4.95 0L19 10a3.5 3.5 0 0 0-4.95-4.95L13 6"
                    stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
              <path d="M14 10a3.5 3.5 0 0 0-4.95 0L5 14a3.5 3.5 0 0 0 4.95 4.95L11 18"
                    stroke="#fff" strokeWidth="2" strokeLinecap="round"/>
            </svg>
            <span className="absolute -bottom-[3px] -right-[3px] w-3 h-3 rounded-full bg-success border-2 border-[var(--tg-theme-bg-color,#fff)]" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="text-[15px] font-semibold text-[var(--tg-theme-text-color)] leading-tight">
              {t('vpn_sub_url_title' as TKey)}
            </div>
            {/* line-clamp-2: разрешаем 2 строки чтобы не обрезалось «Теперь
                вставь в Happ: "+" → "Из подписки"» (длинная подсказка).
                truncate в одну строку было слишком узко на 360px-экранах. */}
            <div className="text-xs text-[var(--tg-theme-hint-color)] mt-0.5 leading-[1.35] line-clamp-2">
              {copied
                ? t('vpn_sub_url_paste_hint' as TKey)
                : t('vpn_sub_url_hint' as TKey)}
            </div>
          </div>

          <button
            onClick={handleCopy}
            className="bg-purple text-white text-[13px] font-semibold cursor-pointer rounded-[10px] py-[7px] px-[14px] border-none flex items-center gap-[5px] shrink-0"
          >
            {copied ? (
              <>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <path d="M4 12l5 5 11-13" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                {t('vpn_sub_url_copied' as TKey)}
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2"/>
                  <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                </svg>
                {t('vpn_sub_url_copy' as TKey)}
              </>
            )}
          </button>
        </div>
      </div>

      {manualOpen && (
        <>
          <div data-testid="sub-url-manual-backdrop" onClick={() => setManualOpen(false)} className="fixed inset-0 bg-black/55 z-[200]" />
          <div className="fixed bottom-0 left-0 right-0 z-[201] rounded-t-[20px] bg-[var(--tg-theme-secondary-bg-color,#f7f7f8)] px-4 pt-5 pb-8">
            <div className="w-9 h-1 rounded-sm bg-[var(--tg-theme-hint-color,#888)] opacity-40 mx-auto mb-5" />
            <div className="text-[17px] font-semibold text-[var(--tg-theme-text-color)]">
              {t('vpn_sub_url_manual_title' as TKey)}
            </div>
            <p className="text-[13px] leading-[1.4] text-[var(--tg-theme-hint-color)] mt-1.5 mb-3">
              {t('vpn_sub_url_manual_hint' as TKey)}
            </p>
            <textarea
              ref={manualTextRef}
              readOnly
              value={subUrl}
              onFocus={(e) => e.currentTarget.select()}
              className="w-full min-h-[88px] resize-none rounded-xl border border-[var(--card-border)] bg-[var(--tg-theme-bg-color,#fff)] p-3 text-[13px] leading-[1.35] text-[var(--tg-theme-text-color)] outline-none"
            />
            <div className="flex gap-2 mt-3">
              <button
                onClick={() => {
                  manualTextRef.current?.focus()
                  manualTextRef.current?.select()
                }}
                className="flex-1 rounded-[12px] border-none bg-purple py-3 text-[14px] font-semibold text-white"
              >
                {t('vpn_sub_url_select' as TKey)}
              </button>
              <button
                onClick={() => setManualOpen(false)}
                className="flex-1 rounded-[12px] border border-[var(--card-border)] bg-[var(--tg-theme-section-bg-color)] py-3 text-[14px] font-semibold text-[var(--tg-theme-text-color)]"
              >
                {t('configs_close' as TKey)}
              </button>
            </div>
          </div>
        </>
      )}
    </>
  )
}
