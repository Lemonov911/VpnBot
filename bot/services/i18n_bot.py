"""Bot-side i18n for Telegram messages.

Routing: looks up users.lang (set from Telegram language_code on /start);
defaults to 'ru' if missing or unknown.

Keys mirror webapp/src/i18n.tsx where overlap exists. Bot-specific keys
have a bot_* prefix.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

T: dict[str, dict[str, str]] = {
    "ru": {
        "bot_trial_success_header":     "🎁 <b>Trial на {days} {day_word} активирован</b>",
        "bot_trial_success_until":      "📅 До: <b>{until}</b>",
        "bot_trial_success_speed":      "🚀 Скорость: 60 Mbps",
        "bot_trial_success_after":      "💎 После trial — выбери постоянный тариф ↓",
        "bot_grace_notice":             "⏳ <b>Подписка истекла — VPN работает на 256 кбит/с 14 дней</b>\n\nПродли в Mini App, чтобы вернуть полную скорость.",
        "bot_expiry_notice":            "❌ <b>Подписка закрыта</b>\n\nVPN-конфиги отключены. Открой Mini App чтобы оформить новую подписку ↓",
        "bot_trial_expiry_notice":      "⌛ <b>Trial закончился</b>\n\nЧтобы продолжить — выбери постоянный тариф ↓",
        "bot_renewal_reminder_today":   "🔁 <b>Сегодня спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_tomorrow":"🔁 <b>Завтра спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_in":      "🔁 <b>Через {n} {day_word} спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_body":    "\nVPN {plan} → продлится автоматически на 30 дней.\nДата списания: <b>{date}</b>\n\nХочешь отменить? Открой Mini App → VPN → Отменить автопродление.",
        "bot_purchase_success_title":   "✅ <b>VPN {plan} активирован!</b>",
        "bot_purchase_success_until":   "📅 Действует до: <b>{until}</b>",
        "bot_purchase_success_sub_url": "🔗 <b>Subscription URL</b> (для Happ / Streisand / Amnezia VPN — импортируй один раз, обновляется автоматически):\n<code>{url}</code>",
        "bot_btn_my_configs":           "📁 Мои конфиги",
        "bot_btn_howto":                "📖 Инструкция",
        "bot_lava_renewed":             "🔁 <b>Подписка продлена автоматически</b>\n\nVPN {plan} активен до <b>{until}</b>.",
        "bot_lava_renewed_grace":       "\n⚡ Полная скорость восстановлена.",
        "bot_lava_charge_failed":       "⚠️ <b>Не удалось продлить подписку</b>\n\nLava не смогла списать оплату с карты. Lava попробует ещё раз через сутки. Если не получится — VPN перейдёт в режим 256 кбит/с на 14 дней.\n\nПроверь баланс карты или оплати вручную через меню.",
        "bot_payment_failed":           "⚠️ <b>Оплата не прошла</b>\n\nLava не смогла списать с карты. Возможные причины:\n• недостаточно средств\n• 3DS не пройден\n• карта заблокирована для онлайн-оплат\n\nПопробуй оплатить ещё раз или выбери другой способ.",
        "bot_ban_message":              "🚫 Доступ ограничен.\n\nЕсли считаешь это ошибкой — напиши на support@maxvpnesim.com",
    },
    "en": {
        "bot_trial_success_header":     "🎁 <b>Trial activated for {days} {day_word}</b>",
        "bot_trial_success_until":      "📅 Until: <b>{until}</b>",
        "bot_trial_success_speed":      "🚀 Speed: 60 Mbps",
        "bot_trial_success_after":      "💎 After trial — pick a permanent plan ↓",
        "bot_grace_notice":             "⏳ <b>Subscription expired — VPN throttled to 256 kbps for 14 days</b>\n\nRenew in Mini App to restore full speed.",
        "bot_expiry_notice":            "❌ <b>Subscription closed</b>\n\nVPN configs disabled. Open Mini App to pick a new plan ↓",
        "bot_trial_expiry_notice":      "⌛ <b>Trial ended</b>\n\nTo keep VPN — pick a permanent plan ↓",
        "bot_renewal_reminder_today":   "🔁 <b>Today {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_tomorrow":"🔁 <b>Tomorrow {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_in":      "🔁 <b>In {n} {day_word} {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_body":    "\nVPN {plan} → will renew automatically for 30 days.\nCharge date: <b>{date}</b>\n\nWant to cancel? Open Mini App → VPN → Cancel auto-renewal.",
        "bot_purchase_success_title":   "✅ <b>VPN {plan} activated!</b>",
        "bot_purchase_success_until":   "📅 Active until: <b>{until}</b>",
        "bot_purchase_success_sub_url": "🔗 <b>Subscription URL</b> (for Happ / Streisand / Amnezia VPN — import once, updates automatically):\n<code>{url}</code>",
        "bot_btn_my_configs":           "📁 My configs",
        "bot_btn_howto":                "📖 How to set up",
        "bot_lava_renewed":             "🔁 <b>Subscription renewed automatically</b>\n\nVPN {plan} active until <b>{until}</b>.",
        "bot_lava_renewed_grace":       "\n⚡ Full speed restored.",
        "bot_lava_charge_failed":       "⚠️ <b>Could not renew subscription</b>\n\nLava could not charge your card. Lava will retry in 24h. If it fails — VPN will throttle to 256 kbps for 14 days.\n\nCheck your card balance or pay manually via menu.",
        "bot_payment_failed":           "⚠️ <b>Payment failed</b>\n\nLava could not charge your card. Possible reasons:\n• insufficient funds\n• 3DS not passed\n• card blocked for online payments\n\nTry paying again or use another method.",
        "bot_ban_message":              "🚫 Access restricted.\n\nIf you think this is a mistake — write to support@maxvpnesim.com",
    },
}


def _resolve_lang(user_lang: str | None) -> str:
    """Telegram language_code → 'ru' | 'en'. Default ru."""
    if user_lang and user_lang.lower().startswith("en"):
        return "en"
    return "ru"


def t(user_lang: str | None, key: str, **kwargs: Any) -> str:
    """Lookup translation.

    Fallback chain: requested lang → ru → key itself.
    На неизвестный {placeholder} в шаблоне — лог + возврат raw template (лучше
    показать сырое чем 500 во время /start).
    """
    lang = _resolve_lang(user_lang)
    bundle = T.get(lang, T["ru"])
    template = bundle.get(key) or T["ru"].get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError) as e:
            logger.warning("bot_i18n: format failed for key=%s lang=%s: %s", key, lang, e)
            return template
    return template


def day_word(lang: str | None, n: int) -> str:
    """Слово 'день/дня/дней' (ru, plural) или 'day/days' (en)."""
    resolved = _resolve_lang(lang)
    if resolved == "en":
        return "day" if n == 1 else "days"
    # RU plural
    n100 = n % 100
    n10 = n % 10
    if 11 <= n100 <= 14:
        return "дней"
    if n10 == 1:
        return "день"
    if 2 <= n10 <= 4:
        return "дня"
    return "дней"
