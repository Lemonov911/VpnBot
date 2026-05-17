# Платёжная система VpnBot

Полная архитектура: методы оплаты, multi-period, recurring, cancel-флоу, webhook-обработка, безопасность.

> Цены и тарифы → [PRICING.md](./PRICING.md). Реферальная программа → [REFERRAL.md](./REFERRAL.md). Здесь — как платежи работают технически.

---

## Матрица возможностей

| Метод | 1м | 3м | 6м | 12м | Auto-renew | Cancel-flow |
|---|---|---|---|---|---|---|
| ⭐ Telegram Stars | ✅ | ✅ | ✅ | ✅ | ✅ (только 1м) | TG → Настройки → Звёзды |
| 💳 Карта Lava (Smart Glocal) | ✅ | ✅ | ✅ | ✅ | ✅ (любой период) | Кнопка в Mini App |
| 🔗 Cryptomus (on-chain) | ✅ | ✅ | ✅ | ✅ | ❌ (one-time) | — (наступает expiry) |
| 💎 CryptoBot (USDT/TON/BTC через TG) | ✅ | ✅ | ✅ | ✅ | ❌ (one-time) | — (наступает expiry) |

**32 покупательских комбинации** (4 метода × 4 периода × 2 плана).

---

## Архитектура backend

### Plan keys

8 ключей в `bot/services/plans.py::VPN_PLANS`:

```
vpn_base       (1 мес)
vpn_base_3m
vpn_base_6m
vpn_base_12m
vpn_max        (1 мес)
vpn_max_3m
vpn_max_6m
vpn_max_12m
```

Каждый ключ имеет: `stars`, `rub`, `usd`, `duration_days`, `awg_slots`, `vless_slots`, `wg_slots`, `speed_mbps`, `soft_cap_gb`, `throttle_mbps`. Multi-period планы дополнительно имеют флаг `multi_period: True` (используется для guard'ов).

### Invoice endpoints

| Endpoint | Метод | Handler | Что делает |
|---|---|---|---|
| `POST /api/vpn/invoice` | Stars | `handle_vpn_invoice` | `bot.create_invoice_link` с currency='XTR'. Для 1m с `recurring=True` добавляет `subscription_period=2592000` (30 дней). |
| `POST /api/vpn/invoice/crypto` | CryptoBot | `handle_cryptobot_invoice` | POST на pay.crypt.bot/api/createInvoice. Payload `vpn:{user_id}:{plan_key}`. |
| `POST /api/vpn/invoice/cryptomus` | Cryptomus | `handle_cryptomus_invoice` | POST api.cryptomus.com/v1/payment. order_id `vpn-{user_id}-{plan_key}-{ts_min}`. MD5 подпись. |
| `POST /api/vpn/invoice/lavatop` | Lava | `handle_lavatop_invoice` | POST gate.lava.top/api/v2/invoice. Один offer_id × 4 periodicity (MONTHLY/PERIOD_90_DAYS/PERIOD_180_DAYS/PERIOD_YEAR). |

### Webhook endpoints

| Endpoint | Метод | Auth |
|---|---|---|
| Telegram `successful_payment` event | Stars | aiogram polling, signature in update |
| `POST /api/cryptobot/webhook` | CryptoBot | HMAC-SHA256 в `crypto-pay-api-signature` header |
| `POST /api/cryptomus/webhook` | Cryptomus | MD5: `md5(base64(json_body) + payment_key)` в поле `sign` |
| `POST /api/lavatop/webhook` | Lava | X-Api-Key header (тот же что для исходящих запросов) |

### Идемпотентность

Каждый webhook handler:
1. Извлекает уникальный `payment_id` (charge_id / contract_id / uuid)
2. Проверяет `get_subscription_by_payment_id` — если найден → 200, no-op
3. Иначе `create_subscription` с UNIQUE-constraint на `payment_id` — catches TOCTOU race
4. Provisioning через `provision_vpn_slots_async` (общий helper)
5. Notification юзеру в чат

`payment_id` форматы:
- Stars: `<telegram_payment_charge_id>` (например `123_abc_xyz`)
- CryptoBot: `crypto_<invoice_id>`
- Cryptomus: `cryptomus_<uuid>`
- Lava: `lavatop_<contractId>`

---

## Recurring / auto-renew flow

### Lava (Smart Glocal subscription)

```
1. User: PaymentSheet → 💳 Lava → выбирает период → жмёт «Оплатить»
2. handle_lavatop_invoice → Lava API с periodicity= MONTHLY/PERIOD_90_DAYS/etc
3. Lava возвращает paymentUrl → WebApp.openLink
4. User оплачивает → Lava processes → webhook event=payment.success status=subscription-active
5. handle_lavatop_webhook:
   - create_subscription(..., parent_contract_id=<contractId>, auto_renew=True, payment_provider='lavatop')
   - provision_vpn_slots_async
   - Notify юзеру
6. Через N дней (1/3/6/12 в зависимости от периода) Lava сама списывает с карты
7. Прилетает webhook event=subscription.recurring.payment.success с parentContractId
8. handle_lavatop_webhook:
   - get_subscription_by_parent_contract → нашли наш sub
   - extend_subscription_expires_at(sub_id, new_expires)
   - Notify «🔁 Подписка продлена автоматически»
```

**Recurring failure** (нет денег на карте): event=subscription.recurring.payment.failed → notify юзеру с подсказкой проверить баланс. Если 19 retry'ев не помогут — sub переходит в обычный grace flow при expiry.

### Stars (Telegram subscription)

```
1. User: PaymentSheet → ⭐ Stars → 1м chip → toggle 🔁 ON
2. handle_vpn_invoice с recurring=True → bot.create_invoice_link с subscription_period=2592000
3. Telegram открывает Stars-invoice (с пометкой «Subscription»)
4. User платит → successful_payment с is_first_recurring=True
5. on_successful_payment в handlers/vpn.py:
   - _deliver_vpn(auto_renew=True) → create_subscription с payment_provider='stars'
6. Через 30 дней Telegram сам списывает 180⭐
7. successful_payment с is_recurring=True (без is_first_recurring)
8. _handle_stars_renewal:
   - get_recurring_sub_for_renewal(user_id, plan_key) → нашли parent
   - extend_subscription_expires_at(+30 дней)
   - record_payment для аналитики
   - Notify «🔁 Подписка продлена • Списано 180⭐»
```

**Telegram ограничения**:
- Только 30-дневный subscription_period (`2592000` секунд)
- Бот не может отменить sub через API — только user через Telegram-клиент

### Cryptomus + CryptoBot
Без recurring. Каждая покупка = отдельный one-time payment на нужную сумму. После expires_at юзер должен сам купить снова.

---

## Cancel-flow

### Lava (в Mini App)

```
User: VPN page → плашка «🔁 Автопродление включено» → кнопка «Отменить»
    ↓
CancelRenewalModal: подтверждение с явной датой «работает до X»
    ↓
POST /api/vpn/subscription/cancel-renewal
    ↓
handle_cancel_renewal:
    1. Lava API: POST /api/v2/subscriptions/{contract_id}/cancel
    2. disable_auto_renew(sub_id) в БД
    3. Если Lava API упал → admin alert «ручная отмена нужна»
    ↓
UI обновляется: «❎ Автопродление отключено»
User дослужит текущий период до expires_at, потом обычный grace flow.
```

Также user может отменить в **Lava-кабинете напрямую** → Lava пришлёт webhook `subscription.cancelled` → мы поймаем → автоматически снимем флаг + notify.

### Stars (в Telegram-клиенте)

Бот не имеет API для отмены Stars-подписки. Юзер делает сам:

```
Telegram → Настройки → Звёзды и Premium → Активные подписки → MAX VPN → Cancel
```

В Mini App плашка содержит:
- Текстовую подсказку
- Кнопку «⚙️ Открыть настройки Звёзд» — deep link через `WebApp.openTelegramLink('https://t.me/premium')` (один тап вместо 4 шагов)

После отмены Telegram перестаёт списывать → бот больше не получает renewal-events → sub доживёт до expires_at и истечёт.

### Cryptomus + CryptoBot
Нечего отменять. Sub истекает естественно.

---

## Renewal reminder (за 3 дня до auto-charge)

`bot/services/scheduler.py::_send_renewal_reminders` бьёт каждый час:

```sql
SELECT * FROM subscriptions
WHERE auto_renew=1
  AND payment_provider IN ('lavatop', 'stars')
  AND expires_at BETWEEN now AND now+3days
  AND reminded_renewal_3d = 0
  AND status IN ('active','grace')
```

Шлёт уведомление с разным текстом для Lava/Stars (Lava с упоминанием карты + ссылкой на cancel в Mini App; Stars с инструкцией где cancel в Telegram). Ставит `reminded_renewal_3d=1`.

На каждом успешном renewal `extend_subscription_expires_at` сбрасывает `reminded_renewal_3d=0` → следующий цикл начнётся чисто.

Снижает chargeback risk + строит trust («предупредил, не сюрприз»).

---

## Безопасность

### Pre-checkout validation (Stars)
`on_pre_checkout_query`:
- Проверяет что `total_amount >= plan.stars` (защита от занижения суммы)
- Проверяет что payload-формат знакомый (vpn_*, esim:*, plan_upgrade:*)

### Webhook signature
- **CryptoBot**: HMAC-SHA256 от raw body с CRYPTOBOT_TOKEN
- **Cryptomus**: MD5(base64(json) + payment_key); пробуем 3 варианта JSON-форматирования (Python compact, separators, PHP-style \\/)
- **Lava**: constant-time compare X-Api-Key (через `hmac.compare_digest`)

### multi_period guard
~~CryptoBot и Lava раньше блокировали multi_period планы~~ — теперь все 4 метода принимают; guard убран.

### Idempotency
- UNIQUE constraint на `subscriptions.payment_id`
- `mark_payment_refunded` с `WHERE refunded_at IS NULL` — защита от двойного refund

### Sum verification
- Cryptomus/Lava webhook: amount из body сверяется с `plan.rub` или `plan.usd`
- Без этого подделанный webhook мог бы зачислить дешёвый план как премиум

### Trial close
При первой платной покупке `_close_trial_on_paid_purchase` закрывает активный триал юзера (если есть), чтобы не было параллельно trial+paid пиров в Happ-балансировке.

---

## Environment variables

```env
# Stars (через Telegram, никакого setup'а)
BOT_TOKEN=<bot api token>

# CryptoBot
CRYPTOBOT_TOKEN=<token from @CryptoBot Crypto Pay API>

# Cryptomus (выключен пока user не подключит)
CRYPTOMUS_MERCHANT_UUID=<merchant uuid>
CRYPTOMUS_PAYMENT_KEY=<payment api key>
CRYPTOMUS_ENABLED=true

# Lava
LAVATOP_API_KEY=<X-Api-Key из gate.lava.top>
LAVATOP_OFFER_VPN_BASE=<UUID offer'а «база» — один offer_id × 4 periodicity>
LAVATOP_OFFER_VPN_MAX=<UUID offer'а «макс»>
LAVATOP_WEBHOOK_KEY=<тот же X-Api-Key, либо отдельный>
LAVATOP_ENABLED=true
```

Webhook URLs (настраиваются в кабинете каждого провайдера):
- CryptoBot: `https://maxvpnesim.com/api/cryptobot/webhook`
- Cryptomus: `https://maxvpnesim.com/api/cryptomus/webhook`
- Lava: `https://maxvpnesim.com/api/lavatop/webhook`

---

## Frontend UI flow

```
User → /vpn/plans
    ↓
Клик на тариф (карточка с ценой)
    ↓
PaymentSheet (bottom-sheet)
    - 4 radio метода (Stars / Lava / Cryptomus / CryptoBot) — гейтятся features.lavatop / .cryptomus
    - Под выбранным методом — 4 chip-периода (1м/3м/6м/12м) с ценой и discount %
    - Для Stars+1m — toggle «🔁 Автопродление каждый месяц» (default ON)
    - Для Lava — info-badge «🔁 Автопродление подписки» (всегда recurring, toggle нет)
    - Кнопка «Оплатить N ⭐/₽»
    ↓
handleBuy:
    - Stars: WebApp.openInvoice (нативный) → callback(s)
    - Lava/Cryptomus/CryptoBot: createInvoice → openLink в браузер
    ↓
[для не-Stars методов] PostPayOnboarding overlay:
    🎉 Платёж открыт
    1. Оплати в браузере
    2. Конфиги приедут автоматически (5-30 сек)
    3. Подключи Happ
    [Перейти в Мои конфиги] [Закрыть]
    ↓
Webhook прилетает на бэк → sub created → бот шлёт «✅ оплачен» в чат
    ↓
Юзер открывает Mini App → /vpn → видит активную подписку с auto-renew badge (если recurring)
```

---

## Файлы (source of truth)

### Backend
- `bot/services/plans.py` — VPN_PLANS dict, цены и слоты
- `bot/services/webapp_api.py` — все 4 invoice endpoint'а + 3 webhook (Stars не там, он в handlers/)
- `bot/services/cryptobot.py` / `cryptomus.py` / `lavatop.py` — клиенты к платёжным API
- `bot/handlers/vpn.py` — pre_checkout, on_successful_payment, _deliver_vpn, _handle_stars_renewal, _close_trial_on_paid_purchase
- `bot/services/scheduler.py` — `_send_renewal_reminders`, `_send_expiry_reminders`, grace/expired transitions
- `bot/services/database.py` — миграции (subscriptions.parent_contract_id, auto_renew, payment_provider, reminded_renewal_3d) + helpers

### Frontend
- `webapp/src/components/PaymentSheet.tsx` — главный экран покупки + STARS_PRICES/RUB_PRICES таблицы
- `webapp/src/components/CancelRenewalModal.tsx` — модалка отмены автопродления
- `webapp/src/components/PostPayOnboarding.tsx` — «что дальше» после openLink
- `webapp/src/pages/Plans.tsx` — карточки тарифов + handleBuy
- `webapp/src/pages/VPN.tsx` — управление активной подпиской + cancel UI
- `webapp/src/api/index.ts` — createVpnInvoice / createVpnInvoiceCrypto / createVpnInvoiceCryptomus / createVpnInvoiceLavatop / cancelLavatopRenewal / getFeatures
- `webapp/src/i18n.tsx` — все текста RU/EN

---

## Полезные команды

### Проверить какие methods active на проде
```bash
curl https://maxvpnesim.com/api/health | python3 -m json.tool
# {"features": {"cryptobot": true, "cryptomus": false, "lavatop": true, "esim": false}}
```

### Посмотреть все subs юзера
```bash
ssh root@151.243.113.31 'sqlite3 /opt/vpnbot/bot/bot.db \
  "SELECT id, plan, status, auto_renew, payment_provider, expires_at \
   FROM subscriptions WHERE user_id=<TG_USER_ID> ORDER BY id DESC LIMIT 5;"'
```

### Сбросить sub в expired для тестов UX
```bash
ssh root@151.243.113.31 'sqlite3 /opt/vpnbot/bot/bot.db \
  "UPDATE subscriptions SET status=\"expired\", expires_at=datetime(\"now\",\"-1 day\") \
   WHERE id=<SUB_ID>;"'
```

### Проверить Lava offer'ы и цены
```bash
curl -H "X-Api-Key: $LAVATOP_API_KEY" https://gate.lava.top/api/v2/products | python3 -m json.tool
```

### Проверить scheduler работает
```bash
ssh root@151.243.113.31 'journalctl -u vpnbot --since "1 hour ago" | grep -iE "renewal|expiry|grace"'
```
