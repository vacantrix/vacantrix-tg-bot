# vacantrix/telegram/handlers.py
"""Обработчики команд и кнопок Telegram-бота (шлюз экосистемы Vacantrix)."""

import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from vacantrix.telegram.config import (
    ADMIN_ID, INSTRUCTION_URL, SUPPORT_URL, FAQ_URL, SITE_URL,
)
from vacantrix.telegram.supabase import (
    get_user_by_telegram, create_user, get_link_by_chat,
    get_stats, get_all_telegram_ids,
)

logger = logging.getLogger(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
_rate_map: dict[int, float] = {}
_RATE_LIMIT = 0.8  # сек между действиями одного пользователя

def _check_rate(user_id: int) -> bool:
    now = time.time()
    if now - _rate_map.get(user_id, 0) < _RATE_LIMIT:
        return False
    _rate_map[user_id] = now
    return True


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


def _is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID


# ── Клавиатуры ────────────────────────────────────────────────────────────────

MAIN_MENU_TEXT = (
    "👋 Привет, {name}!\n\n"
    "Это бот экосистемы Vacantrix. Сюда приходят уведомления ваших приложений: "
    "результаты мониторингов, оплата, продление подписки, новые версии.\n\n"
    "Выберите раздел:"
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Уведомления",         callback_data="notifications")],
        [InlineKeyboardButton("📥 Скачать приложение",  callback_data="download_latest")],
        [InlineKeyboardButton("📖 Инструкция",          callback_data="instructions")],
        [InlineKeyboardButton("❓ FAQ",                 callback_data="faq")],
        [InlineKeyboardButton("🛠 Поддержка",           callback_data="support")],
        [InlineKeyboardButton("ℹ️ О боте",              callback_data="about")],
    ])


def back_keyboard(*extra_rows) -> InlineKeyboardMarkup:
    rows = list(extra_rows)
    rows.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(rows)


# ── Основные команды ──────────────────────────────────────────────────────────

async def safe_delete(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def send_main_menu(message, name: str):
    return await message.reply_text(
        MAIN_MENU_TEXT.format(name=name or "друг"),
        reply_markup=main_menu_keyboard(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    user = update.effective_user

    # deep-link линковка уведомлений: /start <code> из приложения Vacantrix
    # («Уведомления → Подключить Telegram»). Гасим код → пишем chat_id.
    args = context.args or []
    if args:
        from vacantrix.telegram.supabase import redeem_link_code
        if redeem_link_code(args[0].strip(), user.id, "telegram"):
            await update.message.reply_text(
                "✅ Telegram подключён к Vacantrix.\n\n"
                "Сюда будут приходить уведомления ваших приложений: результаты "
                "мониторингов (Авито и вакансии), оплата, продление подписки, "
                "новые версии. Отключить — в приложении.")
            return
        await update.message.reply_text(
            "⚠️ Ссылка подключения недействительна или уже использована.\n"
            "Откройте приложение: Уведомления → «Подключить Telegram» — и попробуйте снова.")
        # дальше покажем обычное меню

    profile = get_user_by_telegram(user.id)
    if not profile:
        create_user(user.id)
        # Уведомляем администратора о новом пользователе
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"👤 Новый пользователь: {user.full_name} (ID: {user.id})",
                )
            except Exception:
                pass

    # Удаляем предыдущее меню
    prev = context.user_data.get("last_bot_message")
    if prev:
        await safe_delete(prev)

    msg = await send_main_menu(update.message, user.first_name)
    context.user_data["last_bot_message"] = msg


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await send_main_menu(update.message, update.effective_user.first_name)


async def notifications_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await _send_notifications_status(update.message, update.effective_chat.id)


# ── Админ-команды ─────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    s = get_stats()
    await update.message.reply_text(
        "📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: *{s['total']}*",
        parse_mode="Markdown",
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <текст> — СЕРВИСНАЯ рассылка всем пользователям бота.

    ⚠️ Только сервисные/транзакционные сообщения. Рекламные рассылки требуют
    согласия на рекламу с каналом «Telegram» (38-ФЗ ст. 18) — их через эту
    команду не отправлять, пока Согласие №2 не покрывает Telegram."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    if not context.args:
        await update.message.reply_text(
            "Использование: /broadcast <текст>\n"
            "Текст будет отправлен всем пользователям бота.\n"
            "⚠️ Только сервисные сообщения (не реклама)."
        )
        return
    text = " ".join(context.args)
    ids = get_all_telegram_ids()
    status_msg = await update.message.reply_text(f"⏳ Рассылка {len(ids)} пользователям...")
    sent = failed = 0
    for tg_id in ids:
        try:
            await context.bot.send_message(chat_id=tg_id, text=text)
            sent += 1
        except Exception:
            failed += 1
    await status_msg.edit_text(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {sent}, ошибок: {failed}."
    )


async def find_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /find_user <telegram_id>")
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id должен быть числом.")
        return
    profile = get_user_by_telegram(tg_id)
    if not profile:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    link = get_link_by_chat(tg_id)
    await update.message.reply_text(
        f"👤 *Пользователь*\n\n"
        f"Telegram ID: `{profile.get('telegram_id')}`\n"
        f"Уведомления: {'🔔 подключены' if link else '🔕 не подключены'}",
        parse_mode="Markdown",
    )


# ── Удалённое управление: стоп-кран ───────────────────────────────────────────

_STORE_ICON = {"active": "🟢", "coming_soon": "🟡", "hidden": "🔴"}


async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/apps — статус всех приложений (магазин + стоп-кран)."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    from vacantrix.telegram.supabase import get_tools_admin
    tools = get_tools_admin()
    if not tools:
        await update.message.reply_text("Не удалось получить список.")
        return
    lines = ["📦 *Приложения*\n"]
    for t in tools:
        store = _STORE_ICON.get(t.get("status"), "❔")
        users = "✅ работает" if t.get("enabled") is not False else "⛔ ЗАБЛОКИРОВАНО"
        lines.append(f"{store} `{t['slug']}` — магазин: {t.get('status')}, "
                     f"у пользователей: {users}")
    lines.append("\n/stop <slug> [причина] · /unstop <slug>\n"
                 "/hide <slug> · /show <slug>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop <slug> [причина] — стоп-кран: заблокировать у пользователей."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Использование: /stop <slug> [причина для стоп-экрана]")
        return
    slug = args[0]
    if slug == "platform":
        await update.message.reply_text("⛔ Платформу-ядро блокировать нельзя.")
        return
    reason = " ".join(args[1:]) or "Приложение временно отключено."
    from vacantrix.telegram.supabase import stop_tool
    ok = stop_tool(slug, reason)
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} `{slug}` "
        f"{'заблокирован у пользователей' if ok else 'не удалось'}.\n"
        f"Сообщение: {reason}", parse_mode="Markdown")


async def unstop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unstop <slug> — снять стоп-кран."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /unstop <slug>")
        return
    from vacantrix.telegram.supabase import set_tool_field
    ok = set_tool_field(args[0], "enabled", True)
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} `{args[0]}` "
        f"{'снова работает' if ok else 'не удалось'}.", parse_mode="Markdown")


async def hide_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hide <slug> — убрать из магазина (status=hidden)."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if not args or args[0] == "platform":
        await update.message.reply_text("Использование: /hide <slug> (кроме platform)")
        return
    from vacantrix.telegram.supabase import set_tool_field
    ok = set_tool_field(args[0], "status", "hidden")
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} `{args[0]}` скрыт из магазина.", parse_mode="Markdown")


async def show_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/show <slug> — вернуть в магазин (status=active)."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /show <slug>")
        return
    from vacantrix.telegram.supabase import set_tool_field
    ok = set_tool_field(args[0], "status", "active")
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} `{args[0]}` снова в магазине.", parse_mode="Markdown")


# ── Раздел «Уведомления» ──────────────────────────────────────────────────────

async def _send_notifications_status(message, chat_id: int) -> None:
    link = get_link_by_chat(chat_id)
    if link:
        text = (
            "🔔 *Уведомления*\n\n"
            "✅ Telegram подключён к вашему аккаунту Vacantrix.\n\n"
            "Сюда приходят:\n"
            "• результаты ваших мониторингов — новые объявления Авито и вакансии\n"
            "• события платформы: оплата, продление подписки, новые версии\n\n"
            "Отключить можно в приложении: Уведомления → «Отключить»."
        )
    else:
        text = (
            "🔔 *Уведомления*\n\n"
            "Telegram пока не подключён к аккаунту Vacantrix.\n\n"
            "Как подключить:\n"
            "1. Откройте приложение Vacantrix (например, Monitor)\n"
            "2. Раздел «Уведомления» → «Подключить Telegram»\n"
            "3. Перейдите по ссылке из приложения — бот всё сделает сам\n\n"
            "После подключения сюда будут приходить результаты мониторингов "
            "и события платформы (оплата, подписка, новые версии)."
        )
    await message.reply_text(
        text,
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🌐 Скачать приложения", url=SITE_URL)],
        ),
        parse_mode="Markdown",
    )


# ── Обработчик кнопок ─────────────────────────────────────────────────────────

async def _cb_notifications(query, context, user) -> None:
    await _send_notifications_status(query.message, query.message.chat_id)


async def _cb_download_latest(query, context, user) -> None:
    await query.message.reply_text(
        "📥 *Скачать приложение*\n\n"
        "Все приложения экосистемы — на сайте Vacantrix:\n"
        "• *Vacantrix Platform* — установщик всех инструментов\n"
        "• *Vacantrix* — автоотклики на hh.ru\n"
        "• *Monitor, Analytics, Publisher* и другие\n\n"
        "Подписки оформляются в приложении Vacantrix Platform.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🌐 Скачать на сайте", url=SITE_URL)],
            [InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_instructions(query, context, user) -> None:
    await query.message.reply_text(
        "📖 *Как начать*\n\n"
        "1. Скачайте установщик Vacantrix Platform на сайте\n"
        "2. Войдите или создайте аккаунт — он один на все приложения\n"
        "3. Установите нужный инструмент из каталога\n"
        "4. В приложении подключите Telegram-уведомления "
        "(Уведомления → «Подключить Telegram»)\n\n"
        "Подробная инструкция по кнопке ниже.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("📖 Открыть инструкцию", url=INSTRUCTION_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_support(query, context, user) -> None:
    await query.message.reply_text(
        "🛠 *Поддержка*\n\n"
        "Обращайтесь, если:\n"
        "• не приходят уведомления\n"
        "• приложение не видит активную подписку\n"
        "• нужна помощь с установкой\n\n"
        "Укажите email вашего аккаунта Vacantrix — так мы найдём вас быстрее.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🔗 Написать в поддержку", url=SUPPORT_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_faq(query, context, user) -> None:
    await query.message.reply_text(
        "❓ *Часто задаваемые вопросы*\n\n"
        "*Где оформить подписку?*\n"
        "В приложении Vacantrix Platform. Установщик — на сайте vacantrix.ru.\n\n"
        "*Как получать уведомления в Telegram?*\n"
        "В приложении: Уведомления → «Подключить Telegram» → перейти по ссылке.\n\n"
        "*Почему приложение просит войти заново?*\n"
        "Сессия площадки (hh.ru и т.п.) истекает — нажмите «Обновить сессию».\n\n"
        "*Где скачать новую версию?*\n"
        "На сайте vacantrix.ru — приложения сами подскажут об обновлении.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🔗 Полный FAQ", url=FAQ_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_about(query, context, user) -> None:
    await query.message.reply_text(
        "ℹ️ *О боте Vacantrix*\n\n"
        "Это шлюз уведомлений экосистемы Vacantrix — инструментов для поиска "
        "работы и продвижения (автоотклики hh.ru/Авито, мониторинг объявлений, "
        "аналитика, кросс-постинг).\n\n"
        "Возможности:\n"
        "• уведомления приложений прямо в Telegram\n"
        "• скачивание приложений, инструкции, поддержка\n\n"
        "Подписки оформляются в приложении Vacantrix Platform.",
        reply_markup=back_keyboard(),
        parse_mode="Markdown",
    )


async def _cb_legacy_payment(query, context, user) -> None:
    """Кнопки покупки из старых сообщений бота — подсказываем новый путь."""
    await query.message.reply_text(
        "💳 Бот больше не продаёт подписки.\n\n"
        "Подписки теперь оформляются в приложении Vacantrix Platform.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🌐 Скачать на сайте", url=SITE_URL)],
        ),
    )


async def _cb_legacy_link(query, context, user) -> None:
    """Кнопка «Привязать ID соискателя» из старых сообщений — привязка больше не нужна."""
    await query.message.reply_text(
        "🔗 Привязка ID соискателя больше не нужна.\n\n"
        "Подписка проверяется автоматически через ваш аккаунт Vacantrix Platform.",
        reply_markup=back_keyboard(),
    )


# Устаревшие платёжные callback-и (кнопки в старых сообщениях)
_LEGACY_PAYMENT_CB = {"buy", "referral", "payment_history", "back_to_subscription"}

# Диспетчер callback-кнопок. «profile»/«link» — кнопки из СТАРЫХ сообщений:
# profile теперь ведёт в «Уведомления», link — объясняет, что привязка не нужна.
_CB_DISPATCH = {
    "notifications":   _cb_notifications,
    "profile":         _cb_notifications,
    "link":            _cb_legacy_link,
    "download_latest": _cb_download_latest,
    "instructions":    _cb_instructions,
    "support":         _cb_support,
    "faq":             _cb_faq,
    "about":           _cb_about,
}


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = query.data

    # Rate limiting
    if not _check_rate(user.id):
        await query.answer("Подождите секунду...", show_alert=False)
        return

    if data == "back_to_main":
        await safe_delete(query.message)
        await send_main_menu(query.message, user.first_name)
        return

    # Кнопки покупки из старых сообщений
    if data in _LEGACY_PAYMENT_CB or data.startswith(("sub_", "confirm_pay_")):
        await safe_delete(query.message)
        await _cb_legacy_payment(query, context, user)
        return

    handler = _CB_DISPATCH.get(data)
    if handler:
        await safe_delete(query.message)
        await handler(query, context, user)
    else:
        await query.message.reply_text("Неизвестная команда. Используйте /menu.")


# ── Обработчик текста ─────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await update.message.reply_text(
        "Используйте кнопки меню или команду /menu.",
        reply_markup=main_menu_keyboard(),
    )
