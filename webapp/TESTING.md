# UI test checklist — VpnBot Mini App

Пробегается за **15–20 мин** руками после каждого релиза в `webapp/**`.
Если **Smoke** падает — откатывать релиз. Остальные категории — фиксить в
течение дня.

**Где тестить:** Telegram → `@maxvpn_robot` (или dev-бот) → `/start` →
Открыть VPN-приложение.

## 🖥 Если есть только Mac (нет Android)

**90% чеклиста гонится в Chrome на Mac за 15 мин** через Telegram Web:

1. `https://web.telegram.org/k/` → логин по номеру
2. Найти бота → `/start` → кнопка «Open VPN» — Mini App открывается прямо во вкладке
3. `Cmd+Opt+I` (DevTools) → `Cmd+Shift+M` (Device Mode) → выбрать iPhone SE / Pixel 7 / Galaxy S20
4. Тестировать как обычный сайт: Console / Network / Sources с breakpoints

**Что Web НЕ покажет** (придётся открыть на iPhone после Web-прогона, ~5 мин):
- `WebApp.HapticFeedback` (silent no-op)
- Платежи Stars (нативный Telegram payment UI)
- Скачивание / открытие AWG / VLESS конфигов в Happ
- Subtle theme color differences

**Что Web ПОКАЖЕТ корректно** (это 90% чеклиста):
- Все layouts, навигация, формы
- API-запросы, JS-ошибки
- i18n RU/EN
- Light/dark theme
- Long names, маленькие экраны (через viewport switch)

**Когда нужен реальный Android:**
- Layout-баги специфичные для Android WebView (старые версии)
- Quirks с input-полями (eSIM-форма с email)
- Только перед крупным релизом, ~раз в месяц
- Через BrowserStack App Live ($5/час, real Pixel/Samsung) — без покупки устройства

**Тестовые аккаунты:**
- Главный: твой Telegram (`@Lemonov911`, admin)
- «Чистый юзер»: попроси у кого-нибудь свежий аккаунт без подписок
- Banned: `/admin ban` сам себя в dev-боте, потом разбан

---

## 🚨 Smoke (5 кейсов · ~3 мин · must-pass)

Если что-то из этого не работает — откат. Эти ломают **весь продукт**.

- [ ] **S1.** `/start` в боте → Mini App открывается, не белый экран, не 500
- [ ] **S2.** Главный экран рендерится: hero, баланс, кнопка «Купить» / «Управлять»
- [ ] **S3.** Bottom nav работает: тап по каждой вкладке (Home / VPN / Configs / eSIM / Friends) — переход без ошибок
- [ ] **S4.** Plans экран открывается, цены и кнопки видны (3 плана)
- [ ] **S5.** В Configs у активного юзера видны его конфиги (не пустой список с активной подпиской)

---

## ⭐ Core flows (10 кейсов · ~10 мин · happy path)

Основные user-сценарии. Один большой fail = блокер для релиза.

### Новый юзер

- [ ] **C1.** Чистый юзер открывает Mini App → видит «Нет подписки» + CTA «Купить»
- [ ] **C2.** Тап «Купить» → Plans. Выбор любого плана → открывается PaymentSheet с 3 методами оплаты (Stars / CryptoBot / OxaPay)
- [ ] **C3.** Получить trial (если доступен) → Home показывает active sub, Configs показывает empty slots по плану
- [ ] **C4.** В Configs тап «Активировать» на empty AWG слоте → ServerPicker → выбор страны → конфиг создаётся (config_data появляется)
- [ ] **C5.** Тап «Скачать» / «QR» на активном AWG → файл качается / QR показывается

### Действующий юзер

- [ ] **C6.** VPN-вкладка: видна текущая подписка, дата истечения, кнопка продления
- [ ] **C7.** Подписка на vpn_max: в Configs есть и AWG, и VLESS слоты соответственно плану
- [ ] **C8.** VLESS subscription URL (`/sub/{token}`) копируется по тапу + открывается в Happ
- [ ] **C9.** Revoke active slot: тап «Отозвать» → confirm popup → слот переходит в empty, peer удаляется на агенте
- [ ] **C10.** Referral: ссылка `t.me/maxvpn_robot?start=ref_<id>` копируется, статистика «приглашено» отображается

---

## 🔬 Edge cases (10 кейсов · ~5 мин · нечастые но важные)

- [ ] **E1.** **Grace state**: у юзера sub в grace (expires в прошлом, grace_until в будущем) → UI показывает «истекла, throttle 256 кбит» баннер + кнопка продления
- [ ] **E2.** **Expired sub**: статус expired, конфиги revoked → UI показывает «Нет активной подписки», старые конфиги не активны (visual подсветка)
- [ ] **E3.** **Pending downgrade**: vpn_max + pending_plan=vpn_base → UI показывает «после истечения станет Base»
- [ ] **E4.** **Double-tap** на «Активировать» в Configs → второй тап игнорируется (кнопка disabled), не создаётся два пира
- [ ] **E5.** **Server offline**: один из active configs на сервере с `is_active=0` → конфиг подсвечен красным, текст «сервер недоступен»
- [ ] **E6.** **Bot blocked**: юзер блокирует бота → reminders не шлются (проверить в логах после 3-day/1-day reminder)
- [ ] **E7.** **eSIM поток**: открыть eSIM → видны страны → выбрать → видны пакеты → не падает 500 на покупке (Stars-only)
- [ ] **E8.** **Support тикет**: написать обращение → отправляется → попадает в админку
- [ ] **E9.** **Реферальный бонус**: у юзера ref_bonus_days > 0 → на главной видна plашка «N бонусных дней» + кнопка «Активировать»
- [ ] **E10.** **Banned user**: открывает Mini App → видит сообщение «вы заблокированы», кнопки покупки не активны

---

## 🎨 Visual / UX (5 кейсов · ~2 мин)

- [ ] **V1.** **Light theme** (Telegram → Settings → Light): все экраны читаемы, нет белого текста на белом
- [ ] **V2.** **Dark theme**: то же — нет тёмного на тёмном, контраст ОК
- [ ] **V3.** **Язык**: переключить язык Telegram на EN → перезагрузить Mini App → все экраны на английском, ничего на русском не торчит
- [ ] **V4.** **Длинные имена**: юзер с длинным first_name (>20 символов) → не ломает layout (truncate работает)
- [ ] **V5.** **Маленький экран** (iPhone SE / Galaxy A10): bottom nav не перекрывает контент, все кнопки доступны

---

## 📦 Подготовка тестовых данных

Чтобы быстро воспроизвести edge-states, на dev-боте можно подделать через
SQL. **Только на dev-БД!** На проде — только `/admin`.

```sql
-- Перевести sub в grace
UPDATE subscriptions SET status='grace',
  expires_at=datetime('now','-1 days'),
  grace_until=datetime('now','+13 days')
WHERE user_id=<your_id> AND status='active';

-- Сделать sub expired
UPDATE subscriptions SET status='expired',
  expires_at=datetime('now','-1 days')
WHERE user_id=<your_id>;

-- Накинуть бонус-дни
UPDATE users SET ref_bonus_days=ref_bonus_days+30 WHERE id=<your_id>;

-- Pending downgrade
UPDATE subscriptions SET pending_plan='vpn_base'
WHERE user_id=<your_id> AND plan='vpn_max';

-- Сервер offline (один из VLESS)
UPDATE servers SET is_active=0 WHERE id=11;  -- Amsterdam VLESS
-- ⚠️ потом вернуть: UPDATE servers SET is_active=1 WHERE id=11;
```

После SQL: дёрни бот-API чтобы кеш протух:
```bash
curl -X POST https://api.maxvpnesim.com/api/internal/sub_cache/invalidate \
  -H "X-Admin-Secret: $ADMIN_API_SECRET" \
  -d '{"user_id": <your_id>}'
```

Или просто рестартни бот.

---

## ❌ Известные ограничения (не тестируем — известные баги)

Список чтобы не путаться: эти кейсы УЖЕ известны и в backlog'е, нет смысла
заводить дубли тикетов.

- VLESS traffic counter сбрасывается при рестарте Xray (`#20` в task-list)
- Server_health_log retention (зафикшено в коммите `53df730`, проверь после
  следующего deploy что таблица перестала расти)

---

## 🛠 Если кейс упал

1. Скриншот в Telegram-чат с командой
2. URL в адресной строке Mini App (если есть)
3. Tg user_id (из `WebApp.initDataUnsafe.user.id` или `/start` в боте)
4. Время с точностью до минуты
5. Логи бота: `ssh root@151.243.113.31 'journalctl -u vpnbot -n 200 | grep <user_id>'`
