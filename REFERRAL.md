# Реферальная программа VpnBot

Полная логика: 7-day триал для рефералов + manual-redeem бонус-банк для реферрера.

> Связано: [PRICING.md](./PRICING.md) (цены) · [PAYMENTS.md](./PAYMENTS.md) (платёжная архитектура)

---

## Правила в двух строках

- **Реферал** (приведённый): пришёл по `/start ref_<id>` → получает **7 дней триала** вместо 3
- **Реферрер** (приведший, должен быть paid): за каждого реферала, купившего платную подписку, получает **+7 дней** в **бонус-банк**, активирует вручную в Mini App

---

## Cценарий 1 — стандартный happy path

```
Вася [paid] → копирует ссылку https://t.me/maxvpnesim_bot?start=ref_<вася_id>
            → шлёт Маше

Маша [новая] → кликает ссылку → /start ref_<вася_id>
            → бот проверяет:
              ✓ это не self-ref
              ✓ у Маши нет ни одной подписки (has_any_subscription = False)
              ✓ у Васи есть active или grace платная подписка (has_active_paid_sub)
            → set_referred_by(Маша, Вася)

Маша жмёт «получить триал»
            → trial_days_for(Маша) = 7 (так как referred_by != NULL)
            → подписка vpn_trial на 7 дней

Маша через 5 дней покупает «База 200₽» (Stars / Lava / Cryptomus / CryptoBot — любой метод)
            → maybe_award_referral_bonus(bot, Маша.id, sub.id) вызывается во всех 4 flows
            → try_award_referral_bonus:
              ✓ Маша.referred_by = Вася (есть)
              ✓ Вася has_active_paid_sub (или хотя бы platic когда-то)
              ✓ это первая платная Маши (paid_count = 1)
              ✓ ATOMIC CLAIM на subscriptions.ref_bonus_awarded_to IS NULL → успешен
              → Вася.ref_bonus_days += 7 (КОПИТСЯ в bank)
              → Маша.sub.ref_bonus_awarded_to = Вася.id (tracking)
              → Маша.sub.ref_bonus_days_awarded = 7
            → бот шлёт Васе: «🎁 +7 дней в твой бонус-банк! Активируй в Mini App»

Вася открывает /vpn/friends
            → видит блок «🎁 Мои бонусы: +7 дней»
            → has_active_sub = True → кнопка «Активировать +7 дней» enabled

Вася жмёт «Активировать»
            → redeem_referral_bonus(Вася.id):
              ✓ ATOMIC CAS: UPDATE users SET ref_bonus_days=0 WHERE id=Вася AND ref_bonus_days=7
                (если параллельный redeem обогнал — rowcount=0, выходим без double-extend)
              → Вася.active_sub.expires_at += 7 дней
              → Маша.sub.ref_bonus_redeemed_at = NOW (трекинг что бонус активирован)
            → бот шлёт Васе: «🎁 +7 дней активированы. Подписка до 25.06»
```

---

## Сценарий 2 — Маша поспешила и взяла триал САМА (без ссылки)

```
Маша [новая] → /start (без ref) → активирует 3-дневный триал

Маша через час получает от Васи ссылку → /start ref_<вася_id>
            → has_any_subscription(Маша) = True (есть vpn_trial sub)
            → НЕ записываем referred_by
            → шлём: «Ты уже зарегистрирован — реферальная ссылка для новых юзеров»

Маша покупает «База»
            → maybe_award_referral_bonus → Маша.referred_by = NULL → return None
            → Вася НИЧЕГО не получает
```

**Урок Маше**: надо кликать ссылку до триала. Late-ref защищает от self-абуза (юзер взял триал → попросил себя «пригласить» с второго аккаунта).

---

## Сценарий 3 — Вася сам на триале → его ссылка не работает

```
Вася [trial] → открывает /vpn/friends в Mini App
            → has_active_paid_sub(Вася) = False
            → can_refer = false в API response
            → UI показывает заглушку «Реферальная программа доступна с подпиской»
              + кнопку «Купить подписку» → /vpn/plans

Если Вася всё же скопировал ссылку через консоль:
Маша кликает /start ref_<Вася>
            → has_active_paid_sub(Вася) = False
            → SILENT skip (не записываем referred_by)
            → Маша получает обычный 3-day триал, Вася не получит бонус
```

---

## Сценарий 4 — Вася без active sub, когда Маша покупает

```
Вася купил → expired → нет активной подписки

Маша покупает «База»
            → try_award_referral_bonus:
              ✓ Вася когда-то был paid (referrer_paid_subs > 0)
              → Вася.ref_bonus_days += 7 (в bank, ждёт)
            → expires_at не extend'им (нет active sub)

Вася заходит /vpn/friends
            → видит «🎁 Мои бонусы: +7 дней»
            → has_active_sub = False → кнопка disabled
            → hint: «Бонусы применяются к активной подписке — продли чтобы активировать»

Вася покупает новую подписку → жмёт «Активировать»
            → +7 дней к новой sub
```

---

## Сценарий 5 — Refund реферала ДО redeem

```
Маша.sub.ref_bonus_redeemed_at = NULL (Вася ещё не активировал)
Вася.ref_bonus_days = 7

Маша делает refund → rollback_referral_bonus(Маша.sub):
  → видит redeemed_at = NULL → бонус ещё в bank
  → Вася.ref_bonus_days = MAX(0, 7-7) = 0
  → active sub Васи НЕ трогаем
```

---

## Сценарий 6 — Refund реферала ПОСЛЕ redeem

```
Маша.sub.ref_bonus_redeemed_at = вчера (Вася уже активировал)
Вася.ref_bonus_days = 0 (обнулили при redeem)
Вася.active_sub.expires_at = +7 days от продления

Маша делает refund → rollback_referral_bonus(Маша.sub):
  → видит redeemed_at != NULL → бонус уже сидит в active sub
  → Вася.active_sub.expires_at -= 7 дней
  → Вася.ref_bonus_days НЕ трогаем (он 0)
```

---

## Цепочка нескольких рефералов

```
Маша покупает  → Вася.bank = 7
Петя покупает  → Вася.bank = 14
Лена покупает  → Вася.bank = 21

Вася жмёт «Активировать +21 день»
  → active sub +21
  → bank = 0
  → ВСЕ tracking-subs (Маша, Петя, Лена) получают ref_bonus_redeemed_at = NOW

Если Петя потом refund:
  → rollback видит redeemed_at != NULL у его sub
  → Вася.active_sub.expires_at -= 7
  (от других не трогаем — они тоже redeemed, но не refundились)
```

---

## Что в UI «Мои бонусы»

3 состояния (показывается **всегда**, даже когда bank=0):

| Bank | Active sub | Карточка | Кнопка |
|---|---|---|---|
| 0 | любое | Серая | Disabled «Пока нечего активировать» + hint «Пригласи друга — за каждого +7 дней» |
| > 0 | ✅ | Зелёная gradient | Активная «Активировать +N дней» |
| > 0 | ❌ | Зелёная | Disabled «Купи подписку чтобы активировать» |

Для **триал-юзеров и юзеров без paid sub** — заглушка вместо ссылки и share-кнопки: «Реферальная программа доступна с подпиской» + CTA «Купить» → /vpn/plans.

---

## Шпаргалка по полям БД

| Поле | Таблица | Что |
|---|---|---|
| `referred_by` | users | ID реферрера (кто пригласил) |
| `ref_bonus_days` | users | **Bank** — pending бонус, ждёт redeem |
| `ref_bonus_awarded_to` | subscriptions | ID реферрера которому начислили (на этой sub) |
| `ref_bonus_days_awarded` | subscriptions | Сколько именно начислили (для rollback) |
| `ref_bonus_redeemed_at` | subscriptions | NULL = в bank, NOT NULL = активирован реферрером |

`schema_state` — служебная табличка для one-time миграций (запоминает что
`ref_bonus_redeem_migration_v1` уже отработал).

---

## Шпаргалка по функциям

### `bot/services/database.py`

| Функция | Когда | Что делает |
|---|---|---|
| `set_referred_by(юзер, реферрер)` | `/start ref_<id>` handler | Записывает связь, защита от self-ref + race на overwrite |
| `try_award_referral_bonus(user_id, days, paid_sub_id)` | После первой платной покупки (вызывается из всех 4 payment flows) | Atomic CLAIM на subscriptions.ref_bonus_awarded_to → +days в bank реферрера |
| `redeem_referral_bonus(user_id)` | Кнопка «Активировать» в Mini App | Atomic CAS на users.ref_bonus_days → extend active/grace sub, ставит redeemed_at |
| `rollback_referral_bonus(refunded_sub_id)` | При refund реферала (auto-detect через payment_provider) | Откатывает: bank или sub.expires_at в зависимости от redeemed_at |
| `has_active_paid_sub(user_id)` | Гейтит реф-ссылки + redeem | True если есть active/grace платная sub |
| `has_any_subscription(user_id)` | Гейтит late-ref | True если есть любая (trial/paid/expired/refunded) |
| `get_referred_by(user_id)` | Для trial_days_for | Возвращает referrer_id или None |

### `bot/services/trial.py`

| Функция | Что |
|---|---|
| `trial_days_for(user_id)` | 7 если есть referred_by, иначе 3 |
| `TRIAL_DAYS` = 3 | default |
| `TRIAL_DAYS_REFERRED` = 7 | для рефералов |

### `bot/handlers/vpn.py`

| Функция | Где вызывается |
|---|---|
| `maybe_award_referral_bonus(bot, user_id, sub_id)` | После провижининга в `_deliver_vpn` (Stars) и `handle_*_webhook` через `provision_vpn_slots_async` (CryptoBot/Cryptomus/Lava) — единая точка |

### `bot/services/webapp_api.py`

| Endpoint | Что |
|---|---|
| `GET /api/referral/stats` | `{ref_link, invited, converted, bonus_days, bonus_days_pending, can_refer, has_active_sub}` |
| `POST /api/referral/redeem` | Активация bank к active/grace sub. 400 `no_active_sub` / `no_bonus` |
| `GET /api/vpn/trial` | `{eligible, duration_days}` — duration зависит от referred_by |

---

## Race-safety inventory

| Race | Защита |
|---|---|
| Юзер двойным тапом нажимает «Активировать» | Atomic CAS: `UPDATE users SET ref_bonus_days=0 WHERE id=? AND ref_bonus_days=?` — второй UPDATE даёт rowcount=0 |
| Параллельные webhook-handlers начисляют бонус за один sub | Atomic CLAIM: `UPDATE subscriptions SET ref_bonus_awarded_to=? WHERE id=? AND ref_bonus_awarded_to IS NULL` — второй CLAIM даёт rowcount=0 |
| Два параллельных rollback'а на один refund | Atomic CLAIM: `UPDATE subscriptions SET ref_bonus_awarded_to=NULL WHERE ref_bonus_awarded_to IS NOT NULL` — второй даёт rowcount=0 |
| Multi-instance bot deploy с одной БД | Migration через INSERT-first в schema_state — IntegrityError ловится, no-op |

---

## Известные ограничения

1. **Реферрер без active sub теряет бонус при истечении bank периода**
   - Бонус копится indefinitely в bank. Юзер может никогда не купить → банк никогда не активируется. OK.

2. **Cooldown триала**
   - Один юзер один раз получает trial (cooldown 30 дней через `TRIAL_COOLDOWN_DAYS` в `trial.py`).
   - Если юзер уже использовал триал → late-ref blocked, 7-day триал не выдаётся.

3. **Self-абуз через несколько TG-аккаунтов**
   - Vasya с второго аккаунта (PetяFakeAcc) кликает ref-link и регистрируется → 7-day триал. Покупает что-нибудь дешевое → +7 дней Vasya.
   - Технически возможно. Цена защиты выше профита. Принято.

4. **Реферрер должен быть PAID — иначе круговой абуз через trial-юзеров**
   - Vasya, Petya, Lena — все на триалах. Vasya кидает ссылку Petya → Petya открывает по ссылке (но Vasya не paid → SILENT skip). Petya не получит 7-day триал, его triаl = 3 дня default. Vasya не получит бонус.

5. **Lifetime earned counter не показывается**
   - Поле `ref_bonus_days` = pending bank (после migration v1). Lifetime earned = можно вычислить как `SUM(ref_bonus_days_awarded) WHERE ref_bonus_awarded_to=user_id AND refunded=false` — но в UI не выведено.
