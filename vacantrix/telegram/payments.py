# vacantrix/telegram/payments.py
"""Модуль обработки платежей через ЮKassa (Telegram Payments API).
   Строго по примеру интеграции ЮKassa: quantity = "1.00", vat_code = 1."""

import logging
import json
import traceback
from datetime import datetime, timezone, timedelta

from telegram import LabeledPrice, PreCheckoutQuery, SuccessfulPayment, Update
from telegram.ext import ContextTypes

from vacantrix.telegram.config import PROVIDER_TOKEN, ADMIN_ID
from vacantrix.telegram.supabase import (
    update_subscription_by_telegram,
    add_subscription_days,
    reset_reminders,
    get_referrer_telegram_id,
)

logger = logging.getLogger(__name__)

# Чеки включены (как в примере)
USE_RECEIPT = True


def _validate_provider_token() -> None:
    if not PROVIDER_TOKEN:
        raise ValueError("PROVIDER_TOKEN не задан в .env")
    if ":TEST:" in PROVIDER_TOKEN:
        logger.warning("Используется TEST-токен YooKassa — платежи тестовые, деньги не списываются")
    elif ":LIVE:" not in PROVIDER_TOKEN:
        logger.warning("PROVIDER_TOKEN имеет неожиданный формат")


def get_provider_token_mode() -> str:
    if ":LIVE:" in PROVIDER_TOKEN:
        return "LIVE"
    if ":TEST:" in PROVIDER_TOKEN:
        return "TEST"
    if PROVIDER_TOKEN:
        return "UNKNOWN"
    return "EMPTY"


def _validate_invoice_input(days: int, price: int) -> None:
    if days <= 0:
        raise ValueError("Срок подписки должен быть > 0")
    if price <= 0:
        raise ValueError("Цена должна быть > 0")
    payload = f"sub_{days}"
    if len(payload.encode()) > 128:
        raise ValueError("Слишком длинный payload")


def _build_receipt(days: int, amount_kop: int) -> dict:
    """Формирует объект receipt точно по примеру ЮKassa."""
    rubles = amount_kop / 100.0
    return {
        "receipt": {
            "items": [
                {
                    "description": f"Подписка Vacantrix на {days} дн.",
                    "quantity": "1.00",               # ← строка с двумя знаками
                    "amount": {
                        "value": f"{rubles:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1
                }
            ]
        }
    }


async def send_subscription_invoice(
        update: Update, context: ContextTypes.DEFAULT_TYPE, days: int, price: int
) -> None:
    user_id = update.effective_user.id
    invoice_kwargs = {}
    try:
        _validate_provider_token()
        logger.info("Отправка инвойса YooKassa: provider token mode=%s", get_provider_token_mode())
        _validate_invoice_input(days, price)
        amount_kop = price * 100
        if amount_kop <= 0 or amount_kop > 999999999:
            raise ValueError("Некорректная сумма платежа")
        prices = [LabeledPrice(label=f"Подписка {days} дн.", amount=amount_kop)]

        invoice_kwargs = {
            "title": "Подписка Vacantrix",
            "description": f"Доступ к автооткликам на {days} дней",
            "payload": f"sub_{days}",
            "provider_token": PROVIDER_TOKEN,
            "currency": "RUB",
            "start_parameter": "subscription",
            "prices": prices,
        }

        if USE_RECEIPT:
            receipt = _build_receipt(days, amount_kop)
            invoice_kwargs["need_email"] = True
            invoice_kwargs["send_email_to_provider"] = True
            invoice_kwargs["provider_data"] = json.dumps(receipt)

        await update.callback_query.message.reply_invoice(**invoice_kwargs)

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error("Ошибка отправки инвойса для user %d: %s", user_id, e, exc_info=True)
        if ADMIN_ID:
            try:
                debug_invoice = invoice_kwargs.copy()
                debug_invoice.pop("provider_token", None)
                debug_text = json.dumps(debug_invoice, indent=2, ensure_ascii=False, default=str)
                admin_msg = (
                    f"❌ Ошибка создания счёта для user {user_id} (days={days}, price={price}):\n"
                    f"<pre>{error_trace[:2000]}</pre>\n\n"
                    f"Параметры инвойса (без токена):\n"
                    f"<pre>{debug_text[:2000]}</pre>"
                )
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode="HTML")
            except Exception as notify_e:
                logger.warning("Не удалось уведомить админа: %s", notify_e)

        await update.callback_query.message.reply_text(
            "❌ Не удалось создать счёт. Попробуйте позже."
        )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query: PreCheckoutQuery = update.pre_checkout_query
    payload = query.invoice_payload
    # Валидируем формат payload
    try:
        parts = payload.split("_")
        assert parts[0] == "sub" and int(parts[1]) > 0
    except Exception:
        await query.answer(ok=False, error_message="Некорректные данные платежа.")
        return
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment: SuccessfulPayment = update.message.successful_payment
    tg_id = update.effective_user.id
    payload = payment.invoice_payload
    try:
        days = int(payload.split("_")[1])
    except (IndexError, ValueError):
        logger.error("Некорректный payload: %s", payload)
        await update.message.reply_text("❌ Ошибка в данных платежа.")
        return

    new_expire = datetime.now(timezone.utc) + timedelta(days=days)
    resp = update_subscription_by_telegram(tg_id, new_expire)
    if not resp or not resp.ok:
        logger.error("Ошибка активации подписки для user %d", tg_id)
        await update.message.reply_text("❌ Не удалось активировать подписку. Обратитесь в поддержку.")
        return

    reset_reminders(tg_id)
    txn_id = payment.provider_payment_charge_id
    amount_rub = payment.total_amount // 100
    logger.info("Платёж %s успешен, подписка активирована для user %d на %d дн.", txn_id, tg_id, days)

    # Уведомление администратора
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💰 *Новый платёж*\n\n"
                     f"Пользователь: `{tg_id}`\n"
                     f"Срок: *{days} дн.*\n"
                     f"Сумма: *{amount_rub} ₽*\n"
                     f"Транзакция: `{txn_id}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Не удалось уведомить админа о платеже: %s", e)

    referrer_id = get_referrer_telegram_id(tg_id)
    if referrer_id:
        bonus_resp = add_subscription_days(referrer_id, days)
        if bonus_resp and bonus_resp.ok:
            logger.info("Реферальный бонус %d дн. начислен рефереру %d", days, referrer_id)
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎁 Ваш друг купил подписку — вам начислено {days} дн. бонуса!"
                )
            except Exception as notify_e:
                logger.warning("Не удалось уведомить реферера %d: %s", referrer_id, notify_e)

    await update.message.reply_text(f"✅ Подписка на {days} дн. активирована!")
