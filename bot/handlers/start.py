import os
import re
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

from config import ADMIN_ID, SHOW_ESIM
from services.database import (
    upsert_user, set_referred_by, get_referral_stats, add_referral_bonus,
    has_any_subscription, is_eligible_referrer,
)
from services.trial import can_claim_trial, TRIAL_DAYS

router = Router()
# Private-chats only. Channel posts / group messages have no `from_user`
# (channel posts) or different semantics — handlers crash on from_user.id.
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

# UTM-payload разрешает только [a-z0-9_-], lowercase. Telegram сам ограничивает
# start-param ровно этим алфавитом (+ длина <=64), но мы дополнительно
# валидируем чтоб мусор не записался в БД при ручных подменах.
_UTM_RE = re.compile(r"^[a-z0-9_\-]{1,60}$")


def _derive_source(start_param: str) -> str:
    """First-touch attribution из `/start <param>`.

    Возвращает namespace-prefixed строку для users.traffic_source:
      utm_<code>   → "utm:<code>"   — рекламные ссылки
      ref_<id>     → "referral:<id>" — приглашение от другого юзера
      plan_<...>   → "deeplink:plan" — пришёл по продуктовому deep-link (низкий
                     приоритет — это уже re-engaged юзер, не «новый источник»),
                     но всё-таки фиксируем чтобы понимать долю
      "" (пусто)   → "direct" — открыл бота напрямую (без ссылки)
      прочее       → "other:<param>" — кто-то прислал странный start
    """
    p = start_param.strip()
    if not p:
        return "direct"
    if p.startswith("utm_"):
        code = p[4:]
        if _UTM_RE.match(code):
            return f"utm:{code}"
        return "utm:invalid"
    if p.startswith("ref_"):
        rest = p[4:]
        if rest.isdigit():
            return f"referral:{rest}"
        return "referral:invalid"
    if p.startswith("plan_") or p in ("esim", "support"):
        return "deeplink"
    # Любая другая полезная нагрузка — сохраняем как есть для будущей разборки
    safe = p[:60] if _UTM_RE.match(p[:60]) else "unknown"
    return f"other:{safe}"

WEBAPP_URL = os.getenv("WEBAPP_URL", "")
REFERRAL_BONUS_DAYS = 7  # дней бонуса рефереру за первую покупку реферала


def _main_menu(
    start_param: str = "",
    trial_eligible: bool = False,
    lang: str | None = None,
    trial_days: int = TRIAL_DAYS,
) -> InlineKeyboardMarkup:
    from services.i18n_bot import t as _t, day_word as _day_word
    buttons: list[list[InlineKeyboardButton]] = []

    # Триал — самый верх меню для тех кому он доступен. Это first-screen
    # call-to-action, выводит юзера в активацию за один клик без захода в
    # Mini App.
    # trial_days передаётся снаружи (3 для обычного юзера, 7 для referred) —
    # должно совпасть с тем что юзер увидит в greeting'е и реально получит.
    if trial_eligible:
        buttons.append([
            InlineKeyboardButton(
                text=_t(
                    lang, "bot_btn_try_free",
                    days=trial_days, day_word=_day_word(lang, trial_days),
                ),
                callback_data="trial:claim",
            )
        ])

    if WEBAPP_URL:
        url = WEBAPP_URL
        # Deep link: открываем нужный раздел через startapp param
        if start_param.startswith("plan_"):
            url = f"{WEBAPP_URL}/vpn/plans"
        elif start_param == "esim" and SHOW_ESIM:
            url = f"{WEBAPP_URL}/esim"
        elif start_param == "support":
            url = f"{WEBAPP_URL}/support"

        buttons.append([
            InlineKeyboardButton(
                text=_t(lang, "bot_btn_open_app"),
                web_app=WebAppInfo(url=url),
            )
        ])
    else:
        buttons.append([InlineKeyboardButton(text="🌐 VPN",  callback_data="menu:vpn")])
        if SHOW_ESIM:
            buttons.append([InlineKeyboardButton(text="📱 eSIM", callback_data="menu:esim")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id    = message.from_user.id
    username   = message.from_user.username
    first_name = message.from_user.first_name
    # Telegram language_code: 'ru', 'en', 'en-US', etc. None для legacy клиентов.
    # i18n_bot.t() сам нормализует → ru/en.
    user_lang  = message.from_user.language_code or None

    # Парсим start param ДО upsert — нужен для first-touch attribution.
    # /start ref_123456789, /start plan_vpn_popular, /start utm_tg_pythonist_jul
    args = message.text.split(maxsplit=1)
    start_param = args[1].strip() if len(args) > 1 else ""

    # First-touch attribution: запишется только при первом INSERT, у уже
    # существующего юзера НЕ перетрётся. `lang` обновляется при каждом /start
    # (см. upsert_user — для traffic_source first-touch, для lang last-seen).
    await upsert_user(
        user_id=user_id,
        username=username,
        first_name=first_name,
        traffic_source=_derive_source(start_param),
        lang=user_lang,
    )

    # Ban-гейт: silent-ish reject.  Юзер не видит меню/триал/реф-ссылки,
    # но получает один читаемый текст чтобы понимал что произошло.
    from services.database import is_user_banned
    from services.i18n_bot import t as _t
    if await is_user_banned(user_id):
        await message.answer(_t(user_lang, "bot_ban_message"))
        return

    # Реферальный код. Логика:
    # 1. Self-referral — silently игнорируем
    # 2. Юзер уже подписан (trial или paid) — НЕ записываем ref + шлём
    #    «ты уже зарегистрирован» (поздний ref нечестен — потенциальный абуз)
    # 3. Реферрер не paid юзер — silently игнорируем (его ссылка не работает,
    #    требование #3: реферальная программа только с платной подпиской)
    # 4. Иначе — set_referred_by, юзер получит 7-day trial вместо 3-day
    ref_link_late = False  # для warning внизу
    if start_param.startswith("ref_"):
        try:
            referrer_id = int(start_param[4:])
            # Sanity bound: Telegram user_id fits in int53 (~9e15). Всё что
            # выше — мусор/попытка спама. Без upper-bound на /start ref_<huge>
            # мы бы парсили произвольно длинные числа и били DB лишним запросом.
            if 0 < referrer_id < 10**13 and referrer_id != user_id:
                if await has_any_subscription(user_id):
                    ref_link_late = True
                elif await is_eligible_referrer(referrer_id):
                    await set_referred_by(user_id, referrer_id)
                # else: referrer не существует / забанен / не paid — silent skip
        except ValueError:
            pass

    # Триал first-screen: если юзеру он доступен — упоминаем в тексте +
    # вешаем верхней кнопкой меню. Если уже есть подписка или недавно был
    # триал — нейтральное приветствие.
    trial_eligible = await can_claim_trial(user_id)

    from services.i18n_bot import day_word as _day_word
    from services.trial import trial_days_for
    # trial_days_for(user_id): для referred-юзеров возвращает 7, для остальных — 3.
    # Hard-coded TRIAL_DAYS=3 ниже игнорировал реферал-бонус, и юзер пришедший
    # по ссылке видел «3 дня бесплатно» вместо обещанных 7. Используется и в
    # greeting'е, и в кнопке «Попробуй бесплатно» через _main_menu(trial_days=...).
    td = await trial_days_for(user_id) if trial_eligible else TRIAL_DAYS
    if trial_eligible:
        text = _t(
            user_lang, "bot_start_greeting_with_trial",
            trial_days=td, day_word=_day_word(user_lang, td),
        )
    elif WEBAPP_URL:
        # Текст подстраивается под feature flag — если eSIM скрыт, не упоминаем
        # его в приветствии. Юзеры приходящие за чистым VPN не должны видеть
        # «магазин eSIM» который им недоступен.
        if SHOW_ESIM:
            text = _t(user_lang, "bot_start_greeting_no_trial_shop")
        else:
            text = _t(user_lang, "bot_start_greeting_no_trial")
    else:
        text = _t(user_lang, "bot_start_greeting_fallback")

    # Сначала шлём «уже зарегистрирован» если был поздний ref-клик —
    # юзер должен понять что ссылка не применилась, чтобы не ждал бонуса.
    if ref_link_late:
        await message.answer(
            _t(user_lang, "bot_start_referral_late"),
            parse_mode="HTML",
        )

    await message.answer(
        text,
        reply_markup=_main_menu(
            start_param, trial_eligible=trial_eligible, lang=user_lang,
            trial_days=td,
        ),
        parse_mode="HTML",
    )


@router.message(lambda m: m.text and m.text.strip() == "/referral")
async def cmd_referral(message: Message):
    """Показывает реферальную ссылку и статистику."""
    user_id = message.from_user.id
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    stats = await get_referral_stats(user_id)

    from services.database import get_user_lang
    from services.i18n_bot import t as _t
    lang = await get_user_lang(user_id)

    await message.answer(
        _t(
            lang, "bot_referral_text",
            bonus=REFERRAL_BONUS_DAYS,
            link=ref_link,
            invited=stats["invited"],
            converted=stats["converted"],
            bonus_days=stats["bonus_days"],
        ),
        parse_mode="HTML",
    )


@router.message(lambda m: m.text and m.text.strip() == "/privacy")
async def cmd_privacy(message: Message):
    from services.database import get_user_lang
    from services.i18n_bot import t as _t
    lang = await get_user_lang(message.from_user.id)
    await message.answer(
        _t(lang, "bot_privacy_text"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(lambda m: m.text and m.text.strip() == "/rotate_token")
async def cmd_rotate_token(message: Message):
    """Ротирует subscription token — старая ссылка перестаёт работать.

    Юзер: «случайно выложил Subscription URL в чат — стрёмно». Команда
    выдаёт новый sub_token, старый отзывается. Импортировать заново в Happ.
    """
    from services.database import rotate_sub_token, get_user_lang
    from services.i18n_bot import t as _t
    user_id = message.from_user.id
    new_token = await rotate_sub_token(user_id)
    new_url = f"https://maxvpnesim.com/sub/{new_token}"
    lang = await get_user_lang(user_id)
    await message.answer(
        _t(lang, "bot_rotate_token_text", url=new_url),
        parse_mode="HTML",
    )
