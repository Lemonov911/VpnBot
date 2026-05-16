import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

/**
 * Полноэкранный overlay «Что дальше после оплаты» — показывается после
 * того как мы openLink-нули юзера в внешний браузер (Lava / Cryptomus /
 * CryptoBot). Stars не нужен — там нативный invoice с success-callback.
 *
 * Цель: убрать «я заплатил а где конфиги» провал на activation-шаге.
 * Содержимое: 3-4 шага что произойдёт + кнопка перейти в Мои конфиги.
 */
export default function PostPayOnboarding({
  onClose,
  onGoConfigs,
}: {
  onClose: () => void
  onGoConfigs: () => void
}) {
  const t = useT()

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-[100] bg-black/60" />
      <div className="fixed inset-x-0 bottom-0 z-[101] bg-[var(--tg-theme-bg-color,#fff)] rounded-t-[20px] p-5 pb-[calc(env(safe-area-inset-bottom)+24px)] shadow-[0_-4px_30px_rgba(0,0,0,0.18)]">
        <div className="w-9 h-1 rounded-sm bg-gray-500/30 -mt-2 mx-auto mb-[18px]" />

        <div className="flex items-center gap-2.5 mb-4">
          <span className="text-[24px]">🎉</span>
          <div>
            <div className="font-bold text-lg text-[var(--tg-theme-text-color,#000)]">
              {t('postpay_title' as never)}
            </div>
            <div className="text-[12px] text-[var(--tg-theme-hint-color)]">
              {t('postpay_subtitle' as never)}
            </div>
          </div>
        </div>

        {/* Steps как numbered timeline */}
        <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-[14px] overflow-hidden mb-5">
          {[
            { num: '1', emoji: '💳', title: t('postpay_step1_title' as never), sub: t('postpay_step1_sub' as never) },
            { num: '2', emoji: '⚡', title: t('postpay_step2_title' as never), sub: t('postpay_step2_sub' as never) },
            { num: '3', emoji: '📲', title: t('postpay_step3_title' as never), sub: t('postpay_step3_sub' as never) },
          ].map((s, i, arr) => (
            <div
              key={s.num}
              className={`py-3 px-4 flex items-start gap-3 ${i < arr.length - 1 ? 'border-b border-gray-500/10' : ''}`}
            >
              <div className="w-7 h-7 rounded-full shrink-0 bg-[var(--tg-theme-button-color,#2481cc)] text-[var(--tg-theme-button-text-color,#fff)] flex items-center justify-center text-[12px] font-bold">
                {s.num}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-semibold text-[var(--tg-theme-text-color,#000)] leading-tight">
                  {s.emoji} {s.title}
                </div>
                <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-0.5 leading-snug">
                  {s.sub}
                </div>
              </div>
            </div>
          ))}
        </div>

        <button
          className="btn !w-full !text-base !py-3.5"
          onClick={() => { WebApp.HapticFeedback.impactOccurred('light'); onGoConfigs() }}
        >
          {t('postpay_btn_configs' as never)}
        </button>
        <button
          onClick={onClose}
          className="mt-2 w-full py-2.5 text-[12px] text-[var(--tg-theme-link-color,#2481cc)] underline"
        >
          {t('postpay_btn_later' as never)}
        </button>

        {/* Reassurance — payment может ещё не дойти, не пугаемся */}
        <div className="mt-3 text-[10.5px] text-[var(--tg-theme-hint-color)] text-center leading-snug px-2">
          {t('postpay_reassurance' as never)}
        </div>
      </div>
    </>
  )
}
