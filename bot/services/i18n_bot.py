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
        "bot_grace_notice":             "⏳ <b>Подписка истекла — VPN работает на 512 кбит/с 14 дней</b>\n\nПродли в Mini App, чтобы вернуть полную скорость.",
        "bot_expiry_notice":            "❌ <b>Подписка закрыта</b>\n\nVPN-конфиги отключены. Открой Mini App чтобы оформить новую подписку ↓",
        "bot_trial_expiry_notice":      "⌛ <b>Trial закончился</b>\n\nЧтобы продолжить — выбери постоянный тариф ↓",
        "bot_renewal_reminder_today":   "🔁 <b>Сегодня спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_tomorrow":"🔁 <b>Завтра спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_in":      "🔁 <b>Через {n} {day_word} спишется {amount} ₽ с твоей карты</b>",
        "bot_renewal_reminder_body":    "\nVPN {plan} → продлится автоматически на 30 дней.\nДата списания: <b>{date}</b>\n\nХочешь отменить? Открой Mini App → VPN → Отменить автопродление.",
        "bot_purchase_success_title":   "✅ <b>VPN {plan} активирован!</b>",
        "bot_purchase_success_until":   "📅 Действует до: <b>{until}</b>",
        "bot_purchase_success_sub_url": "🔗 <b>Subscription URL</b> (для Happ / Streisand / Amnezia VPN — импортируй один раз, обновляется автоматически):\n<code>{url}</code>",
        "bot_purchase_success_awg_caption": "📁 <b>WireGuard конфиг #{n}</b>\nИмпортируй в AmneziaWG / WireGuard",
        "bot_purchase_success_partial": "⚙️ Слотов готово: {delivered}/{total} (остальные подтянутся)",
        "bot_upgrade_success_title":    "✅ <b>Тариф изменён на «{plan}»!</b>",
        "bot_upgrade_success_slots":    "🔌 Теперь у тебя: <b>{slots}</b>",
        "bot_upgrade_success_check":    "Открой <b>Мои конфиги</b> — новые пустые слоты уже там.",
        "bot_upgrade_success_paid":     "💎 Оплата: {amount} {asset}",
        "bot_precheckout_bad_payload":  "Некорректный payload — попробуй создать платёж заново.",
        "bot_precheckout_bad_amount":   "Неверная сумма платежа.",
        "bot_btn_my_configs":           "📁 Мои конфиги",
        "bot_btn_howto":                "📖 Инструкция",
        "bot_lava_renewed":             "🔁 <b>Подписка продлена автоматически</b>\n\nVPN {plan} активен до <b>{until}</b>.",
        "bot_lava_renewed_grace":       "\n⚡ Полная скорость восстановлена.",
        "bot_grace_renewed_title":      "✅ <b>Подписка продлена!</b>",
        "bot_grace_renewed_until":      "📅 Действует до: <b>{until}</b>",
        "bot_grace_renewed_speed":      "⚡ Полная скорость восстановлена — VPN снова работает без ограничений.",
        "bot_precheckout_banned":       "Доступ ограничен. Напиши в поддержку.",
        "bot_precheckout_unknown_plan": "Неизвестный тариф.",
        "bot_precheckout_active_sub":   "У тебя уже есть активная подписка. Для смены тарифа используй кнопку «Улучшить» в Mini App.",
        "bot_precheckout_active_sub_same": "У тебя уже активная подписка «{plan}». Открой Mini App.",
        "bot_precheckout_plan_unavailable": "Тариф больше недоступен. Открой меню заново.",
        "bot_provision_failed":         "❌ <b>VPN-конфиги не создались</b>\n\nОплата прошла, но сервера временно недоступны. Поддержка уже уведомлена — подключим вручную или вернём средства.",
        "bot_lava_autorenew_note":      "🔁 Автопродление включено — продляется автоматически каждый месяц. Отменить можно в разделе VPN.",
        "bot_referral_bonus_activated": "🎁 <b>Бонусные дни активированы!</b>\n\nДобавлено: <b>+{days} {day_word}</b>\nПодписка действует до: <b>{until}</b>",
        "bot_server_decom":             "🔄 <b>Локация VPN обновлена</b>\n\nОдин из VPN-серверов выведен из эксплуатации. Открой Happ → потяни вниз для обновления подписки. Остальные локации работают как обычно.",
        "bot_server_migration":         "🔄 <b>Ваш VPN перенесён</b>\n\nСервер заменён на {server}.\nСкачайте обновлённый конфиг в <a href=\"{url}\">приложении</a>.",
        "bot_lava_charge_failed":       "⚠️ <b>Не удалось продлить подписку</b>\n\nLava не смогла списать оплату с карты. Lava попробует ещё раз через сутки. Если не получится — VPN перейдёт в режим 512 кбит/с на 14 дней.\n\nПроверь баланс карты или оплати вручную через меню.",
        "bot_payment_failed":           "⚠️ <b>Оплата не прошла</b>\n\nLava не смогла списать с карты. Возможные причины:\n• недостаточно средств\n• 3DS не пройден\n• карта заблокирована для онлайн-оплат\n\nПопробуй оплатить ещё раз или выбери другой способ.",
        "bot_ban_message":              "🚫 Доступ ограничен.\n\nЕсли считаешь это ошибкой — напиши на support@maxvpnesim.com",
        # /start greetings + referral
        "bot_start_greeting_with_trial":
            "👋 Привет! Я помогу защитить соединение и сохранить приватность.\n\n"
            "🎁 <b>Первые {trial_days} {day_word} бесплатно</b> — без карты, без подписки. "
            "Просто нажми кнопку «Попробуй бесплатно» ниже, и через 30 секунд у тебя "
            "будет личный VPN.\n\n"
            "Дальше — тарифы от 200 ₽/мес.\n\n"
            '<a href="https://maxvpnesim.com/privacy.html">Политика конфиденциальности</a>',
        "bot_start_greeting_no_trial_shop":
            "👋 С возвращением! Открывай магазин VPN & eSIM кнопкой ниже.",
        "bot_start_greeting_no_trial":
            "👋 С возвращением. Тарифы и подписка — в приложении ниже.",
        "bot_start_greeting_fallback":
            "👋 Привет! Я помогу тебе получить доступ к интернету без ограничений.\n\n"
            "Выбери, что тебя интересует:",
        "bot_start_referral_late":
            "ℹ️ <b>Реферальная ссылка не применилась</b>\n\n"
            "Ты уже зарегистрирован в боте — реферальные ссылки работают только "
            "для новых юзеров. Расширенный 7-дневный триал предназначен только "
            "для тех, кто впервые открывает бота по ссылке.",
        "bot_btn_try_free":             "🎁 Попробуй бесплатно — {days} {day_word}",
        "bot_btn_open_app":             "🚀 Открыть приложение",
        "bot_btn_renew_subscription":   "💎 Продлить подписку",
        "bot_btn_choose_plan":          "🎯 Выбрать тариф",
        "bot_lava_err_self_buy":        "Lava не разрешает покупать у себя — введи другой email.",
        "bot_lava_err_email_rejected":  "Email отклонён платёжной системой — попробуй другой.",
        "bot_lava_err_payment_rejected":"Платёжная система отклонила запрос. Попробуй другой email или метод оплаты.",
        # Scheduler reminders
        "bot_trial_expiry_1d":
            "⏳ <b>Пробный период заканчивается через 24 часа</b>\n\n"
            "Понравилось? Выбери постоянный тариф — "
            "от 200 ₽/мес, та же скорость, без перерыва.\n\n"
            "Продли сейчас — VPN продолжит работать без остановки.",
        "bot_expiry_3d":
            "⏰ <b>Подписка истекает через 3 дня</b>\n\n"
            "Успей продлить, чтобы VPN не отключился.",
        "bot_expiry_1d":
            "🚨 <b>Подписка истекает завтра!</b>\n\n"
            "Последний шанс продлить без перерыва в работе VPN.",
        "bot_grace_3d":
            "⏰ <b>Через 3 дня VPN отключится</b>\n\n"
            "Подписка в режиме 512 кбит/с — а через 3 дня закроется совсем. "
            "Продли сейчас, чтобы вернуть полную скорость и не остаться без VPN.",
        "bot_stars_renewal_today":
            "🔁 <b>Сегодня Telegram спишет {stars} ⭐ за продление</b>\n\n"
            "Тариф: <b>{plan}</b>\n"
            "Дата списания: <b>{date}</b>\n\n"
            "Если не хочешь продлевать — отмени в Telegram: "
            "Настройки → Звёзды → Подписки → выбери MAX VPN → Cancel.",
        "bot_stars_renewal_tomorrow":
            "🔁 <b>Завтра Telegram спишет {stars} ⭐ за продление</b>\n\n"
            "Тариф: <b>{plan}</b>\n"
            "Дата списания: <b>{date}</b>\n\n"
            "Если не хочешь продлевать — отмени в Telegram: "
            "Настройки → Звёзды → Подписки → выбери MAX VPN → Cancel.",
        "bot_stars_renewal_in":
            "🔁 <b>Через {n} {day_word} Telegram спишет {stars} ⭐ за продление</b>\n\n"
            "Тариф: <b>{plan}</b>\n"
            "Дата списания: <b>{date}</b>\n\n"
            "Если не хочешь продлевать — отмени в Telegram: "
            "Настройки → Звёзды → Подписки → выбери MAX VPN → Cancel.",
        "bot_trial_nudge":
            "👋 <b>Как VPN?</b>\n\n"
            "Ты уже сутки пользуешься пробным периодом.\n\n"
            "Если что-то не работает или есть вопросы — напиши нам, "
            "быстро разберёмся. Если всё ок — выбери постоянный тариф "
            "прямо сейчас, не придётся настраивать заново.",
        "bot_winback":
            "👋 <b>Скучаем без тебя!</b>\n\n"
            "Прошла неделя, а VPN всё ещё выключен.\n\n"
            "Возможно, что-то не устроило — напиши нам в поддержку, "
            "разберёмся. Или просто продли — тарифы с 200 ₽/мес, "
            "без контракта, отмена в любой момент.\n\n"
            "Будем рады видеть тебя снова 🙂",
        "bot_quota_throttle":
            "🐢 <b>Лимит трафика {cap_gb} GB исчерпан</b>\n\n"
            "Скорость снижена до {throttle_mbps} Mbps до конца месяца.\n"
            "Если ты импортировал <b>Subscription URL</b> — конфиг обновится автоматически "
            "в течение нескольких минут.\n\n"
            "💎 Апгрейд тарифа даёт больше квоты.",
        "bot_quota_restore":
            "⚡ <b>Скорость восстановлена</b>\n\n"
            "Квота обновилась — VPN снова работает на полной скорости.",
        # Profile-Title идёт в HTTP-header — non-ASCII не декодируется
        # клиентами; держим ASCII even в RU bundle (см. _safe_header).
        "bot_sub_expired_title":          "MAX VPN - subscription expired",
        # Lava cancel webhook
        "bot_lava_cancelled":
            "❎ <b>Автопродление отключено</b>\n\n"
            "VPN продолжит работать до <b>{until}</b>.\n"
            "Чтобы вернуть автопродление — оформи новую подписку.",
        # Stars manual cancel instructions
        "bot_stars_cancel_instructions":
            "Чтобы окончательно отменить автопродление в Telegram:\n"
            "1. Открой Настройки в Telegram\n"
            "2. Перейди в «Звёзды» → «Активные подписки»\n"
            "3. Выбери MAX VPN → «Отменить»\n\n"
            "Подписка останется активной до конца оплаченного периода.",
        # Trial flow (admin._trial_response)
        "trial_blocked_active_sub":
            "У тебя уже активная подписка. Trial доступен только новым пользователям.",
        "trial_already_claimed":
            "🎁 Trial уже использован.\n\nДля продолжения — выбери тариф в /start",
        "trial_no_server":
            "⚠️ Серверы пока недоступны, попробуй позже",
        "trial_provision_error":
            "⚠️ Ошибка провижининга: {error}",
        "trial_success_awg":
            "🎁 <b>Trial на {days} {day_word} активирован</b>\n\n"
            "📅 До: <b>{expires}</b>\n"
            "🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
            "<b>1) AmneziaWG</b> — главный обфускатор, работает на МТС\n"
            "   Открой Configs (/start → 📁 Конфиги) → скачай AWG-конфиг\n\n"
            "<b>2) VLESS Subscription URL</b> (для Happ / V2Box):\n"
            "<code>{sub_url}</code>\n\n"
            "📖 Инструкция: /howto\n"
            "💎 После trial — выбери постоянный тариф в /start",
        "trial_success_vless":
            "🎁 <b>Trial на {days} {day_word} активирован</b>\n\n"
            "📅 До: <b>{expires}</b>\n"
            "🚀 Скорость: 60 Mbps (как на тарифе База)\n\n"
            "<b>Subscription URL</b> (импортируй в Happ один раз):\n"
            "<code>{sub_url}</code>\n\n"
            "📖 Инструкция: /howto\n"
            "💎 После trial — выбери постоянный тариф в /start",
        "btn_open_configs":             "📁 Открыть конфиги",
        # User-facing API errors (webapp_api.py)
        "bot_api_err_server_unavailable":   "Сервер недоступен",
        "bot_api_err_no_servers":           "Нет доступных серверов",
        "bot_api_err_server_no_agent":      "Сервер не настроен (нет агента)",
        "bot_api_err_config_create_failed": "Ошибка создания конфига на сервере",
        "bot_api_err_slot_already_active":  "Слот уже активен",
        "bot_api_err_slot_activating":      "Слот уже активируется в другой вкладке",
        "bot_api_err_slot_bad_status":      "Неверный статус слота",
        "bot_api_err_slot_not_active":      "Слот не активен",
        # MD-F6: CAS race — слот сбросили между claim и activate.
        "bot_api_err_slot_reset_during_activation": "Слот был сброшен в другом окне. Обнови страницу и попробуй ещё раз.",
        # MD-F-r1: concurrent revoke race — второе устройство уже стартовало revoke.
        "bot_api_err_slot_already_revoking": "Слот уже отзывается — обновится автоматически",
        "bot_api_err_active_sub_exists":    "У тебя уже есть активная подписка. Используй смену тарифа.",
        "bot_api_err_no_active_sub":        "Нет активной подписки",
        "bot_api_err_payment_service":      "Ошибка платёжного сервиса",
        "bot_api_err_concurrent_plan_change": "План был изменён в другом окне. Перезагрузи страницу.",
        "bot_api_err_unknown_plan":         "Неизвестный тариф",
        "bot_api_err_current_plan_unknown": "Ошибка: текущий тариф не распознан",
        "bot_api_err_upgrade_unavailable":  "Оплата апгрейда временно недоступна",
        "bot_api_err_cryptobot_disabled":   "CryptoBot не настроен",
        "bot_api_err_oxapay_disabled":      "OxaPay не подключён",
        "bot_api_err_lava_disabled":        "Lava.top не подключён",
        "bot_api_err_no_recurring_sub":     "Нет активной recurring-подписки",
        "bot_api_err_sub_not_recurring":    "Подписка не recurring",
        "bot_api_err_ticket_empty":         "Пустое сообщение",
        "bot_api_err_ticket_too_long":      "Сообщение слишком длинное",
        "bot_api_err_banned":               "Доступ ограничен. Напиши на support@maxvpnesim.com",
        # Support ticket attachments (PHASE 2: screenshot upload)
        "bot_api_err_bad_body":             "Некорректное тело запроса",
        "bot_api_err_text_too_short":       "Минимум 10 символов",
        "bot_api_err_text_too_long":        "Слишком длинный текст (макс 2000)",
        "bot_api_err_too_many_files":       "Не больше 5 файлов",
        "bot_api_err_too_many_videos":      "Можно прикрепить только одно видео",
        "bot_api_err_file_too_large":       "Файл больше 5 МБ",
        "bot_api_err_video_too_large":      "Видео больше 10 МБ",
        "bot_api_err_total_too_large":      "Суммарный размер больше 30 МБ",
        "bot_api_err_bad_file_type":        "Поддерживаются JPEG/PNG/WebP/HEIC и MP4/MOV",
        "bot_api_err_heic_unsupported":     "HEIC временно недоступен — пересохрани как JPEG/PNG и отправь снова.",
        # Device label fallback
        "bot_device_fallback":              "Устройство #{n}",
        # Telegram invoice descriptions (shown in TG payment dialog)
        "bot_invoice_desc_vpn":             "Доступ к VPN на {days} дней. VLESS-Reality. Оплачивая, принимаете условия: maxvpnesim.com/oferta",
        "bot_invoice_desc_esim":            "eSIM: {name}. Активация при первом подключении. Оплачивая, принимаете условия: maxvpnesim.com/oferta",
        "bot_upgrade_desc_grace":           "Подписка «{name}»",
        "bot_upgrade_desc_active":          "Апгрейд до «{name}». Доплата за {days} дн.",
        # /howto, /referral, /privacy, /rotate_token
        "bot_howto_text":
            "📖 <b>Как настроить VPN — 3 шага</b>\n\n"
            "<b>1. Скачай приложение</b>:\n"
            "   • iOS / Android / Mac — <a href=\"https://apps.apple.com/app/happ-proxy-utility/id6504287215\">Happ</a> "
            "(или <a href=\"https://play.google.com/store/apps/details?id=com.happproxy\">Google Play</a>)\n"
            "   • <b>Windows</b> — <a href=\"https://amnezia.org/downloads\">Amnezia VPN</a> "
            "(не WireGuard.exe — тот даёт 1-2 Мбит/с вместо 120)\n\n"
            "<b>2. После оплаты</b> я пришлю <b>Subscription URL</b> — это твоя постоянная "
            "ссылка. Импортируешь её в Happ <b>один раз</b> — дальше Happ сам подтягивает "
            "обновления и переключает между серверами.\n\n"
            "<b>3. В Happ</b>: «+» → <b>«Подписка»</b> → вставь URL → жми переключатель.\n"
            "   <b>В Amnezia VPN (Windows)</b>: Файл → Импортировать → вставь URL.\n\n"
            "💡 Если получишь ещё и AWG-конфиг — импортируй его в "
            "<a href=\"https://apps.apple.com/app/amneziawg/id6478942365\">AmneziaWG</a> "
            "для усиленного шифрования трафика.\n\n"
            "💡 Если какой-то российский сайт не открывается (Сбер, Госуслуги) — "
            "напиши в поддержку, добавим в исключения.",
        "bot_referral_text":
            "🔗 <b>Реферальная программа</b>\n\n"
            "Приглашай друзей — за каждого, кто купит VPN, получаешь <b>+{bonus} дней</b> бесплатно.\n\n"
            "Твоя ссылка:\n<code>{link}</code>\n\n"
            "👥 Приглашено: <b>{invited}</b>\n"
            "💳 Купили: <b>{converted}</b>\n"
            "🎁 Бонусных дней получено: <b>{bonus_days}</b>",
        "bot_privacy_text":
            "🔒 <b>Политика конфиденциальности</b>\n\n"
            "Мы не логируем трафик и не знаем какие сайты ты посещаешь.\n\n"
            "Подробнее: <a href=\"https://maxvpnesim.com/privacy.html\">maxvpnesim.com/privacy.html</a>",
        "bot_rotate_token_text":
            "🔄 <b>Subscription URL обновлён</b>\n\n"
            "Старая ссылка больше не работает. Импортируй новую в Happ:\n"
            "<code>{url}</code>",
        # Inline menu callbacks
        "bot_menu_vpn_text":
            "🌐 <b>VPN — приватность и защищённое соединение</b>\n\n"
            "Протокол: <b>VLESS + Reality</b> — шифрует трафик, работает в любых сетях\n"
            "Локация: 🇩🇪 Frankfurt\n"
            "Soft-лимит трафика, после — медленнее, но не отключение\n\n"
            "<b>Тарифы:</b>\n"
            "• <b>База</b> 60 Mbps — 2 человека в 4K + телефоны в фоне\n"
            "• <b>Макс</b> 120 Mbps — семья / стриминг + торренты\n",
        "bot_menu_esim_text":
            "📱 <b>eSIM — мобильный интернет за рубежом</b>\n\n"
            "Покупаешь, сканируешь QR — и через 30 сек у тебя интернет в Турции, "
            "Грузии, ОАЭ, Таиланде, Вьетнаме или по всей Европе.\n\n"
            "🇷🇺 Есть отдельный тариф для России — с зарубежным IP "
            "(удобно для международных сервисов и командировок).\n\n"
            "Оплата ⭐ или картой через Telegram.",
        "bot_menu_start_text":
            "👋 Привет! Выбери, что тебя интересует:",
        "bot_btn_back_to_plans":            "◀️ Назад к тарифам",
        "bot_btn_back":                     "◀️ Назад",
        "bot_btn_howto_short":              "📖 Как настроить?",
        "bot_btn_open_esim_catalog":        "📱 Открыть каталог eSIM",
        "bot_plan_chip_base":               "⭐ База — 145 ⭐ (≈200 ₽) · 60 Mbps · 5 устройств",
        "bot_plan_chip_max":                "🚀 Макс — 360 ⭐ (≈500 ₽) · 120 Mbps · 10 устройств",
        "bot_admin_extended":
            "🎁 <b>Доступ продлён</b>\n\n"
            "Админ добавил +{days} дн.\n"
            "Подписка действует до <b>{until}</b>.",
        "bot_ticket_closed_no_reply":
            "✅ <b>Тикет #{ticket_id} закрыт</b>\n\n"
            "Если вопрос остался — открой новый тикет в Mini App.",
    },
    "en": {
        "bot_trial_success_header":     "🎁 <b>Trial activated for {days} {day_word}</b>",
        "bot_trial_success_until":      "📅 Until: <b>{until}</b>",
        "bot_trial_success_speed":      "🚀 Speed: 60 Mbps",
        "bot_trial_success_after":      "💎 After trial — pick a permanent plan ↓",
        "bot_grace_notice":             "⏳ <b>Subscription expired — VPN throttled to 512 kbps for 14 days</b>\n\nRenew in Mini App to restore full speed.",
        "bot_expiry_notice":            "❌ <b>Subscription closed</b>\n\nVPN configs disabled. Open Mini App to pick a new plan ↓",
        "bot_trial_expiry_notice":      "⌛ <b>Trial ended</b>\n\nTo keep VPN — pick a permanent plan ↓",
        "bot_renewal_reminder_today":   "🔁 <b>Today {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_tomorrow":"🔁 <b>Tomorrow {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_in":      "🔁 <b>In {n} {day_word} {amount} ₽ will be charged from your card</b>",
        "bot_renewal_reminder_body":    "\nVPN {plan} → will renew automatically for 30 days.\nCharge date: <b>{date}</b>\n\nWant to cancel? Open Mini App → VPN → Cancel auto-renewal.",
        "bot_purchase_success_title":   "✅ <b>VPN {plan} activated!</b>",
        "bot_purchase_success_until":   "📅 Active until: <b>{until}</b>",
        "bot_purchase_success_sub_url": "🔗 <b>Subscription URL</b> (for Happ / Streisand / Amnezia VPN — import once, updates automatically):\n<code>{url}</code>",
        "bot_purchase_success_awg_caption": "📁 <b>WireGuard config #{n}</b>\nImport into AmneziaWG / WireGuard",
        "bot_purchase_success_partial": "⚙️ Configs ready: {delivered}/{total} (rest will appear shortly)",
        "bot_upgrade_success_title":    "✅ <b>Plan changed to «{plan}»!</b>",
        "bot_upgrade_success_slots":    "🔌 Now you have: <b>{slots}</b>",
        "bot_upgrade_success_check":    "Open <b>My configs</b> — new empty slots are ready.",
        "bot_upgrade_success_paid":     "💎 Paid: {amount} {asset}",
        "bot_precheckout_bad_payload":  "Invalid payload — please re-create the payment.",
        "bot_precheckout_bad_amount":   "Invalid payment amount.",
        "bot_btn_my_configs":           "📁 My configs",
        "bot_btn_howto":                "📖 How to set up",
        "bot_lava_renewed":             "🔁 <b>Subscription renewed automatically</b>\n\nVPN {plan} active until <b>{until}</b>.",
        "bot_lava_renewed_grace":       "\n⚡ Full speed restored.",
        "bot_grace_renewed_title":      "✅ <b>Subscription renewed!</b>",
        "bot_grace_renewed_until":      "📅 Active until: <b>{until}</b>",
        "bot_grace_renewed_speed":      "⚡ Full speed restored — VPN works without limits again.",
        "bot_precheckout_banned":       "Access restricted. Contact support.",
        "bot_precheckout_unknown_plan": "Unknown plan.",
        "bot_precheckout_active_sub":   "You already have an active subscription. To change plans use the «Upgrade» button in Mini App.",
        "bot_precheckout_active_sub_same": "You already have an active «{plan}» subscription. Open Mini App.",
        "bot_precheckout_plan_unavailable": "Plan no longer available. Reopen the menu.",
        "bot_provision_failed":         "❌ <b>VPN configs could not be created</b>\n\nPayment went through, but servers are temporarily unavailable. Support has been notified — they'll connect you manually or refund within a few hours.",
        "bot_lava_autorenew_note":      "🔁 Auto-renewal is enabled — renews automatically each month. Cancel anytime in the VPN section.",
        "bot_referral_bonus_activated": "🎁 <b>Bonus days activated!</b>\n\nAdded: <b>+{days} {day_word}</b>\nSubscription active until: <b>{until}</b>",
        "bot_server_decom":             "🔄 <b>VPN location updated</b>\n\nOne of the VPN servers was decommissioned. Open Happ → pull down to refresh the subscription. Other locations work as usual.",
        "bot_server_migration":         "🔄 <b>Your VPN has been migrated</b>\n\nServer replaced with {server}.\nDownload the updated config in the <a href=\"{url}\">app</a>.",
        "bot_lava_charge_failed":       "⚠️ <b>Could not renew subscription</b>\n\nLava could not charge your card. Lava will retry in 24h. If it fails — VPN will throttle to 512 kbps for 14 days.\n\nCheck your card balance or pay manually via menu.",
        "bot_payment_failed":           "⚠️ <b>Payment failed</b>\n\nLava could not charge your card. Possible reasons:\n• insufficient funds\n• 3DS not passed\n• card blocked for online payments\n\nTry paying again or use another method.",
        "bot_ban_message":              "🚫 Access restricted.\n\nIf you think this is a mistake — write to support@maxvpnesim.com",
        # /start greetings + referral
        "bot_start_greeting_with_trial":
            "👋 Hi! I help you secure your connection and keep things private.\n\n"
            "🎁 <b>First {trial_days} {day_word} free</b> — no card, no subscription. "
            "Just tap the «Try for free» button below, and in 30 seconds you'll have "
            "your own VPN.\n\n"
            "After that — plans starting from 200 ₽/mo.\n\n"
            '<a href="https://maxvpnesim.com/privacy.html">Privacy Policy</a>',
        "bot_start_greeting_no_trial_shop":
            "👋 Welcome back! Open the VPN & eSIM shop with the button below.",
        "bot_start_greeting_no_trial":
            "👋 Welcome back. Plans and subscription — in the app below.",
        "bot_start_greeting_fallback":
            "👋 Hi! I'll help you get internet access without restrictions.\n\n"
            "Pick what you need:",
        "bot_start_referral_late":
            "ℹ️ <b>Referral link not applied</b>\n\n"
            "You are already registered in the bot — referral links only work "
            "for new users. The extended 7-day trial is only available "
            "for those who open the bot for the first time via a link.",
        "bot_btn_try_free":             "🎁 Try for free — {days} {day_word}",
        "bot_btn_open_app":             "🚀 Open app",
        "bot_btn_renew_subscription":   "💎 Renew subscription",
        "bot_btn_choose_plan":          "🎯 Choose plan",
        "bot_lava_err_self_buy":        "Lava doesn't allow self-purchases — please enter a different email.",
        "bot_lava_err_email_rejected":  "Email rejected by the payment system — try another one.",
        "bot_lava_err_payment_rejected":"Payment system rejected the request. Try a different email or payment method.",
        # Scheduler reminders
        "bot_trial_expiry_1d":
            "⏳ <b>Trial ends in 24 hours</b>\n\n"
            "Liked it? Pick a permanent plan — "
            "from 200 ₽/mo, same speed, no interruption.\n\n"
            "Renew now — VPN keeps working without a break.",
        "bot_expiry_3d":
            "⏰ <b>Subscription expires in 3 days</b>\n\n"
            "Renew in time so your VPN doesn't switch off.",
        "bot_expiry_1d":
            "🚨 <b>Subscription expires tomorrow!</b>\n\n"
            "Last chance to renew without interrupting VPN service.",
        "bot_grace_3d":
            "⏰ <b>VPN shuts down in 3 days</b>\n\n"
            "Subscription is throttled to 512 kbps — and in 3 days it will close completely. "
            "Renew now to restore full speed and avoid losing your VPN.",
        "bot_stars_renewal_today":
            "🔁 <b>Today Telegram will charge {stars} ⭐ for renewal</b>\n\n"
            "Plan: <b>{plan}</b>\n"
            "Charge date: <b>{date}</b>\n\n"
            "Don't want to renew? Cancel in Telegram: "
            "Settings → Stars → Subscriptions → pick MAX VPN → Cancel.",
        "bot_stars_renewal_tomorrow":
            "🔁 <b>Tomorrow Telegram will charge {stars} ⭐ for renewal</b>\n\n"
            "Plan: <b>{plan}</b>\n"
            "Charge date: <b>{date}</b>\n\n"
            "Don't want to renew? Cancel in Telegram: "
            "Settings → Stars → Subscriptions → pick MAX VPN → Cancel.",
        "bot_stars_renewal_in":
            "🔁 <b>In {n} {day_word} Telegram will charge {stars} ⭐ for renewal</b>\n\n"
            "Plan: <b>{plan}</b>\n"
            "Charge date: <b>{date}</b>\n\n"
            "Don't want to renew? Cancel in Telegram: "
            "Settings → Stars → Subscriptions → pick MAX VPN → Cancel.",
        "bot_trial_nudge":
            "👋 <b>How's the VPN?</b>\n\n"
            "It's been a day since you activated the trial.\n\n"
            "If anything is broken or you have questions — ping us, "
            "we'll sort it out fast. If all good — pick a permanent plan "
            "right now and you won't have to set anything up again.",
        "bot_winback":
            "👋 <b>We miss you!</b>\n\n"
            "A week has passed and your VPN is still off.\n\n"
            "Maybe something didn't work out — write to support, "
            "we'll figure it out. Or just renew — plans from 200 ₽/mo, "
            "no contract, cancel anytime.\n\n"
            "Would love to see you back 🙂",
        "bot_quota_throttle":
            "🐢 <b>Traffic limit {cap_gb} GB used up</b>\n\n"
            "Speed reduced to {throttle_mbps} Mbps until end of month.\n"
            "If you imported the <b>Subscription URL</b> — the config will update automatically "
            "in a few minutes.\n\n"
            "💎 Upgrade your plan for more quota.",
        "bot_quota_restore":
            "⚡ <b>Speed restored</b>\n\n"
            "Quota reset — VPN works at full speed again.",
        "bot_sub_expired_title":          "MAX VPN - subscription expired",
        # Lava cancel webhook
        "bot_lava_cancelled":
            "❎ <b>Auto-renewal disabled</b>\n\n"
            "VPN will keep working until <b>{until}</b>.\n"
            "To re-enable auto-renewal — start a new subscription.",
        # Stars manual cancel instructions
        "bot_stars_cancel_instructions":
            "To fully cancel auto-renewal in Telegram:\n"
            "1. Open Settings in Telegram\n"
            "2. Go to «Stars» → «Active subscriptions»\n"
            "3. Pick MAX VPN → «Cancel»\n\n"
            "Subscription will stay active until the end of the paid period.",
        # Trial flow (admin._trial_response)
        "trial_blocked_active_sub":
            "You already have an active subscription. Trial is only available to new users.",
        "trial_already_claimed":
            "🎁 Trial already used.\n\nTo continue — pick a plan in /start",
        "trial_no_server":
            "⚠️ Servers are temporarily unavailable, try again later",
        "trial_provision_error":
            "⚠️ Provisioning error: {error}",
        "trial_success_awg":
            "🎁 <b>Trial activated for {days} {day_word}</b>\n\n"
            "📅 Until: <b>{expires}</b>\n"
            "🚀 Speed: 60 Mbps (same as Base plan)\n\n"
            "<b>1) AmneziaWG</b> — main obfuscator, works on Russian carriers\n"
            "   Open Configs (/start → 📁 My configs) → download the AWG config\n\n"
            "<b>2) VLESS Subscription URL</b> (for Happ / V2Box):\n"
            "<code>{sub_url}</code>\n\n"
            "📖 How to set up: /howto\n"
            "💎 After trial — pick a permanent plan in /start",
        "trial_success_vless":
            "🎁 <b>Trial activated for {days} {day_word}</b>\n\n"
            "📅 Until: <b>{expires}</b>\n"
            "🚀 Speed: 60 Mbps (same as Base plan)\n\n"
            "<b>Subscription URL</b> (import in Happ once):\n"
            "<code>{sub_url}</code>\n\n"
            "📖 How to set up: /howto\n"
            "💎 After trial — pick a permanent plan in /start",
        "btn_open_configs":             "📁 Open configs",
        # User-facing API errors (webapp_api.py)
        "bot_api_err_server_unavailable":   "Server unavailable",
        "bot_api_err_no_servers":           "No servers available",
        "bot_api_err_server_no_agent":      "Server not configured (no agent)",
        "bot_api_err_config_create_failed": "Failed to create config on server",
        "bot_api_err_slot_already_active":  "Slot already active",
        "bot_api_err_slot_activating":      "Slot is being activated in another tab",
        "bot_api_err_slot_bad_status":      "Invalid slot status",
        "bot_api_err_slot_not_active":      "Slot not active",
        "bot_api_err_slot_reset_during_activation": "Slot was reset in another window. Reload the page and try again.",
        # MD-F-r1: concurrent revoke race.
        "bot_api_err_slot_already_revoking": "Slot is being revoked — will update automatically",
        "bot_api_err_active_sub_exists":    "You already have an active subscription. Use plan change instead.",
        "bot_api_err_no_active_sub":        "No active subscription",
        "bot_api_err_payment_service":      "Payment service error",
        "bot_api_err_concurrent_plan_change": "Plan was changed in another window. Reload the page.",
        "bot_api_err_unknown_plan":         "Unknown plan",
        "bot_api_err_current_plan_unknown": "Error: current plan not recognized",
        "bot_api_err_upgrade_unavailable":  "Upgrade payment temporarily unavailable",
        "bot_api_err_cryptobot_disabled":   "CryptoBot is not configured",
        "bot_api_err_oxapay_disabled":      "OxaPay is not enabled",
        "bot_api_err_lava_disabled":        "Lava.top is not enabled",
        "bot_api_err_no_recurring_sub":     "No active recurring subscription",
        "bot_api_err_sub_not_recurring":    "Subscription is not recurring",
        "bot_api_err_ticket_empty":         "Empty message",
        "bot_api_err_ticket_too_long":      "Message too long",
        "bot_api_err_banned":               "Access restricted. Contact support@maxvpnesim.com",
        # Support ticket attachments (PHASE 2: screenshot upload)
        "bot_api_err_bad_body":             "Bad request body",
        "bot_api_err_text_too_short":       "At least 10 characters",
        "bot_api_err_text_too_long":        "Text too long (max 2000)",
        "bot_api_err_too_many_files":       "Up to 5 files",
        "bot_api_err_too_many_videos":      "Only one video can be attached",
        "bot_api_err_file_too_large":       "File over 5 MB",
        "bot_api_err_video_too_large":      "Video over 10 MB",
        "bot_api_err_total_too_large":      "Total size over 30 MB",
        "bot_api_err_bad_file_type":        "Supported: JPEG/PNG/WebP/HEIC and MP4/MOV",
        "bot_api_err_heic_unsupported":     "HEIC is temporarily unavailable — please re-export as JPEG/PNG and try again.",
        # Device label fallback
        "bot_device_fallback":              "Device #{n}",
        # Telegram invoice descriptions
        "bot_invoice_desc_vpn":             "VPN access for {days} days. VLESS-Reality. By paying, you accept terms: maxvpnesim.com/oferta",
        "bot_invoice_desc_esim":            "eSIM: {name}. Activates on first connection. By paying, you accept terms: maxvpnesim.com/oferta",
        "bot_upgrade_desc_grace":           "Subscription «{name}»",
        "bot_upgrade_desc_active":          "Upgrade to «{name}». Surcharge for {days} days.",
        # /howto, /referral, /privacy, /rotate_token
        "bot_howto_text":
            "📖 <b>How to set up VPN — 3 steps</b>\n\n"
            "<b>1. Install the app</b>:\n"
            "   • iOS / Android / Mac — <a href=\"https://apps.apple.com/app/happ-proxy-utility/id6504287215\">Happ</a> "
            "(or <a href=\"https://play.google.com/store/apps/details?id=com.happproxy\">Google Play</a>)\n"
            "   • <b>Windows</b> — <a href=\"https://amnezia.org/downloads\">Amnezia VPN</a> "
            "(not WireGuard.exe — that one gives 1-2 Mbps instead of 120)\n\n"
            "<b>2. After payment</b> I'll send you a <b>Subscription URL</b> — your permanent "
            "link. Import it into Happ <b>once</b> — Happ pulls updates and switches "
            "between servers automatically.\n\n"
            "<b>3. In Happ</b>: «+» → <b>«Subscription»</b> → paste URL → tap the toggle.\n"
            "   <b>In Amnezia VPN (Windows)</b>: File → Import → paste URL.\n\n"
            "💡 If you also get an AWG-config — import it into "
            "<a href=\"https://apps.apple.com/app/amneziawg/id6478942365\">AmneziaWG</a> "
            "for stronger traffic obfuscation.\n\n"
            "💡 If some Russian site doesn't open (Sber, Gosuslugi) — "
            "ping support, we'll add it to exceptions.",
        "bot_referral_text":
            "🔗 <b>Referral program</b>\n\n"
            "Invite friends — for each one who buys VPN you get <b>+{bonus} days</b> free.\n\n"
            "Your link:\n<code>{link}</code>\n\n"
            "👥 Invited: <b>{invited}</b>\n"
            "💳 Purchased: <b>{converted}</b>\n"
            "🎁 Bonus days received: <b>{bonus_days}</b>",
        "bot_privacy_text":
            "🔒 <b>Privacy policy</b>\n\n"
            "We don't log traffic and don't know what sites you visit.\n\n"
            "More: <a href=\"https://maxvpnesim.com/privacy.html\">maxvpnesim.com/privacy.html</a>",
        "bot_rotate_token_text":
            "🔄 <b>Subscription URL updated</b>\n\n"
            "The old link no longer works. Import the new one in Happ:\n"
            "<code>{url}</code>",
        # Inline menu callbacks
        "bot_menu_vpn_text":
            "🌐 <b>VPN — privacy and a secure connection</b>\n\n"
            "Protocol: <b>VLESS + Reality</b> — encrypts traffic, works on any network\n"
            "Location: 🇩🇪 Frankfurt\n"
            "Soft traffic limit — after which speed reduces, but no shutoff\n\n"
            "<b>Plans:</b>\n"
            "• <b>Base</b> 60 Mbps — 2 people streaming 4K + phones in background\n"
            "• <b>Max</b> 120 Mbps — family / streaming + torrents\n",
        "bot_menu_esim_text":
            "📱 <b>eSIM — mobile internet abroad</b>\n\n"
            "Buy, scan the QR — and in 30 seconds you have internet in Turkey, "
            "Georgia, UAE, Thailand, Vietnam or across Europe.\n\n"
            "🇷🇺 There's a separate plan for Russia — with foreign IP "
            "(handy for international services and business trips).\n\n"
            "Pay with ⭐ or by card via Telegram.",
        "bot_menu_start_text":
            "👋 Hi! Pick what you need:",
        "bot_btn_back_to_plans":            "◀️ Back to plans",
        "bot_btn_back":                     "◀️ Back",
        "bot_btn_howto_short":              "📖 How to set up?",
        "bot_btn_open_esim_catalog":        "📱 Open eSIM catalog",
        "bot_plan_chip_base":               "⭐ Base — 145 ⭐ (≈$2.2) · 60 Mbps · 5 devices",
        "bot_plan_chip_max":                "🚀 Max — 360 ⭐ (≈$5.5) · 120 Mbps · 10 devices",
        "bot_admin_extended":
            "🎁 <b>Subscription extended</b>\n\n"
            "Admin added +{days} day(s).\n"
            "Active until <b>{until}</b>.",
        "bot_ticket_closed_no_reply":
            "✅ <b>Ticket #{ticket_id} closed</b>\n\n"
            "If your question is still unresolved — open a new ticket in the Mini App.",
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
