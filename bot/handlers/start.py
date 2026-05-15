import os
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

from config import ADMIN_ID, SHOW_ESIM
from services.database import upsert_user, set_referred_by, get_referral_stats, add_referral_bonus
from services.trial import can_claim_trial, TRIAL_DAYS

router = Router()

WEBAPP_URL = os.getenv("WEBAPP_URL", "")
REFERRAL_BONUS_DAYS = 7  # дней бонуса рефереру за первую покупку реферала


def _main_menu(start_param: str = "", trial_eligible: bool = False) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    # Триал — самый верх меню для тех кому он доступен. Это first-screen
    # call-to-action, выводит юзера в активацию за один клик без захода в
    # Mini App.
    if trial_eligible:
        buttons.append([
            InlineKeyboardButton(
                text=f"🎁 Попробуй бесплатно — {TRIAL_DAYS} дня",
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
                text="🚀 Открыть приложение",
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

    await upsert_user(user_id=user_id, username=username, first_name=first_name)

    # Парсим start param: /start ref_123456789  или  /start plan_vpn_popular
    args = message.text.split(maxsplit=1)
    start_param = args[1].strip() if len(args) > 1 else ""

    if start_param.startswith("ref_"):
        try:
            referrer_id = int(start_param[4:])
            if referrer_id != user_id:
                await set_referred_by(user_id, referrer_id)
        except ValueError:
            pass

    # Триал first-screen: если юзеру он доступен — упоминаем в тексте +
    # вешаем верхней кнопкой меню. Если уже есть подписка или недавно был
    # триал — нейтральное приветствие.
    trial_eligible = await can_claim_trial(user_id)

    if trial_eligible:
        text = (
            "👋 Привет! Я помогу обойти блокировки.\n\n"
            f"🎁 <b>Первые {TRIAL_DAYS} дня бесплатно</b> — без карты, без подписки. "
            "Просто нажми кнопку «Попробуй бесплатно» ниже, и через 30 секунд у тебя "
            "будет личный VPN.\n\n"
            "Дальше — тарифы от 200 ₽/мес."
        )
    elif WEBAPP_URL:
        # Текст подстраивается под feature flag — если eSIM скрыт, не упоминаем
        # его в приветствии. Юзеры приходящие за чистым VPN не должны видеть
        # «магазин eSIM» который им недоступен.
        if SHOW_ESIM:
            text = "👋 С возвращением! Открывай магазин VPN & eSIM кнопкой ниже."
        else:
            text = "👋 С возвращением. Тарифы и подписка — в приложении ниже."
    else:
        text = (
            "👋 Привет! Я помогу тебе получить доступ к интернету без ограничений.\n\n"
            "Выбери, что тебя интересует:"
        )

    await message.answer(
        text,
        reply_markup=_main_menu(start_param, trial_eligible=trial_eligible),
        parse_mode="HTML",
    )


@router.message(lambda m: m.text and m.text.strip() == "/referral")
async def cmd_referral(message: Message):
    """Показывает реферальную ссылку и статистику."""
    user_id = message.from_user.id
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    stats = await get_referral_stats(user_id)

    await message.answer(
        "🔗 <b>Реферальная программа</b>\n\n"
        f"Приглашай друзей — за каждого, кто купит VPN, получаешь <b>+{REFERRAL_BONUS_DAYS} дней</b> бесплатно.\n\n"
        f"Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: <b>{stats['invited']}</b>\n"
        f"💳 Купили: <b>{stats['converted']}</b>\n"
        f"🎁 Бонусных дней получено: <b>{stats['bonus_days']}</b>",
        parse_mode="HTML",
    )


@router.message(lambda m: m.text and m.text.strip() == "/rotate_token")
async def cmd_rotate_token(message: Message):
    """Ротирует subscription token — старая ссылка перестаёт работать.

    Юзер: «случайно выложил Subscription URL в чат — стрёмно». Команда
    выдаёт новый sub_token, старый отзывается. Импортировать заново в Happ.
    """
    from services.database import rotate_sub_token
    user_id = message.from_user.id
    new_token = await rotate_sub_token(user_id)
    new_url = f"https://maxvpnesim.com/sub/{new_token}"
    await message.answer(
        "🔄 <b>Subscription URL обновлён</b>\n\n"
        "Старая ссылка больше не работает. Импортируй новую в Happ:\n"
        f"<code>{new_url}</code>",
        parse_mode="HTML",
    )
