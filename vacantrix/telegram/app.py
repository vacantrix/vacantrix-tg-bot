# vacantrix/telegram/app.py
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

from vacantrix.telegram.config import BOT_TOKEN
from vacantrix.telegram.handlers import (
    start, menu_command, cancel_command, profile_command,
    stats_command, broadcast_command, find_user,
    apps_command, stop_command, unstop_command, hide_command, show_command,
    disputes_command, resolve_command,
    button_handler, handle_text,
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


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Пользовательские команды
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("menu",    menu_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("cancel",  cancel_command))

    # Администраторские команды
    app.add_handler(CommandHandler("stats",     stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("find_user", find_user))

    # Удалённое управление (стоп-кран + арбитраж) — только ADMIN_ID
    app.add_handler(CommandHandler("apps",     apps_command))
    app.add_handler(CommandHandler("stop",     stop_command))
    app.add_handler(CommandHandler("unstop",   unstop_command))
    app.add_handler(CommandHandler("hide",     hide_command))
    app.add_handler(CommandHandler("show",     show_command))
    app.add_handler(CommandHandler("disputes", disputes_command))
    app.add_handler(CommandHandler("resolve",  resolve_command))

    # Кнопки и текст
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    logger.info("Бот запущен (информационный режим, без оплаты)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
