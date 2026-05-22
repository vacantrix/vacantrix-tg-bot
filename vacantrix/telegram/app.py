# vacantrix/telegram/app.py
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, filters, ContextTypes,
)

from vacantrix.telegram.config import BOT_TOKEN, VERSIONS_GROUP_ID
from vacantrix.telegram.handlers import (
    start, menu_command, cancel_command, profile_command,
    add_sub, revoke_sub, stats_command, broadcast_command, find_user,
    button_handler, handle_text, version_publisher_handler,
)
from vacantrix.telegram.payments import (
    get_provider_token_mode, pre_checkout_handler, successful_payment_handler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Ошибка бота. update=%s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Произошла ошибка. Попробуйте /menu.",
            )
        except Exception:
            pass


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",     "Запустить бота"),
        BotCommand("menu",      "Главное меню"),
        BotCommand("profile",   "Мой профиль и подписка"),
        BotCommand("cancel",    "Отменить текущее действие"),
    ])


async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отправляет напоминания об истечении подписки.
    Использует пороговый подход: отправляет при первом попадании в окно,
    флаги предотвращают повторную отправку.
    """
    from vacantrix.telegram.supabase import (
        get_users_with_expiring_subscription,
        get_reminder_status,
        set_reminder_sent,
    )
    for user in get_users_with_expiring_subscription():
        tg_id = user["telegram_id"]
        days_left = user["days_left"]
        reminders = get_reminder_status(tg_id) or {}

        # Определяем нужный тип напоминания (порог, а не точечное окно)
        if days_left <= 0.5 and not reminders.get("remind_12h_sent"):
            kind = "remind_12h_sent"
            text = "🔔 Ваша подписка истечёт *сегодня через 12 часов*. Продлите прямо сейчас!"
        elif days_left <= 1 and not reminders.get("remind_1d_sent"):
            kind = "remind_1d_sent"
            text = "⏰ Ваша подписка истечёт *завтра*. Рекомендуем продлить заранее."
        elif days_left <= 3 and not reminders.get("remind_3d_sent"):
            kind = "remind_3d_sent"
            text = "⚠️ Ваша подписка истечёт *через 3 дня*. Не забудьте продлить."
        else:
            continue

        try:
            await context.bot.send_message(
                chat_id=tg_id, text=text, parse_mode="Markdown"
            )
            set_reminder_sent(tg_id, kind)
        except Exception as e:
            logger.error("Не удалось отправить напоминание %s: %s", tg_id, e)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Пользовательские команды
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("menu",    menu_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("cancel",  cancel_command))

    # Администраторские команды
    app.add_handler(CommandHandler("add_sub",    add_sub))
    app.add_handler(CommandHandler("revoke_sub", revoke_sub))
    app.add_handler(CommandHandler("stats",      stats_command))
    app.add_handler(CommandHandler("broadcast",  broadcast_command))
    app.add_handler(CommandHandler("find_user",  find_user))

    # Кнопки и текст
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(
        filters.Chat(chat_id=VERSIONS_GROUP_ID) & filters.Document.ALL,
        version_publisher_handler,
    ))

    # Платежи YooKassa
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Фоновые задачи
    if app.job_queue:
        app.job_queue.run_repeating(check_expiring_subscriptions, interval=3600, first=30)

    app.add_error_handler(error_handler)

    logger.info("Бот запущен | payment mode: %s", get_provider_token_mode())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
