import { Link } from 'react-router-dom'
import { useT, type TKey } from '../i18n'

/**
 * Subtle footer с trust-signal линками: статус-страница + privacy.
 * Trust-audit 2026-05-25 (persona «Игорь — paranoid sysadmin»): главные
 * trust-signals (public status, privacy policy) физически в Mini App
 * существуют (/status, /privacy.html), но **нигде не линкуются** —
 * tech-savvy юзер их не находит и оценивает сервис как anonymous.
 * Этот footer кладёт обе ссылки в одно место.
 *
 * Размещён на главных touch-points (Home + Support) чтобы не плодить
 * визуальный шум, но быть в reach при сомнении.
 *
 * Не путать с `BottomNav` — он tab-нав (Home/VPN/Configs/etc.). Этот
 * footer — мета-навигация (status / legal / support contact).
 */
export function AppFooter() {
  const t = useT()
  return (
    <footer className="mt-6 mb-4 px-4 pt-4 border-t border-[var(--card-border)] text-[11px] text-[var(--tg-theme-hint-color)] flex flex-wrap gap-x-4 gap-y-1 justify-center">
      <Link
        to="/status"
        className="hover:underline text-[var(--tg-theme-link-color,var(--tg-theme-hint-color))]"
      >
        {t('footer_status' as TKey)}
      </Link>
      {/* /privacy.html лежит в webapp/public/, не SPA-route — используем <a> с full reload */}
      <a
        href={`${import.meta.env.BASE_URL}privacy.html`}
        className="hover:underline text-[var(--tg-theme-link-color,var(--tg-theme-hint-color))]"
      >
        {t('footer_privacy' as TKey)}
      </a>
      {/* Оферта — публичный договор, требуется для платёжных систем (CryptoBot/Stars/Lava)
       * и для скептичной аудитории, которая ищет «что я подписываю». Тоже статика в public/. */}
      <a
        href={`${import.meta.env.BASE_URL}oferta.html`}
        className="hover:underline text-[var(--tg-theme-link-color,var(--tg-theme-hint-color))]"
      >
        {t('footer_oferta' as TKey)}
      </a>
      <a
        href="https://t.me/maxvpnesim_bot"
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline text-[var(--tg-theme-link-color,var(--tg-theme-hint-color))]"
      >
        {t('footer_contact' as TKey)}
      </a>
    </footer>
  )
}
