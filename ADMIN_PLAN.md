# Admin Bot — Plan

## Что есть сейчас

### БД (database.py)
- `users` — id, username, first_name, referred_by, ref_bonus_days
- `subscriptions` — plan, payment_id, stars_paid, status, expires_at, pending_plan
- `orders` — старая таблица, дублирует subscriptions (legacy)
- `configs` — VPN слоты (awg/vless), peer_name, config_data
- `servers` — VPN серверы
- `support_tickets` — category, message, status, admin_msg_id

### Текущие команды в боте
- `/admin` — статистика (юзеры, заказы, звёзды)
- `/gift <план>` — бесплатный VPN себе
- `/send <user_id> <план>` — подарить юзеру
- Reply на тикет → пересылка ответа юзеру

---

## Что нужно добавить

### 1. БД — новые поля

```sql
-- Различать метод оплаты (сейчас определяем по префиксу payment_id — хрупко)
ALTER TABLE subscriptions ADD COLUMN payment_method TEXT; -- 'stars' | 'crypto'
ALTER TABLE subscriptions ADD COLUMN amount_usd TEXT;     -- для крипто-платежей

-- Статус возврата
-- support_tickets.status уже есть (open/closed), добавить 'refund_requested'
```

### 2. БД — новые функции

```python
get_all_tickets(status)          # очередь тикетов для админа
get_user_full(user_id)           # юзер + подписки + конфиги + рефералы
get_recent_payments(limit=20)    # последние оплаты
deactivate_subscription(sub_id)  # для возвратов
extend_subscription(sub_id, days) # ручное продление
get_referral_chain(user_id)      # кто пригласил + кого пригласил
close_ticket(ticket_id)
```

---

## Структура админ-бота

### Команды
```
/start    — главное меню
/stats    — быстрая статистика
/user <id|@username> — карточка юзера
/tickets  — открытые тикеты
/payments — последние оплаты
/gift     — (уже есть)
/send     — (уже есть)
```

### Главное меню
```
📊 Статистика    👥 Пользователи
🎫 Тикеты        💸 Платежи
```

### Карточка пользователя
```
👤 @username · ID: 123456
📅 Зарегистрирован: 01.04.2025

Подписка: Про · активна до 15.05.2025
Оплатил: ⭐️ 342 Stars

Рефералов приглашено: 3
Из них купили: 2
Бонус начислен: +14 дней

[➕ Продлить]  [❌ Отменить]  [💸 Возврат]
```

### Карточка тикета
```
🎫 Тикет #42 · payment
👤 @username (ID: 123456)
📝 "Хочу вернуть деньги за подписку"

Подписка: Про · оплата ⭐️ 342 Stars
charge_id: XXXXXX

[💬 Ответить]  [💸 Вернуть Stars]  [✅ Закрыть]
```

---

## Возвраты

### Stars
- Автоматически: `bot.refund_star_payment(user_id, charge_id)`
- `charge_id` берём из `subscriptions.payment_id`
- После возврата: `deactivate_subscription(sub_id)`
- Уведомление юзеру

### Crypto (CryptoBot)
- API возврата нет — только вручную через CryptoBot dashboard
- Бот показывает сумму в USD и ссылку на CryptoBot
- Кнопка "Подтвердить ручной возврат" → деактивирует подписку + уведомляет юзера

---

## Реферальная система

- В карточке юзера: кто его пригласил + список кого он пригласил
- Ручное начисление бонусных дней: `/bonus <user_id> <days>`
- При оформлении возврата — списывать реферальный бонус рефереру (опционально)

---

## Статистика (/stats)

```
📊 Статистика · 28.04.2025

👥 Всего юзеров: 142
🟢 Активных подписок: 38
💀 Истёкших: 24

⭐️ Заработано Stars: 12 840
💵 Заработано крипто: ~$187

🎫 Тикетов открытых: 3
👥 Рефералов сегодня: 2
```

---

## Технически

- Отдельный бот (второй токен в `.env`)
- Тот же `database.py` — те же таблицы, никакой дупликации
- `ADMIN_IDS` — список (не один ID), чтобы несколько операторов
- ~400-500 строк Python
- aiogram 3.x, инлайн-клавиатуры

## Файлы

```
bot/
  admin_bot.py          # точка входа
  admin/
    handlers.py         # все хендлеры
    keyboards.py        # инлайн-клавиатуры
    formatters.py       # форматирование карточек
```
