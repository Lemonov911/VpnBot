import WebApp from '@twa-dev/sdk'
import { useT } from '../i18n'

/**
 * Полноэкранная модалка подтверждения отмены автопродления.
 * Замена нативного window.confirm — более крупная, с деталями: до какой
 * даты ещё работает, что произойдёт после, кнопки развёрнутые.
 *
 * Цель: снизить случайные отмены + дать юзеру передумать (саму
 * confirm-кнопку показываем красной + secondary «Назад» более крупной
 * чтобы случайно не нажать destructive).
 */
export default function CancelRenewalModal({
  expiresAt,
  loading = false,
  onConfirm,
  onClose,
}: {
  expiresAt: string  // ISO date
  loading?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  const t = useT()

  let dateStr = ''
  try {
    dateStr = new Date(expiresAt).toLocaleDateString('ru-RU', {
      day: '2-digit', month: 'long', year: 'numeric',
    })
  } catch { dateStr = expiresAt.slice(0, 10) }

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-[100] bg-black/60" />
      <div className="fixed inset-x-0 bottom-0 z-[101] bg-[var(--tg-theme-bg-color,#fff)] rounded-t-[20px] p-5 pb-[calc(env(safe-area-inset-bottom)+24px)] shadow-[0_-4px_30px_rgba(0,0,0,0.18)]">
        <div className="w-9 h-1 rounded-sm bg-gray-500/30 -mt-2 mx-auto mb-[18px]" />

        <div className="flex items-start gap-3 mb-4">
          <span className="text-[28px] shrink-0">⚠️</span>
          <div className="flex-1 min-w-0">
            <div className="font-bold text-lg text-[var(--tg-theme-text-color,#000)] leading-tight">
              {t('cancel_renewal_title' as never)}
            </div>
            <div className="text-[12px] text-[var(--tg-theme-hint-color)] mt-1 leading-snug">
              {t('cancel_renewal_subtitle' as never)}
            </div>
          </div>
        </div>

        {/* Детали — до какой даты остаётся доступ */}
        <div className="bg-[var(--tg-theme-section-bg-color,#f1f1f1)] border border-[var(--card-border)] rounded-[12px] p-3 mb-5">
          <div className="text-[12px] text-[var(--tg-theme-hint-color)] mb-1">
            {t('cancel_renewal_keeps_label' as never)}
          </div>
          <div className="text-[16px] font-bold text-[var(--tg-theme-text-color,#000)]">
            {dateStr}
          </div>
          <div className="text-[11px] text-[var(--tg-theme-hint-color)] mt-1.5 leading-snug">
            {t('cancel_renewal_keeps_hint' as never)}
          </div>
        </div>

        {/* Destructive — confirm. Не делаю primary `btn` чтоб не выглядел как
            «продолжить» — даю red border ясно сигнализирующий destructive */}
        <button
          onClick={() => { WebApp.HapticFeedback.impactOccurred('medium'); onConfirm() }}
          disabled={loading}
          className="w-full py-3.5 rounded-[12px] border border-[var(--tg-theme-destructive-text-color,#ff3b30)] bg-transparent text-[var(--tg-theme-destructive-text-color,#ff3b30)] text-[15px] font-semibold cursor-pointer disabled:opacity-50"
        >
          {loading ? '…' : t('cancel_renewal_confirm_btn' as never)}
        </button>
        {/* Primary — keep subscription. Жирнее destructive чтоб юзер с большей
            вероятностью отказался от отмены (typical SaaS retention pattern) */}
        <button
          onClick={onClose}
          disabled={loading}
          className="btn !w-full !text-base !py-3.5 mt-2 disabled:opacity-50"
        >
          {t('cancel_renewal_keep_btn' as never)}
        </button>
      </div>
    </>
  )
}
