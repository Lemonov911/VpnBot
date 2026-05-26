"""
Фоновый планировщик подписок.

Жизненный цикл подписки:
  active → grace (при истечении expires_at)
    AWG : tc-throttle 256 кбит/с через агент (per-peer по dst IP на awg0)
    VLESS: пир перемещается в inbound vless-grace (порт 9453, tc 256 кбит/с)
    Конфиги остаются активными — пользователь может продлить без потери ключей.

  grace → expired (при истечении grace_until = expires_at + 14 дней)
    AWG : снимается throttle, пир удаляется, слот освобождается.
    VLESS: пир удаляется из vless-grace, слот освобождается.

Также обрабатывает старые заказы из таблицы orders (backward compat).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from html import escape as html_escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from services.database import (
    get_expired_subscriptions,
    get_expired_trials,
    get_grace_expired_subscriptions,
    get_configs_for_subscription,
    mark_subscription_expired,
    mark_subscription_grace,
    ensure_grace_column,
    revoke_config,
    reset_config_slot,
    get_subscriptions_expiring_soon,
    get_subscriptions_grace_ending_soon,
    mark_reminded,
    mark_grace_reminded,
    get_expired_orders,
    mark_order_expired,
    get_server_by_id,
    get_servers_by_protocol,
    update_server_peer_count,
    update_config_traffic,
    get_config_id_by_vless_uuid_and_server,
    get_active_vless_uuids_by_server,
    get_active_vless_configs_with_plan,
    update_config_data,
    get_esim_profiles_for_usage_sync,
    update_esim_usage,
    get_winback_candidates,
    mark_winback_sent,
    get_trial_nudge_candidates,
    mark_trial_nudge_sent,
    set_quota_throttled_flag,
    get_user_lang,
)
from services.i18n_bot import t as _i18n_t
import services.esim_api as esim_api
from services.vpnctl_client import client_for_server, VpnctlError
from services.plans import (
    VPN_PLANS,
    vless_service_for_plan,
    vless_slow_service_for_plan,
    plan_display_name,
)

logger = logging.getLogger(__name__)

GRACE_DAYS = 14

CHECK_INTERVAL = 3600  # секунд (1 час)


def _bot_version() -> str:
    """Lazy lookup BOT_VERSION чтобы избежать circular import."""
    try:
        from bot import BOT_VERSION
        return BOT_VERSION
    except Exception:
        return "dev"


async def _weekly_vacuum():
    """SQLite VACUUM — дефрагментирует и сжимает bot.db.
    Без этого bot.db растёт на 5-10% в месяц (fragmentation).
    VACUUM требует brief exclusive lock, но запускается раз в неделю
    ночью — приемлемо для бота с ~10 req/s.
    incremental_vacuum(N) был no-op без auto_vacuum=INCREMENTAL.
    """
    import sqlite3 as _sqlite
    from services.database import DB_PATH
    def _vacuum_sync():
        conn = _sqlite.connect(str(DB_PATH))
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _vacuum_sync)
    logger.info("weekly VACUUM completed")

# Inline-кнопка «Продлить» во всех retention-уведомлениях. Открывает Plans
# внутри Mini App одним кликом — это разница между «продлил из дивана» и
# «забыл и ушёл к конкуренту».
_WEBAPP_URL = os.getenv("WEBAPP_URL", "")


def _renew_kb(lang: str | None = None, is_trial: bool = False) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура с deep-link на /vpn/plans в Mini App. None если WEBAPP_URL пустой.

    is_trial=True меняет CTA на «Выбрать тариф» (trial-у нечего продлевать).
    """
    if not _WEBAPP_URL:
        return None
    key = "bot_btn_choose_plan" if is_trial else "bot_btn_renew_subscription"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=_i18n_t(lang, key),
            web_app=WebAppInfo(url=f"{_WEBAPP_URL}/vpn/plans"),
        )
    ]])


# Batch-friendly send helper. Telegram global rate-limit ~30 msg/sec; если бот
# поднимается после downtime и `_process_grace_expired_subscriptions` находит
# 80 истёкших подписок, без задержки попадёт в flood-control 429 и заблокируется
# временно. ~25/sec = безопасный потолок.
_TG_SEND_DELAY = 0.04  # 25 msg/sec


async def _send_throttled(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    """Шлёт сообщение с защитой от flood-control. Возвращает True если успешно.

    TelegramForbiddenError (юзер заблокировал бота / удалил аккаунт) → ставит
    users.bot_blocked_at чтобы дальнейший scheduler не спамил, и recurring
    auto-renewal паузил продление.
    """
    from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
    try:
        await bot.send_message(user_id, text, **kwargs)
        await asyncio.sleep(_TG_SEND_DELAY)
        return True
    except TelegramForbiddenError:
        from services.database import mark_user_bot_blocked
        try:
            await mark_user_bot_blocked(user_id)
        except Exception as mb_err:
            logger.warning("mark_user_bot_blocked failed user=%d: %s", user_id, mb_err)
        logger.info("user %d blocked bot → marked, skip notify", user_id)
        return False
    except TelegramRetryAfter as e:
        logger.warning("TG flood control: sleep %ds then retry user=%d", e.retry_after, user_id, exc_info=True)
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(user_id, text, **kwargs)
            return True
        except TelegramForbiddenError:
            from services.database import mark_user_bot_blocked
            try:
                await mark_user_bot_blocked(user_id)
            except Exception:
                pass
            return False
        except Exception as retry_err:
            logger.warning("TG retry failed user=%d: %s", user_id, retry_err, exc_info=True)
            return False
    except Exception as e:
        logger.warning("send_message failed user=%d: %s", user_id, e, exc_info=True)
        return False


# _current_vless_service вынесен в services.revoke.current_vless_service —
# чтобы scheduler grace-loop и refund/recovery handlers использовали единое
# определение текущего tier VLESS-конфига (audit C1: дубль был в 2 местах).
from services.revoke import current_vless_service as _current_vless_service  # noqa: E402,F401


async def _process_expired_subscriptions(bot: Bot):
    """Переводит истёкшие подписки в grace-период (14 дней при 256 кбит/с).

    AWG  — применяет tc-throttle через агент (per-peer по assigned_ip на awg0).
    VLESS — перемещает пир в inbound vless-grace (порт 9453, tc 256 кбит/с).
    Конфиги остаются активными; пользователь может продлить без потери ключей.
    """
    expired_subs = await get_expired_subscriptions()
    if not expired_subs:
        return

    logger.info("Найдено истёкших подписок: %d", len(expired_subs))
    grace_until = (datetime.utcnow() + timedelta(days=GRACE_DAYS)).isoformat()
    # Если бот лежал >GRACE_DAYS, sub'а expired дольше grace_period →
    # переход в expired сразу, без grace 256 кбит/с. Иначе юзер получит
    # одно за другим уведомления «grace» и «expired» за час, а реально
    # сервис всё это время был недоступен.
    cutoff_expired_long_ago_dt = datetime.utcnow() - timedelta(days=GRACE_DAYS)

    for sub in expired_subs:
        sub_id   = sub["id"]
        user_id  = sub["user_id"]
        plan_key = sub.get("plan", "")

        # Bot-offline guard: если sub.expires_at < (now - GRACE_DAYS), значит
        # grace window уже истёк → пропускаем grace transition, сразу к expired.
        sub_expires = sub.get("expires_at") or ""
        sub_expires_dt = None
        if sub_expires:
            # expires_at может быть либо ISO с 'T' (Python isoformat), либо с
            # пробелом (после SQL datetime(...)). Лексикографическое сравнение
            # неверно ('T' > ' '), так что парсим в datetime.
            try:
                normalized = sub_expires.replace("T", " ").split(".")[0]
                sub_expires_dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                sub_expires_dt = None
        if sub_expires_dt is not None and sub_expires_dt < cutoff_expired_long_ago_dt:
            logger.info(
                "Подписка #%d: expires_at=%s давно истекло (>%d дней), "
                "пропускаем grace → expired",
                sub_id, sub_expires[:10], GRACE_DAYS,
            )
            try:
                # Revoke AWG/VLESS peers на агентах ДО mark_expired.
                # Sub никогда не проходила через grace (бот лежал) — peers
                # полностью активны на агенте и будут работать бесплатно
                # если их не удалить.
                from services.revoke import revoke_subscription_configs
                await revoke_subscription_configs(
                    sub_id, plan_key, log_prefix=f"late-expire sub#{sub_id}",
                )
                await mark_subscription_expired(sub_id)
                # Audit F5: cache invalidate чтобы Mini App сразу увидел expired.
                from services.sub_cache import invalidate as _inv_sub_cache
                _inv_sub_cache(user_id)
                # Уведомление юзеру
                _lang = await get_user_lang(user_id)
                await _send_throttled(
                    bot, user_id,
                    _i18n_t(_lang, "bot_expiry_notice"),
                    parse_mode="HTML",
                    reply_markup=_renew_kb(_lang),
                )
            except Exception as e:
                logger.warning("late-expire sub #%d: %s", sub_id, e, exc_info=True)
            continue

        # Применяем pending downgrade ДО throttle/grace, чтобы:
        #  1) revoke лишних configs прошёл до throttle — throttle-loop ниже
        #     не делает лишних tc-операций над пирами, которые тут же будут
        #     удалены (cleaner + дешевле по агентским RTT).
        #  2) Crash-safe ordering: сначала revoke (идемпотентен — пустые слоты
        #     no-op), потом apply_pending (тоже идемпотентен — UPDATE WHERE
        #     pending_plan=?). Если бот упадёт между revoke и apply,
        #     pending_plan ещё стоит → на следующей итерации обе операции
        #     выполнятся снова. Раньше порядок был apply → revoke: при крэше
        #     между ними pending_plan сбрасывался, и лишние configs «висели»
        #     активными на новом тарифе.
        pending = sub.get("pending_plan")
        if pending and pending != plan_key:
            try:
                # 1) Revoke лишних configs (old_count - new_count) для каждого
                #    протокола. revoke_excess_configs_on_downgrade сама вычисляет
                #    дельту по VPN_PLANS; пустые слоты — no-op.
                try:
                    from services.revoke import revoke_excess_configs_on_downgrade
                    rev, fail = await revoke_excess_configs_on_downgrade(
                        sub_id,
                        old_plan_key=plan_key,
                        new_plan_key=pending,
                        log_prefix=f"pending_downgrade_sub{sub_id}",
                    )
                    logger.info(
                        "Подписка #%d pending-downgrade excess-revoke: "
                        "revoked=%d failed=%d",
                        sub_id, rev, fail,
                    )
                except Exception as e:
                    logger.warning(
                        "Подписка #%d: excess-revoke before downgrade failed: %s",
                        sub_id, e, exc_info=True,
                    )

                # 2) Apply pending plan change. WHERE pending_plan=? защищает
                #    от race с параллельным upgrade (тогда no-op).
                from services.database import (
                    apply_pending_plan_change, ensure_empty_slots_match_plan,
                )
                applied = await apply_pending_plan_change(sub_id, pending)
                if applied:
                    logger.info(
                        "Подписка #%d: применён pending downgrade %s → %s",
                        sub_id, plan_key, applied,
                    )
                    plan_key = applied  # throttle-loop ниже работает с новым планом
                    # 3) Upgrade-pending: новый план может иметь БОЛЬШЕ слотов
                    # чем revoke_excess затронул. Достраиваем missing empty
                    # слоты — иначе юзер на новом тарифе видит меньше слотов,
                    # чем заявлено (платит за vpn_max, получает 2+1 вместо 3+5).
                    try:
                        added = await ensure_empty_slots_match_plan(
                            sub_id, sub["user_id"], applied,
                        )
                        if added:
                            logger.info(
                                "Подписка #%d: после pending-upgrade добавлены empty слоты: %s",
                                sub_id, added,
                            )
                    except Exception as e:
                        logger.warning(
                            "Подписка #%d: ensure_empty_slots_match_plan failed: %s",
                            sub_id, e, exc_info=True,
                        )
                else:
                    logger.info(
                        "Подписка #%d: pending downgrade no-op (race? pending уже NULL)",
                        sub_id,
                    )
            except Exception as e:
                logger.warning(
                    "Подписка #%d: pending downgrade failed (%s → %s): %s",
                    sub_id, plan_key, pending, e, exc_info=True,
                )

        configs = await get_configs_for_subscription(sub_id)
        logger.info("Подписка #%d: переводим %d конфиг(ов) в grace", sub_id, len(configs))

        for cfg in configs:
            server_id  = cfg.get("server_id")
            protocol   = cfg.get("protocol", "")
            cfg_id     = cfg["id"]
            peer_name  = cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            vless_uuid = cfg.get("vless_uuid") or ""

            if not server_id:
                continue
            server = await get_server_by_id(server_id)
            if not server or not server.get("agent_url"):
                continue

            try:
                client = client_for_server(server)

                if protocol == "awg":
                    # tc-throttle: ограничиваем пир на awg0 по dst IP
                    if assigned_ip and peer_name:
                        await client.throttle_peer("awg", peer_name, assigned_ip, kbps=1024)
                        logger.info("AWG конфиг #%d throttled 1Mbit (sub=%d)", cfg_id, sub_id)
                    else:
                        # Data drift: assigned_ip пустой → throttle невозможен
                        # без него (tc нужен dst IP для фильтра).  Без alert'а
                        # юзер получит full speed бесплатно 14 дней grace.
                        # Audit 17.05 поймал — раньше silently skipped.
                        logger.error(
                            "AWG cfg #%d (sub=%d) cannot throttle: "
                            "assigned_ip=%r peer_name=%r — admin must check data drift",
                            cfg_id, sub_id, assigned_ip, peer_name,
                        )
                        try:
                            from config import ADMIN_ID
                            if ADMIN_ID and bot is not None:
                                await bot.send_message(
                                    ADMIN_ID,
                                    f"⚠️ <b>AWG grace throttle SKIPPED</b>\n\n"
                                    f"cfg #{cfg_id} sub #{sub_id} — assigned_ip="
                                    f"<code>{html_escape(assigned_ip or 'NULL')}</code>, "
                                    f"peer_name=<code>{html_escape(peer_name or 'NULL')}</code>\n\n"
                                    f"Юзер сейчас на full speed в grace. "
                                    f"Найди и пофикси data drift вручную.",
                                    parse_mode="HTML",
                                )
                        except Exception:
                            pass  # admin alert — best effort

                elif protocol in ("vless", "vless-reality"):
                    # Перемещаем в grace inbound (порт 9453, tc 256 кбит/с).
                    # Атомарность: если add_peer succeeded но remove_peer упал,
                    # юзер окажется в ДВУХ inbound одновременно (двойной трафик
                    # на одном UUID — Xray молча примет первый коннект). Делаем
                    # compensating remove на vless-grace чтобы откатить add.
                    if vless_uuid:
                        config_data = cfg.get("config_data") or ""
                        current_svc = _current_vless_service(config_data, plan_key)
                        if current_svc != "vless-grace":
                            label = f"grace_{sub_id}_{cfg_id}"
                            grace_added = False
                            try:
                                grace_peer = await client.add_peer(
                                    "vless-grace", label, peer_id=vless_uuid
                                )
                                grace_added = True
                                await client.remove_peer(current_svc, vless_uuid)
                                if grace_peer.config:
                                    await update_config_data(cfg_id, grace_peer.config)
                                logger.info(
                                    "VLESS конфиг #%d → vless-grace (был: %s, sub=%d)",
                                    cfg_id, current_svc, sub_id,
                                )
                            except VpnctlError as e:
                                logger.warning(
                                    "VLESS grace move failed cfg #%d (added=%s): %s",
                                    cfg_id, grace_added, e, exc_info=True,
                                )
                                # Compensating remove: если grace_added=True но
                                # remove_peer на старом inbound упал, чистим
                                # vless-grace чтобы не было двух пиров на одном UUID.
                                if grace_added:
                                    try:
                                        await client.remove_peer("vless-grace", vless_uuid)
                                        logger.info(
                                            "VLESS cfg #%d: compensating remove из vless-grace выполнен",
                                            cfg_id,
                                        )
                                    except Exception as cleanup_err:
                                        logger.error(
                                            "VLESS cfg #%d: compensating remove FAILED — пир в двух inbound, нужен ручной фикс: %s",
                                            cfg_id, cleanup_err, exc_info=True,
                                        )
                                        try:
                                            from config import ADMIN_ID
                                            await bot.send_message(
                                                ADMIN_ID,
                                                f"⚠️ <b>VLESS split-brain</b>\n\n"
                                                f"Cfg #{cfg_id}: пир одновременно в двух inbound.\n"
                                                f"Нужен ручной фикс на сервере.\n\n"
                                                f"<code>{html_escape(str(cleanup_err))}</code>",
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            pass

            except Exception as e:
                logger.warning("grace throttle error cfg #%d: %s", cfg_id, e, exc_info=True)

        # NB: pending downgrade применяется ВЫШЕ — до throttle-loop'а, чтобы
        # revoke лишних слотов прошёл первым (crash-safe ordering).
        #
        # ⚠️ Ordering invariant: throttle-loop ПЕРЕД mark_subscription_grace.
        # CAS внутри mark_grace позволяет атомарно откатить throttle если sub
        # был продлён recurring webhook'ом за время throttle-loop'а
        # (см. rollback path ниже). Перестановка mark_grace вверх дала бы
        # короткое окно "status=grace + peers ещё в normal inbound" →
        # /sub/{token} вернул бы vless-grace URLs где пиров нет → Happ
        # connection-fail. Лучше 1-5 сек free-speed чем 1-5 сек disconnect.
        # Crash mid-throttle: следующий scheduler-tick (1h) re-обработает sub
        # (всё idempotent — revoke/throttle/apply_pending), inconsistency
        # window ограничен 1h в худшем случае.
        transitioned = await mark_subscription_grace(sub_id, grace_until)
        if transitioned:
            # Audit F5: invalidate cache чтобы Mini App быстрее показал grace-banner.
            from services.sub_cache import invalidate as _inv_sub_cache
            _inv_sub_cache(user_id)
        if not transitioned:
            # Race: status уже не active ИЛИ expires_at был отодвинут в
            # будущее пока мы throttle'или peers. Возможные сценарии:
            #   1) recurring webhook / admin grant продлил sub → status='active',
            #      expires_at в будущем — нужен unthrottle, paying user не должен
            #      висеть на 256 кбит/с.
            #   2) refund прошёл → status='refunded', configs reset'нуты в empty.
            #      unthrottle всё равно полезен (tc filter мог остаться по IP
            #      несмотря на пустой config_data); unthrottle_sub_configs
            #      идемпотентен и просто no-op для отсутствующих peers.
            # Логируем отдельно чтобы post-mortem различал «забыл unthrottle
            # после refund» vs «paying user залип на medium speed».
            from services.database import get_subscription_by_id
            current_sub = await get_subscription_by_id(sub_id)
            cur_status = current_sub.get("status") if current_sub else "missing"
            if cur_status == "refunded":
                logger.info(
                    "Race: sub %d refunded during throttle, attempting idempotent unthrottle "
                    "(configs уже отозваны refund-flow, но tc-filter мог остаться)",
                    sub_id,
                )
            else:
                logger.warning(
                    "Race detected: sub %d no longer active after throttle "
                    "(status=%s), rolling back",
                    sub_id, cur_status,
                )
            # Await вместо _spawn_bg: если unthrottle падает (агент 500/timeout),
            # мы должны увидеть это в логах и alert'нуть админа. Fire-and-forget
            # терял ошибки → paying user мог зависнуть на slow speed навсегда
            # (scheduler уже не возьмёт sub: expires_at в будущем, status=active).
            from services.grace import unthrottle_sub_configs
            try:
                await unthrottle_sub_configs(sub_id, user_id, plan_key)
            except Exception as e:
                logger.error(
                    "rollback unthrottle sub #%d FAILED: %s — user may be stuck on slow tier",
                    sub_id, e, exc_info=True,
                )
                try:
                    from config import ADMIN_ID
                    if ADMIN_ID and bot is not None:
                        await bot.send_message(
                            ADMIN_ID,
                            f"⚠️ <b>Throttle rollback failed</b>\n\n"
                            f"sub #{sub_id} user {user_id}: paying user остался "
                            f"на slow tier. Ручная проверка throttle на агенте.\n\n"
                            f"<code>{html_escape(str(e))}</code>",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass
            continue
        logger.info("Подписка #%d → grace (до %s)", sub_id, grace_until[:10])

        _lang = await get_user_lang(user_id)
        await _send_throttled(
            bot, user_id, _i18n_t(_lang, "bot_grace_notice"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )


async def _process_grace_expired_subscriptions(bot: Bot):
    """Окончательно отзывает конфиги, у которых истёк grace-период.

    AWG  — снимает tc-throttle, удаляет пир, освобождает слот.
    VLESS — удаляет пир из vless-grace inbound, освобождает слот.

    Race protection: между snapshot'ом `grace_subs` и началом revoke юзер
    может заплатить и `try_renew_from_grace` атомарно перевёл sub в active.
    Atomic UPDATE в БД сам по себе НЕ останавливает наш скедулер — поэтому
    делаем явный re-check `subscriptions.status` ДО revoke каждого config'а.
    Audit 17.05 #2: без re-check'a юзер платил → DB renew OK → 10 сек спустя
    скедулер revoke'ал все его VLESS peers, юзер видел «отвал» сразу после
    оплаты.
    """
    from services.database import get_subscription_by_id
    grace_subs = await get_grace_expired_subscriptions()
    if not grace_subs:
        return

    logger.info("Grace-период истёк: %d подписок", len(grace_subs))

    for sub in grace_subs:
        sub_id   = sub["id"]
        user_id  = sub["user_id"]
        plan_key = sub.get("plan", "")

        # Атомарно помечаем expired ДО того как трогать конфиги.
        # Если юзер только что заплатил и renew-from-grace перевёл sub в active,
        # этот UPDATE вернёт rowcount=0 → пропускаем, не ревокаем ни один конфиг.
        # Старая схема (re-check внутри цикла + abort mid-loop) создавала окно:
        # конфиги 0..K-1 уже отозваны с агента + сброшены в empty, а K+ — нет,
        # при этом подписка уже active → юзер заплатил, но часть слотов пуста.
        from services.database import mark_subscription_expired_from_grace
        if not await mark_subscription_expired_from_grace(sub_id):
            logger.info(
                "sub #%d skipped grace-expiry — уже не grace (race с renew-from-grace)",
                sub_id,
            )
            continue
        # Audit F5: cache invalidate — grace→expired.
        from services.sub_cache import invalidate as _inv_sub_cache
        _inv_sub_cache(user_id)

        configs = await get_configs_for_subscription(sub_id)

        for cfg in configs:
            server_id   = cfg.get("server_id")
            protocol    = cfg.get("protocol", "")
            cfg_id      = cfg["id"]
            peer_name   = cfg.get("peer_name") or ""
            assigned_ip = cfg.get("assigned_ip") or ""
            vless_uuid  = cfg.get("vless_uuid") or ""

            # revoke_ok / agent_attempted — флаги для решения reset_config_slot ниже.
            # До 2026-05-23 reset дёргался безусловно — даже при failed remove_peer
            # на агенте (5xx/timeout). БД помечала slot empty + sub expired,
            # peer оставался в kernel/xray-config (AWG-sync из бота не делается,
            # см. _sync_vless_active_uuids = только VLESS). Это и был основной
            # источник legacy `imported` peer-ов на Amsterdam — мёртвые подписки,
            # у которых grace-revoke упал. Retry-механизм для оставшихся active
            # configs — `_process_orphan_active_configs()` ниже в файле, идёт
            # каждый tick (`SELECT WHERE configs.status='active' AND
            # subscriptions.status='expired'`). Идемпотентен: повторный remove_peer
            # на уже-удалённый peer возвращает 404 — vpnctl_client трактует как OK.
            revoke_ok = True
            agent_attempted = False

            if server_id:
                server = await get_server_by_id(server_id)
                if server and server.get("agent_url"):
                    agent_attempted = True
                    try:
                        client = client_for_server(server)

                        if protocol == "awg":
                            if assigned_ip and peer_name:
                                try:
                                    await client.unthrottle_peer("awg", peer_name, assigned_ip)
                                except VpnctlError as unthrottle_err:
                                    logger.warning(
                                        "AWG unthrottle failed cfg #%d (continuing to remove): %s",
                                        cfg_id, unthrottle_err,
                                    )
                            if peer_name:
                                # Conditional counter dec: 404 (peer уже не было) → counter
                                # уже корректный, не декрементим повторно. См. remove_peer
                                # bool-return + audit 2026-05-23 о double-decrement.
                                if await client.remove_peer("awg", peer_name):
                                    await update_server_peer_count(server_id, -1)

                        elif protocol in ("vless", "vless-reality"):
                            if vless_uuid:
                                config_data = cfg.get("config_data") or ""
                                svc = _current_vless_service(config_data, plan_key)
                                if await client.remove_peer(svc, vless_uuid):
                                    await update_server_peer_count(server_id, -1)

                    except Exception as e:
                        logger.warning("revoke grace cfg #%d: %s — slot оставлен active для retry", cfg_id, e, exc_info=True)
                        revoke_ok = False

            # Решение что делать со slot'ом в БД зависит от того, могли
            # ли мы вообще достучаться до агента:
            #
            # * agent_attempted=True, revoke_ok=False — попробовали и
            #   упали (5xx/timeout/network). Peer мог остаться на агенте.
            #   НЕ reset'им — `_process_orphan_active_configs` подберёт и
            #   retry'нет на следующих тиках. Без этого reaper'а раньше
            #   orphan висел вечно (КРИТ #1 в audit 2026-05-23).
            #
            # * server_id задан, но server.agent_url пуст / нет server-
            #   row — DB inconsistency (пропущенная миграция или ручное
            #   изменение). Тоже НЕ reset'им: возможно peer есть на
            #   реальной железке, но мы её не знаем. Логируем error +
            #   ждём ручного вмешательства / orphan-reaper'а при
            #   восстановлении agent_url.
            #
            # * server_id IS NULL — legacy configs (например SSH-
            #   provisioned до agent-эпохи). Агент в принципе недоступен,
            #   peer мы оттуда не вычистим. Reset'им — нет другого пути.
            if agent_attempted and not revoke_ok:
                logger.warning(
                    "Конфиг #%d — slot НЕ сброшен (agent revoke failed, retry next tick)",
                    cfg_id,
                )
                continue
            if server_id and not agent_attempted:
                logger.error(
                    "Конфиг #%d — server_id=%s но agent недостижим (нет row/agent_url) — "
                    "slot НЕ сброшен, ждём admin / orphan-reaper",
                    cfg_id, server_id,
                )
                continue

            await reset_config_slot(cfg_id)
            logger.info("Конфиг #%d отозван (grace истёк, sub=%d)", cfg_id, sub_id)

        logger.info("Подписка #%d → expired (post-grace)", sub_id)
        _lang = await get_user_lang(user_id)
        await _send_throttled(
            bot, user_id, _i18n_t(_lang, "bot_expiry_notice"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )


async def _process_orphan_active_configs():
    """Retry-механизм для configs застрявших active под expired-sub.

    Возникают когда grace-loop пометил sub expired атомарно (race против
    renew-from-grace) и затем revoke peer на агенте упал (5xx/timeout).
    На след. тике этот reaper подбирает orphan'ы и retry'нет.

    Idempotent через `remove_peer→bool`: на повторе агент возвращает 404,
    counter НЕ декрементим (был сделан в grace-loop при первом успехе).

    Storm-protection: если первая попытка по серверу X упала с
    `VpnctlError` (timeout/network), остальные orphan'ы на этом server_id
    пропускаются в этом тике (`dead_servers` set). Иначе 100 cfg × 30s
    timeout = 50 мин wall-clock → `_safe(timeout=180)` обрежет mid-loop.
    """
    from services.database import get_orphan_active_configs_for_expired_subs
    configs = await get_orphan_active_configs_for_expired_subs(limit=200)
    if not configs:
        return

    # Pre-fetch unique servers (N+1 fix): 200 cfg × 3 уникальных server_id
    # = 3 SELECT вместо 200. server_id=None — legacy, не fetch'им.
    unique_ids = {c["server_id"] for c in configs if c.get("server_id")}
    servers_by_id: dict[int, dict] = {}
    for sid in unique_ids:
        srv = await get_server_by_id(sid)
        if srv:
            servers_by_id[sid] = srv

    logger.info("orphan-reaper: %d configs застряли active под expired-sub", len(configs))

    dead_servers: set[int] = set()
    revoked = 0
    skipped_dead = 0
    skipped_legacy = 0

    for cfg in configs:
        cfg_id      = cfg["id"]
        sub_id      = cfg["subscription_id"]
        server_id   = cfg.get("server_id")
        protocol    = cfg.get("protocol", "")
        peer_name   = cfg.get("peer_name") or ""
        assigned_ip = cfg.get("assigned_ip") or ""
        vless_uuid  = cfg.get("vless_uuid") or ""
        plan_key    = cfg.get("plan_key") or ""

        if not server_id:
            # Legacy SSH-provisioned config (до agent-эпохи). Раньше
            # делали `reset_config_slot` → создавали reverse-orphan на
            # железке (peer есть, БД чистая). Теперь НЕ reset'им —
            # error-лог, чтобы admin видел и мог сходить вручную.
            # Реальных таких configs в проде уже не должно остаться;
            # если появляются — это signal что миграция куда-то делась.
            skipped_legacy += 1
            logger.error(
                "orphan-reaper cfg #%d: server_id IS NULL (legacy SSH config?) — slot НЕ сброшен, "
                "иначе peer повиснет на неучтённой железке. sub=%d",
                cfg_id, sub_id,
            )
            continue

        if server_id in dead_servers:
            skipped_dead += 1
            continue

        server = servers_by_id.get(server_id)
        if not server or not server.get("agent_url"):
            logger.warning(
                "orphan-reaper cfg #%d: server_id=%s но row/agent_url отсутствуют — пропуск",
                cfg_id, server_id,
            )
            continue

        try:
            client = client_for_server(server)
            if protocol == "awg":
                if assigned_ip and peer_name:
                    try:
                        await client.unthrottle_peer("awg", peer_name, assigned_ip)
                    except VpnctlError as ue:
                        logger.debug("orphan-reaper cfg #%d unthrottle skipped: %s", cfg_id, ue)
                if peer_name:
                    # 404 → peer уже удалён ранее (grace-loop успел до crash'а
                    # на reset_config_slot). Counter уже декрементирован — НЕ
                    # повторяем (double-decrement bug, audit 2026-05-23).
                    if await client.remove_peer("awg", peer_name):
                        await update_server_peer_count(server_id, -1)
            elif protocol in ("vless", "vless-reality"):
                if vless_uuid:
                    config_data = cfg.get("config_data") or ""
                    svc = _current_vless_service(config_data, plan_key)
                    if await client.remove_peer(svc, vless_uuid):
                        await update_server_peer_count(server_id, -1)
        except VpnctlError as e:
            # Network/timeout/5xx — пометить server как dead в этом тике,
            # пропустить остальные orphan'ы на нём, retry на след. тике.
            dead_servers.add(server_id)
            logger.warning(
                "orphan-reaper cfg #%d server=%d down (%s) — skip остальные orphan'ы на этом сервере в этом тике",
                cfg_id, server_id, e,
            )
            continue
        except Exception as e:
            logger.warning(
                "orphan-reaper cfg #%d (%s on srv=%s): %s — повторим в следующем тике",
                cfg_id, protocol, server_id, e,
            )
            continue

        await reset_config_slot(cfg_id)
        revoked += 1

    logger.info(
        "orphan-reaper: revoked=%d, skipped_dead_servers=%d, skipped_legacy=%d, total=%d",
        revoked, skipped_dead, skipped_legacy, len(configs),
    )


async def _reconcile_partial_refunds(bot: Bot) -> None:
    """Catch-up handler для refund'ов, упавших в середине каскада.

    Telegram money refund (Stars / CryptoBot / Lava) — необратимая
    операция. Если бот упал между этим шагом и DB/agent cleanup —
    sub остаётся в inconsistent state:
      - Case A: refunded_at заполнен, но status != 'refunded' (БД не
        обновили) → юзер юридически refund'нут, но в нашей системе
        выглядит активным.
      - Case B: status='refunded', но configs всё ещё 'active'/'activating'
        → юзер деньги вернул и продолжает пользоваться VPN бесплатно.

    Этот таск сканирует обе категории и доводит cleanup до конца.
    Идемпотентен: если sub уже в нужном состоянии — get_partial_refunds
    её просто не вернёт.
    """
    from services.database import (
        get_partial_refunds,
        mark_subscription_refunded,
        mark_subscription_trial_rolled_back,
        rollback_referral_bonus,
        disable_auto_renew,
    )
    from services.revoke import revoke_subscription_configs
    from config import LAVATOP_API_KEY

    subs = await get_partial_refunds()
    if not subs:
        return

    logger.warning("Reconciling %d partial refund(s)", len(subs))

    for sub in subs:
        sub_id = sub["id"]
        try:
            # Case A: money refunded but DB inconsistent — finish marking refunded
            if sub["status"] not in ("refunded", "expired"):
                logger.warning(
                    "Reconcile sub=%d: refunded_at set but status=%s, marking refunded now",
                    sub_id, sub["status"],
                )
                payment_id = sub.get("payment_id") or ""
                if payment_id.startswith("trial_"):
                    await mark_subscription_trial_rolled_back(sub_id)
                else:
                    await mark_subscription_refunded(sub_id)
                # Audit F5: cache invalidate after refund reconcile.
                from services.sub_cache import invalidate as _inv_sub_cache
                _inv_sub_cache(sub["user_id"])
                await rollback_referral_bonus(sub_id)

                # Lava cancel: если recurring, отвязываем контракт чтобы
                # webhook не воскресил sub через extend.
                if sub.get("auto_renew") and sub.get("payment_provider") == "lavatop":
                    await disable_auto_renew(sub_id)
                    if sub.get("parent_contract_id") and LAVATOP_API_KEY:
                        from services.lavatop import cancel_subscription as _lava_cancel
                        try:
                            await _lava_cancel(
                                api_key=LAVATOP_API_KEY,
                                contract_id=sub["parent_contract_id"],
                            )
                        except Exception as e:
                            logger.warning("reconcile Lava cancel sub=%d: %s", sub_id, e)

            # Case B (и продолжение A): revoke ещё активных configs.
            if sub.get("active_config_count", 0) > 0:
                logger.warning(
                    "Reconcile sub=%d: %d configs still active, revoking",
                    sub_id, sub["active_config_count"],
                )
                revoked, failed = await revoke_subscription_configs(
                    sub_id, sub["plan"], log_prefix=f"reconcile_refund#{sub_id}",
                )
                logger.info(
                    "Reconcile sub=%d: revoked %d, failed %d",
                    sub_id, revoked, failed,
                )

            # Admin alert — partial refund reconciled.
            try:
                from config import ADMIN_ID
                if ADMIN_ID:
                    await bot.send_message(
                        ADMIN_ID,
                        f"♻️ <b>Partial refund reconciled</b>\n\n"
                        f"Sub: #{sub_id}\n"
                        f"User: <code>{sub['user_id']}</code>\n"
                        f"Plan: {html_escape(str(sub['plan']))}\n"
                        f"Status was: <code>{html_escape(str(sub['status']))}</code>\n"
                        f"Active configs cleaned: {sub.get('active_config_count', 0)}\n\n"
                        "Likely cause: bot crashed mid-refund.",
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.warning("reconcile admin alert sub=%d: %s", sub_id, e)
        except Exception as e:
            logger.error(
                "Reconcile partial refund sub=%d failed: %s",
                sub_id, e, exc_info=True,
            )


async def _process_expired_orders(bot: Bot):
    """
    Обрабатывает истёкшие заказы из старой таблицы orders.
    Оставлено для backward compatibility с заказами до рефакторинга.
    """
    expired = await get_expired_orders()
    if not expired:
        return

    logger.info("Найдено истёкших orders (legacy): %d", len(expired))

    for order in expired:
        order_id     = order["id"]
        user_id      = order["user_id"]
        vpn_username = order["vpn_username"]

        if vpn_username:
            pass  # legacy SSH — больше не используем

        await mark_order_expired(order_id)
        logger.info("Order #%d истёк, пир удалён: %s", order_id, vpn_username)

        _lang = await get_user_lang(user_id)
        await _send_throttled(
            bot, user_id, _i18n_t(_lang, "bot_expiry_notice"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )


# Триал-клоуз notice — не «продли», т.к. триал был бесплатный. Главное
# CTA — выбор постоянного тарифа через inline-кнопку под сообщением
# (см. _renew_kb()), не текстом /start. Текст живёт в i18n_bot.t() как
# bot_trial_expiry_notice.


async def _process_expired_trials(bot: Bot):
    """Полностью закрывает истёкшие trial-подписки.

    Trial-subscriptions ОТЛИЧАЮТСЯ от платных:
      - НЕ переходят в grace (256 кбит/с) — они бесплатные, нет смысла удерживать
        медленным tier'ом; нужно сразу освободить ёмкость серверов.
      - revoke сразу: peer удаляется, slot reset, status='expired'.

    Раньше `get_expired_subscriptions` фильтровала `plan != 'vpn_trial'` —
    scheduler никогда не подбирал истёкшие триалы → они висели как active
    после expires_at → бесплатный безлимит VPN.  Audit поймал.

    Locking: используем `_trial_close_lock(user_id)` из handlers/vpn.py — тот же
    что и при paid-purchase close, чтобы scheduler и _close_trial_on_paid_purchase
    не гонялись за одной триал-sub'ой.

    NB: для VLESS-revoke используем `current_vless_service(...)` — она знает,
    что для plan_key='vpn_trial' пиры живут на vless-base (см. Fix #2 в revoke.py).
    """
    expired_trials = await get_expired_trials()
    if not expired_trials:
        return

    logger.info("Найдено истёкших триалов: %d", len(expired_trials))

    # Lazy import — handlers/vpn.py импортирует services.scheduler косвенно
    # через chain, top-level import создал бы цикл.
    from handlers.vpn import _trial_close_lock
    from services.database import get_active_subscription_by_id

    for sub in expired_trials:
        sub_id  = sub["id"]
        user_id = sub["user_id"]

        async with _trial_close_lock(user_id):
            # Re-check: paid-purchase close мог уже отметить sub expired
            # пока мы ждали lock (тоже под этим же lock-ом). Если status
            # уже не active — нечего чистить.
            sub_now = await get_active_subscription_by_id(sub_id)
            if not sub_now or sub_now.get("status") != "active":
                logger.info("trial expiry skip sub=%d: status уже %s",
                            sub_id, sub_now.get("status") if sub_now else "deleted")
                continue

            configs = await get_configs_for_subscription(sub_id)
            for cfg in configs:
                server_id = cfg.get("server_id")
                cfg_id    = cfg["id"]
                if server_id:
                    server = await get_server_by_id(server_id)
                    if server and server.get("agent_url"):
                        try:
                            client = client_for_server(server)
                            proto = cfg.get("protocol", "")
                            peer_id = cfg.get("vless_uuid") or cfg.get("peer_name") or ""
                            config_data = cfg.get("config_data") or ""
                            if peer_id:
                                # Conditional decrement (audit 2026-05-23 round-3): peer
                                # мог быть удалён ранее vpn.py:1077 (trial close on paid
                                # purchase) — 404 → НЕ декрементим повторно.
                                removed = False
                                if proto == "awg":
                                    removed = await client.remove_peer("awg", peer_id)
                                elif proto in ("vless", "vless-reality"):
                                    svc = _current_vless_service(config_data, "vpn_trial")
                                    removed = await client.remove_peer(svc, peer_id)
                                if removed:
                                    await update_server_peer_count(server_id, -1)
                        except Exception as e:
                            logger.warning(
                                "trial expiry: revoke cfg #%d failed: %s",
                                cfg_id, e, exc_info=True,
                            )
                await reset_config_slot(cfg_id)

            await mark_subscription_expired(sub_id)
            # Audit F5: cache invalidate — trial expired.
            from services.sub_cache import invalidate as _inv_sub_cache
            _inv_sub_cache(user_id)
            logger.info("Триал #%d → expired (user=%d)", sub_id, user_id)

            _lang = await get_user_lang(user_id)
            await _send_throttled(
                bot, user_id, _i18n_t(_lang, "bot_trial_expiry_notice"),
                parse_mode="HTML",
                reply_markup=_renew_kb(_lang, is_trial=True),
            )


async def _sync_vless_stats():
    """Pulls per-user traffic stats from each VLESS server's vpnctl agent
    and writes them to the configs table. Lets billing/quota logic work."""
    servers = await get_servers_by_protocol("vless")
    for server in servers:
        if not server.get("agent_url") or not server.get("agent_token"):
            continue
        client = client_for_server(server)
        # Audit fix 2026-05-24 (C1, RUNTIME CRITICAL): раньше дёргали bare
        # `list_peers("vless")` — но такого service на агенте нет (с tier-split:
        # vless-base/max/slow/grace). 404 → counter навсегда 0 → quota_throttle
        # никогда не enforces → soft_cap_gb эффективно бесконечен. Теперь
        # итерируем _VLESS_SYNC_SERVICES и аггрегируем (same UUID в разных
        # inbound'ах = peer в transit; max() даёт правильное last-known value).
        per_uuid_stats: dict[str, dict] = {}
        timed_out = False
        for svc in _VLESS_SYNC_SERVICES:
            try:
                peers = await asyncio.wait_for(client.list_peers(svc), timeout=30)
            except asyncio.TimeoutError:
                logger.warning(
                    "_sync_vless_stats: timeout server=%s svc=%s, skipping rest",
                    server.get("name"), svc,
                )
                timed_out = True
                break
            except VpnctlError:
                # 404 = service not on this server (legacy or tier-split mismatch)
                continue
            except Exception as e:
                logger.warning("vless stats sync error server=%s svc=%s: %s",
                               server.get("name"), svc, e, exc_info=True)
                continue
            for peer in peers or []:
                uuid = peer.get("id")
                if not uuid:
                    continue
                rx = int(peer.get("rx_bytes") or 0)
                tx = int(peer.get("tx_bytes") or 0)
                last_seen = peer.get("last_seen")
                if last_seen and last_seen.startswith("0001"):
                    last_seen = None
                prev = per_uuid_stats.get(uuid)
                if not prev or (rx + tx) > (prev["rx"] + prev["tx"]):
                    per_uuid_stats[uuid] = {"rx": rx, "tx": tx, "last_seen": last_seen}
        if timed_out and not per_uuid_stats:
            continue

        for uuid, st in per_uuid_stats.items():
            cfg_id = await get_config_id_by_vless_uuid_and_server(uuid, server["id"])
            if cfg_id:
                await update_config_traffic(cfg_id, st["rx"], st["tx"], st["last_seen"])


async def _apply_quota_throttle(bot: Bot):
    """For each VLESS subscription, aggregate usage across all its locations
    and switch the entire subscription between normal and throttled tiers
    via the agent.

    NB: multi-location VLESS = one logical slot replicated on N servers (same
    UUID, multiple rows in `configs`). The quota cap is per-subscription, NOT
    per-row — otherwise user gets Nx the allowance.  We sum bytes across all
    rows of the same subscription_id, then apply identical action to each row.
    """
    from collections import defaultdict

    configs = await get_active_vless_configs_with_plan()
    by_sub: dict[int, list[dict]] = defaultdict(list)
    for cfg in configs:
        sub_id = cfg.get("subscription_id")
        if sub_id is not None:
            by_sub[int(sub_id)].append(cfg)

    for sub_id, sub_configs in by_sub.items():
        head = sub_configs[0]
        plan_key = head["plan_key"]
        plan = VPN_PLANS.get(plan_key)
        if not plan:
            continue
        cap_gb = plan.get("soft_cap_gb")
        if not cap_gb:
            continue  # legacy plan without speed-tier — пропускаем

        cap_bytes = cap_gb * (1024 ** 3)
        total_used = sum(
            (c.get("rx_bytes") or 0) + (c.get("tx_bytes") or 0)
            for c in sub_configs
        )
        should_throttle = total_used > cap_bytes

        normal_svc = vless_service_for_plan(plan_key)
        slow_svc = vless_slow_service_for_plan(plan_key)
        if not slow_svc:
            continue

        # `is_throttled` определяем по головному cfg — все локации одной sub'и
        # обязаны быть в одном tier'е (мы сами их так переводим). Если drift
        # обнаружится, цикл ниже выровняет: каждая row будет проверена против
        # общего should_throttle.
        #
        # already_notified — флаг из БД на уровне подписки. Заменяет старый
        # in-memory `notified_for_throttle`: переживает рестарт + позволяет
        # триггерить un-throttle сообщение когда квота обновляется.
        already_notified = bool(head.get("reminded_quota_throttled"))
        sent_throttle_notify = False
        sent_restore_notify = False
        for cfg in sub_configs:
            cfg_data = cfg.get("config_data") or ""
            is_throttled = (":9443" in cfg_data) or (":9448" in cfg_data)
            if should_throttle == is_throttled:
                continue  # state already correct for this row

            server = await get_server_by_id(cfg["server_id"])
            if not server or not server.get("agent_url"):
                continue
            client = client_for_server(server)
            uuid = cfg["vless_uuid"]
            label = f"tg{cfg['user_id']}_{cfg['config_id']}"

            try:
                if should_throttle and not is_throttled:
                    # Move into throttled tier: add to slow, remove from normal.
                    # Compensating rollback: if remove fails after add, undo the add
                    # to avoid split-brain (UUID in both inbounds simultaneously).
                    slow_added = False
                    try:
                        slow_peer = await client.add_peer(slow_svc, label, peer_id=uuid)
                        slow_added = True
                        await client.remove_peer(normal_svc, uuid)
                    except VpnctlError:
                        if slow_added:
                            try:
                                await client.remove_peer(slow_svc, uuid)
                            except Exception:
                                pass
                        raise
                    if slow_peer.config:
                        await update_config_data(cfg["config_id"], slow_peer.config)
                    logger.info(
                        "throttled config #%d (sub=%d, total used %.1f GB > %d GB cap)",
                        cfg["config_id"], sub_id, total_used / 1024**3, cap_gb,
                    )
                    # Уведомляем юзера только если ещё не уведомляли в прошлом
                    # тике (DB-флаг reminded_quota_throttled). Без этой проверки
                    # после рестарта/drift'а юзер получил бы повторный спам.
                    if not already_notified and not sent_throttle_notify:
                        sent_throttle_notify = True
                        try:
                            _lang = await get_user_lang(cfg["user_id"])
                            await bot.send_message(
                                cfg["user_id"],
                                _i18n_t(
                                    _lang, "bot_quota_throttle",
                                    cap_gb=cap_gb,
                                    throttle_mbps=plan.get('throttle_mbps', '?'),
                                ),
                                parse_mode="HTML",
                                reply_markup=_renew_kb(_lang),
                            )
                        except Exception as e:
                            logger.warning("notify throttle user %d: %s", cfg["user_id"], e, exc_info=True)
                elif is_throttled and not should_throttle:
                    # Restore: re-add to normal, remove from slow.
                    # Same compensating rollback pattern.
                    restore_added = False
                    try:
                        normal_peer = await client.add_peer(normal_svc, label, peer_id=uuid)
                        restore_added = True
                        await client.remove_peer(slow_svc, uuid)
                    except VpnctlError:
                        if restore_added:
                            try:
                                await client.remove_peer(normal_svc, uuid)
                            except Exception:
                                pass
                        raise
                    if normal_peer.config:
                        await update_config_data(cfg["config_id"], normal_peer.config)
                    logger.info("throttle restored on config #%d (sub=%d)", cfg["config_id"], sub_id)
                    # Юзер раньше получал throttle-сообщение → сообщаем что
                    # скорость вернулась (новый расчётный месяц / админ
                    # сбросил счётчики). Без этого юзеры жаловались «продлил
                    # подписку, а скорость всё ещё медленная» — продление
                    # обнуляло rx_bytes, но мы не давали явного фидбека.
                    if already_notified and not sent_restore_notify:
                        sent_restore_notify = True
                        try:
                            _restore_lang = await get_user_lang(cfg["user_id"]) or "ru"
                            await bot.send_message(
                                cfg["user_id"],
                                _i18n_t(_restore_lang, "bot_quota_restore"),
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            logger.warning("notify restore user %d: %s", cfg["user_id"], e, exc_info=True)
            except VpnctlError as e:
                logger.warning("throttle change failed for config #%d: %s", cfg["config_id"], e, exc_info=True)
            except Exception as e:
                logger.warning("throttle change error for config #%d: %s", cfg["config_id"], e, exc_info=True)

        # Обновляем DB-флаг для подписки после обработки всех её row'ов:
        # — отправили throttle-notify впервые → ставим в 1 (на следующих тиках
        #   не повторим);
        # — отправили restore-notify → сбрасываем в 0 (готовы снова уведомить
        #   если юзер опять выберет квоту в этом или следующем периоде).
        if sent_throttle_notify:
            try:
                await set_quota_throttled_flag(sub_id, True)
            except Exception as e:
                logger.warning("quota flag set failed sub=%d: %s", sub_id, e, exc_info=True)
        elif sent_restore_notify:
            try:
                await set_quota_throttled_flag(sub_id, False)
            except Exception as e:
                logger.warning("quota flag clear failed sub=%d: %s", sub_id, e, exc_info=True)


_VLESS_SYNC_SERVICES = [
    "vless",           # legacy single-tier (kept for backward compat)
    "vless-base", "vless-max",
    "vless-base-slow", "vless-max-slow",
    "vless-grace",
]


async def _sync_vless_active_uuids():
    """Sends the list of currently-active UUIDs to each VLESS server.
    Agent removes any UUID not in the list — stops users without a paid subscription.

    Syncs all known VLESS tier services.  Each call is best-effort: a 404
    for a service that doesn't exist on this server is silently skipped.
    """
    servers = await get_servers_by_protocol("vless")
    for server in servers:
        if not server.get("agent_url") or not server.get("agent_token"):
            continue
        try:
            client = client_for_server(server)
            valid = await get_active_vless_uuids_by_server(server["id"])

            # SAFETY GUARD (2026-05-23 incident): пустой valid → wipe всех
            # peer-ов на агенте. Если server.active_peers > 0 по БД-счётчику
            # И SELECT вернул [] — это почти наверняка DB-ошибка / drift,
            # а не legitimate «никто не платит». Лучше пропустить sync и
            # дать orphan-peer-ам пожить лишний час, чем снести платных.
            # Реальный empty-state (новый сервер) тоже сюда попадёт, но
            # active_peers=0 в этом случае → guard не сработает.
            if not valid and (server.get("active_peers") or 0) > 0:
                logger.error(
                    "vless sync ABORTED: empty valid_ids but server.active_peers=%d "
                    "(server=%s id=%d) — likely DB drift, NOT wiping",
                    server.get("active_peers"), server.get("name"), server["id"],
                )
                continue

            total_kept = 0
            total_removed: list[str] = []
            timed_out = False
            for svc in _VLESS_SYNC_SERVICES:
                try:
                    # Per-call timeout: 6 услуг × hung agent = весь _safe()
                    # сожран; 30 сек хватит на любой здоровый sync_active_ids.
                    # allow_empty=True: scheduler уже сделал собственный sanity-check
                    # выше (active_peers floor). Здесь client.sync_active_ids
                    # имеет defence-in-depth raise при пустом valid_ids — без
                    # этого флага legitimate empty sync (новый сервер, 0 peers)
                    # спамил бы VpnctlError каждый тик.
                    result = await asyncio.wait_for(
                        client.sync_active_ids(svc, valid, allow_empty=True),
                        timeout=30,
                    )
                    total_kept += result.get("kept", 0)
                    total_removed += result.get("removed", []) or []
                except asyncio.TimeoutError:
                    timed_out = True
                    logger.warning(
                        "_sync_vless_active_uuids: timeout server=%s svc=%s, skipping rest",
                        server.get("name"), svc,
                    )
                    break
                except VpnctlError:
                    pass  # service not present on this server — skip
            # L9: noisy log — only log INFO when something actually changed
            # (peers removed) or a timeout happened mid-sync. Otherwise DEBUG.
            if len(total_removed) > 0 or timed_out:
                logger.info(
                    "vless sync: server=%s, valid=%d, kept=%d, removed=%d%s",
                    server.get("name"),
                    len(valid),
                    total_kept,
                    len(total_removed),
                    " (partial — timeout)" if timed_out else "",
                )
            else:
                logger.debug(
                    "vless sync: server=%s, valid=%d, kept=%d, removed=0",
                    server.get("name"), len(valid), total_kept,
                )
        except Exception as e:
            logger.warning("vless uuid sync error server=%s: %s", server.get("name"), e, exc_info=True)


async def _daily_backup(bot: Bot):
    """Раз в сутки шлёт сжатый дамп bot.db админу в Telegram.

    Безопасность:
      - SQLite-aware snapshot через `sqlite3.Connection.backup()` —
        корректно для WAL-режима (учитывает WAL/SHM, в отличие от
        `shutil.copy2` который мог терять свежие транзакции).
      - sub_token и payment_id затираются NULL'ами в snapshot — это
        самые чувствительные колонки. Утечка backup'а в TG = потеря
        самого backup'а, но не прямой доступ к VPN-конфигам юзеров.

    Health-alert:
      - state-файл `/opt/vpnbot/.last_backup_date` (persistent, не /tmp!)
        хранит дату последнего успешного backup'а.
      - Если последний backup был > 2 дней назад — шлём alert админу.
        Это покрывает молчаливые failures: bot.send_document отклонён,
        диск переполнен, sqlite повреждён, etc.
    """
    import gzip
    import os
    import sqlite3
    from datetime import datetime, timedelta
    from aiogram.types import BufferedInputFile
    from config import ADMIN_ID
    from services.database import DB_PATH

    # Persistent state — раньше был в /tmp и терялся при рестарте VPS
    # (что давало дубль backup'ов и ломало health-check «3 дня не было»).
    state_file = "/opt/vpnbot/.last_backup_date"
    today = datetime.utcnow().strftime("%Y-%m-%d")
    last_backup_date: str | None = None
    try:
        with open(state_file) as f:
            last_backup_date = f.read().strip()
            if last_backup_date == today:
                return  # уже отправили сегодня
    except FileNotFoundError:
        pass

    # Health-alert: если последний успешный backup был > 2 дней назад,
    # шлём alert. Делаем это ДО самого backup'а — если он опять упадёт,
    # хотя бы alert уйдёт.
    if last_backup_date:
        try:
            last_dt = datetime.strptime(last_backup_date, "%Y-%m-%d")
            days_since = (datetime.utcnow() - last_dt).days
            if days_since >= 2:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Backup health alert</b>\n\n"
                        f"Последний успешный backup: <b>{last_backup_date}</b> "
                        f"({days_since} дн. назад).\n"
                        f"Проверь /opt/vpnbot logs — что-то ломает daily backup "
                        f"(disk full / Telegram API / sqlite corruption).",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning("backup health alert failed: %s", e, exc_info=True)
        except ValueError:
            pass  # malformed state file — игнорируем

    # Snapshot в persistent /opt/vpnbot/.snapshots/ — не /tmp:
    # /tmp может быть tmpfs (теряется при reboot) или tmpwatch-cleaned.
    # На небольших VPS / partition может переполниться при `gzip` (~10 MB → 5 MB).
    snap_dir = "/opt/vpnbot/.snapshots"
    os.makedirs(snap_dir, exist_ok=True)
    snap = f"{snap_dir}/bot.db.snapshot"
    if os.path.exists(snap):
        os.unlink(snap)
    if os.path.exists(snap + ".gz"):
        os.unlink(snap + ".gz")

    # Backup + redact + gzip — все blocking-операции. На больших БД (50+ MB)
    # это 5-10 секунд блокировки event loop'а. polling замораживается, юзеры
    # видят "бот не отвечает". Переносим в default executor (thread pool).
    def _backup_blocking() -> bytes:
        # SQLite-aware backup (учитывает WAL+SHM, в отличие от shutil.copy)
        src = sqlite3.connect(str(DB_PATH))
        try:
            dst = sqlite3.connect(snap)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        # Redact sensitive columns на копии — sub_token даёт постоянный
        # доступ к VLESS-конфигам через /sub/{token}.
        conn = sqlite3.connect(snap)
        try:
            conn.execute("UPDATE users SET sub_token=NULL")
            conn.commit()
        finally:
            conn.close()

        with open(snap, "rb") as src_f, gzip.open(snap + ".gz", "wb", compresslevel=9) as dst_f:
            for chunk in iter(lambda: src_f.read(64 * 1024), b""):
                dst_f.write(chunk)
        with open(snap + ".gz", "rb") as f:
            return f.read()

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _backup_blocking)

    delivered = False
    try:
        try:
            await bot.send_document(
                ADMIN_ID,
                BufferedInputFile(
                    data,
                    filename=f"bot-db-{today}-{_bot_version()[:7]}.gz",
                ),
                caption=f"📦 Daily backup · {today} · {len(data)//1024} KB · sub_tokens redacted",
            )
            delivered = True
            logger.info("daily backup отправлен (%d KB)", len(data) // 1024)
        except Exception as e:
            logger.warning("daily backup не отправлен в TG: %s — сохраняем локально", e, exc_info=True)
            # Local rotation fallback — keep last 7 backups under /opt/vpnbot/.backups/.
            # Если Telegram-канал админа упал, диск переполнен, или bot.send_document
            # отклонён — у нас остаётся локальный snapshot чтобы вручную выкачать
            # rsync'ом. Без этого fallback'а единственная копия — текущая БД и
            # один pre-migrate snapshot, что критично мало.
            try:
                from pathlib import Path as _Path
                backup_dir = _Path("/opt/vpnbot/.backups")
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                local_path = backup_dir / f"bot.db.{ts}.gz"
                local_path.write_bytes(data)
                # Rotate: keep last 7 (date-sorted by filename = timestamp).
                files = sorted(backup_dir.glob("bot.db.*.gz"))
                for old in files[:-7]:
                    try:
                        old.unlink()
                    except Exception:
                        pass
                logger.info("daily backup saved locally: %s", local_path)
            except Exception as inner_e:
                logger.error("daily backup local save also failed: %s", inner_e, exc_info=True)

        # State file обновляем только при успешной TG-доставке — иначе health-alert
        # «N дней без backup'a» начнёт работать как и задумано.
        if delivered:
            with open(state_file, "w") as f:
                f.write(today)
    finally:
        # cleanup
        for p in (snap, snap + ".gz"):
            try:
                os.unlink(p)
            except OSError:
                pass


async def _send_expiry_reminders(bot: Bot):
    """Напоминания за 3д и за 1д до истечения. Триалы используют свой текст —
    «выбери тариф», а не «продли», т.к. триал ещё не платный.

    День-N upsell (включён 27.05 после апгрейда триала 3→7д): 3-day reminder
    у триала падает на день 4 (НЕ activation day как было при 3д триале) —
    это второй конверсионный момент «осталось 3 дня, выбери тариф». Текст
    bot_trial_expiry_3d отдельный от bot_expiry_3d (тот про «продли», у триала
    нечего продлевать). 1-day reminder — главный момент в день-6."""
    for days in (3, 1):
        subs = await get_subscriptions_expiring_soon(days)
        # Audit fix 2026-05-24 (H1): dedup по user_id внутри одного "days"-окна.
        # Если у юзера 2 active sub'a (rare overlap: trial + paid в окне между
        # successful_payment и _close_trial_on_paid_purchase) — раньше получал
        # 2 одинаковых "expires in N days" сообщения с разрывом 40ms = spam.
        # Берём первую sub (соonest по expires_at), остальные silent-mark'аем.
        # SQL уже ORDER BY expires_at ASC (database.py get_subscriptions_expiring_soon).
        seen_users: set[int] = set()
        for sub in subs:
            user_id = sub["user_id"]
            is_trial = sub.get("plan") == "vpn_trial"

            # Multi-sub dedup: silent-mark если уже отправили этому юзеру в
            # этом tick'е. Без этого 2 sub'а одного юзера → 2 сообщения.
            if user_id in seen_users:
                await mark_reminded(sub["id"], days)
                continue
            seen_users.add(user_id)

            _lang = await get_user_lang(user_id)
            # Триал получает оба reminder'а: день-4 «осталось 3 дня» + день-6
            # «последний день». Тексты отдельные от платных (у триала «выбери
            # тариф», не «продли»). Клавиатура is_trial=True → CTA «Выбрать
            # тариф». (Раньше 3-day для триала подавлялся — при 3д триале он
            # падал в день активации; после апгрейда 3→7д это конверсионный
            # момент, см. docstring.)
            if is_trial:
                text = _i18n_t(_lang, "bot_trial_expiry_3d" if days == 3
                               else "bot_trial_expiry_1d")
            elif days == 3:
                text = _i18n_t(_lang, "bot_expiry_3d")
            else:
                text = _i18n_t(_lang, "bot_expiry_1d")
            sent = await _send_throttled(
                bot, user_id, text, parse_mode="HTML",
                reply_markup=_renew_kb(_lang, is_trial=is_trial),
            )
            if sent:
                await mark_reminded(sub["id"], days)

    # ── Grace reminder: 3 дня до полного закрытия доступа ─────────────────
    # Юзер в grace ловит throttle 256 кбит/с и через 14 дней теряет доступ
    # полностью.  За 3 дня до этого момента — последний конверсионный шанс
    # вернуть его в active, иначе уходит.  Без этого reminder'а retention
    # loss потому что юзер чаще всего забывает что VPN на throttle.
    grace_subs = await get_subscriptions_grace_ending_soon(3)
    for sub in grace_subs:
        _lang = await get_user_lang(sub["user_id"])
        sent = await _send_throttled(
            bot, sub["user_id"], _i18n_t(_lang, "bot_grace_3d"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )
        if sent:
            await mark_grace_reminded(sub["id"])


async def _send_renewal_reminders(bot: Bot):
    """За 3 дня до auto-charge на recurring subs (Lava + Stars) шлём
    уведомление: «через 3 дня спишется N₽/⭐ — отменить можно тут».

    Снижает chargeback risk + строит trust («предупредил, не сюрприз»).
    """
    from services.database import get_recurring_renewal_due_soon, mark_renewal_reminded, get_subscription_by_id
    from services.i18n_plural import plural_ru, DAYS
    days_before = 3
    subs = await get_recurring_renewal_due_soon(days_before=days_before)
    if not subs:
        return
    logger.info("renewal reminders: %d sub'ов готовы напомнить", len(subs))

    for sub in subs:
        # Re-fetch в случае если админ extend'нул между SELECT и send_message —
        # без этого юзер получит «через 3 дня спишется» хотя sub продлена и
        # auto-charge уже не на горизонте 3 дней.
        fresh = await get_subscription_by_id(sub["id"])
        if not fresh or not fresh.get("expires_at"):
            continue
        try:
            cur_expires = datetime.fromisoformat(str(fresh["expires_at"]).replace(' ', 'T'))
        except Exception:
            continue
        days_left = (cur_expires - datetime.utcnow()).days
        if days_left < 0 or days_left > days_before + 1:
            # Sub была extended или expired с момента SELECT — пропускаем
            # устаревший reminder.
            continue
        days_left = max(0, days_left)

        user_id = sub["user_id"]
        plan_key = sub.get("plan") or ""
        provider = sub.get("payment_provider") or ""
        plan = VPN_PLANS.get(plan_key, {})
        amount_rub = sub.get("amount_rub") or int(float(plan.get("rub", 0)))
        stars = plan.get("stars", 0)

        # F12: для Lava (RU/EN бирюль рынка) — берём из i18n_bot. Stars-recurring
        # пока только RU — отдельной локализации не требует, оставляем строку.
        user_lang = await get_user_lang(user_id)
        plan_name = plan_display_name(plan, user_lang or "ru") or plan_key
        from services.i18n_bot import day_word as _day_word

        # Edge case: "через 0/1 дней" звучит криво — для 0/1 отдельные ключи.
        day_w = _day_word(user_lang, days_left)

        if provider == "lavatop":
            if days_left == 0:
                head = _i18n_t(user_lang, "bot_renewal_reminder_today", amount=amount_rub)
            elif days_left == 1:
                head = _i18n_t(user_lang, "bot_renewal_reminder_tomorrow", amount=amount_rub)
            else:
                head = _i18n_t(
                    user_lang, "bot_renewal_reminder_in",
                    n=days_left, day_word=day_w, amount=amount_rub,
                )
            body = _i18n_t(
                user_lang, "bot_renewal_reminder_body",
                plan=plan_name, date=cur_expires.strftime("%d.%m.%Y"),
            )
            text = head + body
        else:  # stars
            date_str = cur_expires.strftime('%d.%m.%Y')
            if days_left == 0:
                text = _i18n_t(
                    user_lang, "bot_stars_renewal_today",
                    stars=stars, plan=plan_name, date=date_str,
                )
            elif days_left == 1:
                text = _i18n_t(
                    user_lang, "bot_stars_renewal_tomorrow",
                    stars=stars, plan=plan_name, date=date_str,
                )
            else:
                text = _i18n_t(
                    user_lang, "bot_stars_renewal_in",
                    n=days_left, day_word=day_w,
                    stars=stars, plan=plan_name, date=date_str,
                )

        sent = await _send_throttled(bot, user_id, text, parse_mode="HTML")
        if sent:
            await mark_renewal_reminded(sub["id"])


async def _send_trial_nudge(bot: Bot):
    """Day-2 engagement: через 20-48ч после активации триала шлём «как VPN?»

    Момент пиковой мотивации: юзер освоился, но ещё не принял решение продлять.
    Мягкое «помогу разобраться» конвертирует лучше чем напоминание о деньгах.
    """
    candidates = await get_trial_nudge_candidates()
    if not candidates:
        return
    logger.info("trial nudge: %d кандидатов", len(candidates))
    for sub in candidates:
        _lang = await get_user_lang(sub["user_id"])
        sent = await _send_throttled(
            bot, sub["user_id"], _i18n_t(_lang, "bot_trial_nudge"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )
        if sent:
            await mark_trial_nudge_sent(sub["id"])


async def _winback_campaign(bot: Bot):
    """Win-back: через 7-14 дней после истечения шлём реактивационное письмо.

    Цель — вернуть ушедших пользователей пока они ещё помнят про VPN.
    7 дней: успели почувствовать что VPN нет, но ещё не забыли про нас.
    14 дней: крайний срок, дальше CTR резко падает.

    Один раз на подписку (winback_sent=1) — не спамим.
    Пользователь с active/grace подпиской исключается (уже вернулся).
    """
    candidates = await get_winback_candidates(days_min=7, days_max=14)
    if not candidates:
        return
    logger.info("win-back: %d кандидатов", len(candidates))
    for sub in candidates:
        user_id = sub["user_id"]
        sub_id  = sub["id"]
        _lang = await get_user_lang(user_id)
        sent = await _send_throttled(
            bot, user_id, _i18n_t(_lang, "bot_winback"),
            parse_mode="HTML",
            reply_markup=_renew_kb(_lang),
        )
        if sent:
            await mark_winback_sent(sub_id)


async def _sync_esim_usage():
    """Раз в 3 часа батчем тянет /esim/usage/query для активных eSIM-профилей.
    Лимит API: 10 esimTranNo за один запрос; rate limit 8 req/sec.
    Юзедж у esimaccess обновляется раз в 2-3 ч, чаще опрашивать смысла нет."""
    profiles = await get_esim_profiles_for_usage_sync(limit=200)
    if not profiles:
        return

    tran_nos = [p["esim_tran_no"] for p in profiles if p["esim_tran_no"]]
    BATCH = 10
    updated = 0
    for i in range(0, len(tran_nos), BATCH):
        batch = tran_nos[i:i + BATCH]
        try:
            resp = await esim_api.usage_query(batch)
        except Exception as e:
            logger.warning("eSIM usage_query batch failed: %s", e, exc_info=True)
            continue
        for u in (resp.get("obj") or {}).get("esimUsageList") or []:
            tn = u.get("esimTranNo")
            used = u.get("dataUsage", 0)
            if tn:
                await update_esim_usage(tn, used)
                updated += 1
        # Лёгкий throttle между батчами (rate limit 8 req/s)
        await asyncio.sleep(0.2)

    if updated:
        logger.info("eSIM usage sync: обновлено %d профилей", updated)


# Счётчик тиков шедулера для запуска редких задач (eSIM usage — раз в 3ч)
_TICK = 0
_ESIM_SYNC_EVERY_N_TICKS = 3  # CHECK_INTERVAL=1ч → раз в 3ч

# Health-probe — отдельный таск, бьёт чаще основного шедулера.
HEALTH_PROBE_INTERVAL_SEC = 60
HEALTH_CLEANUP_INTERVAL_SEC = 24 * 3600  # раз в сутки чистим логи старше 31 дня

# Фоновые таски удерживаются здесь — без этого `asyncio.create_task()` может
# быть собран GC, и task незаметно умрёт (классический asyncio-footgun).
_BG_TASKS: set[asyncio.Task] = set()


def _on_bg_done(task: asyncio.Task) -> None:
    """done-callback: убираем task из реестра + логируем unexpected exception.
    Без этого `set.discard` молча проглатывал бы все исключения, и фоновые
    падения становились невидимыми.
    """
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "bg task %s failed: %s",
            task.get_name() or "<unnamed>",
            exc,
            exc_info=exc,
        )


def _spawn_bg(coro, name: str | None = None) -> asyncio.Task:
    """Запускает фоновую корутину и удерживает ссылку. Снимает её
    после завершения, чтобы set не рос бесконечно.

    `name` помогает в debug-выводе asyncio.all_tasks() — без него видны
    только generic "Task pending coro=<...>" что усложняет диагностику
    зависших или утечённых тасков.
    """
    task = asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_on_bg_done)
    return task


async def _reconcile_peer_counters(bot: Bot) -> None:
    """Periodic recompute of servers.active_peers from configs table.

    Catches counter drift caused by missed increments/decrements in edge paths
    (crashes, partial-fail operations). Runs hourly. Logs admin alert if drift
    exceeds threshold per server.
    """
    from services.database import reconcile_active_peers_counters
    fixes = await reconcile_active_peers_counters()
    if not fixes:
        return

    logger.warning("active_peers reconcile: %d servers had drift", len(fixes))

    # Alert admin if drift is significant (>5 peers per server suggests a bug)
    significant = [f for f in fixes if abs(f["delta"]) > 5]
    from config import ADMIN_ID
    if significant and ADMIN_ID:
        lines = ["⚠️ <b>active_peers drift detected</b>\n"]
        for f in significant[:10]:
            lines.append(
                f"• {f['name']}: {f['before']} → {f['after']} "
                f"(delta {f['delta']:+d})"
            )
        try:
            await bot.send_message(ADMIN_ID, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.warning("reconcile admin alert: %s", e)


async def _reconcile_orphans(bot: Bot) -> None:
    """Find DB rows that reference non-existent parents (FK-not-enforced
    columns: users.referred_by, payments.subscription_id).

    Также детектирует subscriptions с NULL expires_at (на non-trial плане) —
    это «never expires» bug который мы фиксили миграцией D7, но runtime
    регрешн может его вернуть. Reports counts via admin alert if any
    orphans found. Запускается раз в сутки (24h), per D3.
    """
    import aiosqlite
    from services.database import DB_PATH as _DB_PATH
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            """SELECT COUNT(*) FROM users u
               WHERE u.referred_by IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM users r WHERE r.id = u.referred_by)"""
        ) as cur:
            row = await cur.fetchone()
            orphan_referred = row[0] if row else 0

        async with db.execute(
            """SELECT COUNT(*) FROM payments p
               WHERE p.subscription_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.id = p.subscription_id)"""
        ) as cur:
            row = await cur.fetchone()
            orphan_payments = row[0] if row else 0

        async with db.execute(
            """SELECT COUNT(*) FROM subscriptions
               WHERE expires_at IS NULL AND plan != 'vpn_trial'"""
        ) as cur:
            row = await cur.fetchone()
            null_expires = row[0] if row else 0

    if orphan_referred + orphan_payments + null_expires > 0:
        logger.warning(
            "Orphan reconcile: referred=%d payments=%d null_expires=%d",
            orphan_referred, orphan_payments, null_expires,
        )
        try:
            from config import ADMIN_ID
            if ADMIN_ID and bot:
                await bot.send_message(
                    ADMIN_ID,
                    f"🔍 <b>DB integrity check</b>\n\n"
                    f"Orphan users.referred_by: {orphan_referred}\n"
                    f"Orphan payments.subscription_id: {orphan_payments}\n"
                    f"NULL expires_at (non-trial): {null_expires}\n\n"
                    "См. логи для деталей.",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning("Orphan reconcile admin alert failed: %s", e)


async def _run_health_loop(bot: Bot | None = None):
    """Independent loop: probe servers every 60s, write to server_health_log.
    Передаём bot чтобы health.py мог слать alert админу при auto-(de)activate.
    """
    from services.health import probe_all_servers, cleanup_old_logs
    cleanup_counter = 0
    logger.info("Health-probe запущен (интервал: %d сек)", HEALTH_PROBE_INTERVAL_SEC)
    # One-shot cleanup at boot — counter resets each restart, so without this
    # the daily cleanup-tick never fires when the bot restarts more often than
    # HEALTH_CLEANUP_INTERVAL_SEC (CI deploys hit ~daily). Prod log grew to
    # 54k+ rows over 9 days because of this.
    try:
        await cleanup_old_logs(keep_days=31)
    except Exception as e:
        logger.warning("health cleanup at boot failed: %s", e, exc_info=True)
    while True:
        try:
            await probe_all_servers(bot)
        except Exception as e:
            logger.warning("health probe error: %s", e, exc_info=True)
        cleanup_counter += HEALTH_PROBE_INTERVAL_SEC
        if cleanup_counter >= HEALTH_CLEANUP_INTERVAL_SEC:
            cleanup_counter = 0
            try:
                await cleanup_old_logs(keep_days=31)
                logger.info("health: log cleanup done")
            except Exception as e:
                logger.warning("health cleanup error: %s", e, exc_info=True)
        await asyncio.sleep(HEALTH_PROBE_INTERVAL_SEC)


async def run_scheduler(bot: Bot):
    """Бесконечный цикл — запускать как asyncio background task из bot.py."""
    global _TICK
    logger.info("Планировщик подписок запущен (интервал: %d сек)", CHECK_INTERVAL)

    # Миграция: добавляет grace_until в subscriptions если её ещё нет.
    await ensure_grace_column()

    # Запускаем health-probe отдельным таском — он бьёт каждые 60с независимо.
    # `_spawn_bg` удерживает ссылку, чтобы GC не убил task.
    _spawn_bg(_run_health_loop(bot))

    # Первый прогон через 60с после старта — чтобы:
    #   1) не зависнуть на медленных задачах в момент cold start (juggling polling)
    #   2) не ждать целый час до первой проверки истёкших подписок после рестарта
    # 60с — компромисс: достаточно для прогрева, но не сутки.
    FIRST_TICK_DELAY = 60

    # Per-task timeout. Без него один залипший vless-sync (медленный агент) на
    # 10 мин блокировал бы reminders на этот час → юзер не получает
    # «осталось 1 день» → не продлевает (sec audit edge-case C2).
    # Критичные retention-таски (expiry/grace/reminders) идут с короткими
    # timeout'ами, медленные sync'и — с longer.
    async def _safe(name: str, coro, timeout: int = 180):
        try:
            await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.CancelledError:
            # При shutdown event-loop'а CancelledError должен прокинуться
            # дальше, иначе scheduler становится «не убиваемым» и graceful
            # shutdown зависает на await.
            raise
        except asyncio.TimeoutError:
            logger.error("scheduler task '%s' timed out after %ds", name, timeout)
        except Exception as e:
            logger.exception("scheduler task '%s' failed: %s", name, e)

    while True:
        await asyncio.sleep(FIRST_TICK_DELAY if _TICK == 0 else CHECK_INTERVAL)
        _TICK += 1
        # Критичные retention первыми и с коротким timeout — чтобы даже если
        # медленный agent залип, юзеры получили уведомления вовремя.
        await _safe("expiry_reminders", _send_expiry_reminders(bot),     timeout=120)
        await _safe("trial_nudge",      _send_trial_nudge(bot),          timeout=60)
        await _safe("renewal_reminders", _send_renewal_reminders(bot),   timeout=60)
        await _safe("expired_subs",     _process_expired_subscriptions(bot),       timeout=180)
        await _safe("expired_trials",   _process_expired_trials(bot),              timeout=180)
        await _safe("grace_expired",    _process_grace_expired_subscriptions(bot), timeout=180)
        # Orphan-configs retry: configs застрявшие active под expired-sub
        # (grace-revoke упал из-за agent timeout/5xx). Без этого reaper'а
        # orphan-peer на сервере висит вечно — ни grace-reaper, ни
        # active-expiry не подбирают (sub.status='expired', cfg.status='active').
        await _safe("orphan_configs",   _process_orphan_active_configs(),          timeout=180)
        # Refund cascade catch-up: ищет sub'ы, где Telegram money refund
        # (необратим) прошёл, но DB/agent cleanup не доехал из-за crash.
        # Идемпотентен — если всё чисто, get_partial_refunds вернёт [].
        await _safe("reconcile_refunds", _reconcile_partial_refunds(bot), timeout=180)
        await _safe("reconcile_peers",  _reconcile_peer_counters(bot),   timeout=120)
        await _safe("expired_orders",   _process_expired_orders(bot),    timeout=60)
        # Менее критичные / медленные — отдельно с large timeout'ом.
        await _safe("vless_stats",      _sync_vless_stats(),             timeout=300)
        await _safe("quota_throttle",   _apply_quota_throttle(bot),      timeout=300)
        await _safe("vless_uuid_sync",  _sync_vless_active_uuids(),      timeout=300)
        await _safe("daily_backup",     _daily_backup(bot),              timeout=240)
        if _TICK % _ESIM_SYNC_EVERY_N_TICKS == 0:
            await _safe("esim_usage",   _sync_esim_usage(),              timeout=180)
        # VACUUM раз в неделю (168 тиков). Без него БД растёт после
        # delete/update — SQLite не освобождает страницы автоматически.
        # incremental_vacuum дешевле full VACUUM (не блокирует БД целиком).
        if _TICK % (24 * 7) == 0:
            await _safe("db_vacuum",    _weekly_vacuum(),                timeout=300)
        # Stuck activating slots — каждые 4 часа. Слоты зависают в
        # 'activating' если provision упал (агент недоступен, таймаут).
        # Без этого юзер видит "слот занят" бесконечно до рестарта бота.
        # Пропускаем первый cleanup-tick после старта бота (_TICK == 4 даст
        # ~3h paused after boot): если бот упал mid-provision, peer мог
        # успеть создаться на агенте, но slot завис в 'activating'. На
        # первом tick сразу cleanup → reset slot → ghost peer на агенте.
        # Лучше подождать ещё одну итерацию (8h после boot), чтобы
        # оставшиеся retry-механизмы и юзер успели среагировать.
        # _safe-обёртка (добавлено 2026-05-23 после audit): без неё
        # exception из cleanup_stuck_activating_slots / DB-lock-timeout /
        # сетевой стопор пробрасывался в run_scheduler-while-loop и убивал
        # ВЕСЬ scheduler до restart бота. Каждый тик scheduler-а должен
        # быть isolated от других — в т.ч. этот ad-hoc cleanup.
        if _TICK % 4 == 0:
            async def _cleanup_stuck():
                from services.database import cleanup_stuck_activating_slots
                n = await cleanup_stuck_activating_slots()
                if n:
                    logger.info("cleanup_stuck_activating: сброшено %d слотов", n)
            await _safe("cleanup_stuck", _cleanup_stuck(), timeout=60)
        # Win-back кампания — раз в сутки. Шлём реактивационное сообщение
        # пользователям у которых sub истёк 7-14 дней назад и они не вернулись.
        if _TICK % 24 == 0:
            await _safe("winback",      _winback_campaign(bot),          timeout=120)
        # Orphan reconcile — раз в сутки (24h cadence). Детектит DB-integrity
        # дрифт по FK-not-enforced колонкам (users.referred_by,
        # payments.subscription_id) + NULL expires_at runtime-регрешн.
        # См. D3 в audit notes.
        if _TICK % 24 == 0:
            await _safe("reconcile_orphans", _reconcile_orphans(bot), timeout=120)
