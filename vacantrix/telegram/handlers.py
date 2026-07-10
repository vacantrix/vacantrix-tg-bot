# vacantrix/telegram/handlers.py
"""Обработчики команд и кнопок Telegram-бота."""

import time
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from vacantrix.telegram.config import (
    ADMIN_ID, INSTRUCTION_URL, SUPPORT_URL, FAQ_URL,
    HH_DOWNLOAD_URL, PLATFORM_DOWNLOAD_URL, SITE_URL,
)
from vacantrix.telegram.supabase import (
    get_user_by_telegram, get_user_by_applicant,
    create_user, link_applicant,
    check_subscription_status, get_stats, get_all_telegram_ids,
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

def _fmt_date(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M UTC")
    except ValueError:
        return iso_str


def _is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


def _is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID


# ── Клавиатуры ────────────────────────────────────────────────────────────────

MAIN_MENU_TEXT = (
    "👋 Привет, {name}!\n\n"
    "Это бот Vacantrix — скачивание приложений, инструкции и поддержка.\n"
    "Подписки оформляются в приложении Vacantrix Platform.\n\n"
    "Выберите нужный раздел:"
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Скачать приложение",       callback_data="download_latest")],
        [InlineKeyboardButton("📖 Инструкция",               callback_data="instructions")],
        [InlineKeyboardButton("📊 Мой профиль",              callback_data="profile")],
        [InlineKeyboardButton("🔗 Привязать ID соискателя",  callback_data="link")],
        [InlineKeyboardButton("🛠 Поддержка",                callback_data="support")],
        [InlineKeyboardButton("❓ FAQ",                      callback_data="faq")],
        [InlineKeyboardButton("ℹ️ О боте",                   callback_data="about")],
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
                "Сюда будут приходить уведомления по вашим мониторингам "
                "(новые объявления Авито и вакансии). Отключить — в приложении.")
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
    context.user_data.pop("awaiting_applicant_id", None)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    context.user_data.pop("awaiting_applicant_id", None)
    await send_main_menu(update.message, update.effective_user.first_name)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает любое ожидающее состояние."""
    if not _is_private(update):
        return
    context.user_data.pop("awaiting_applicant_id", None)
    await update.message.reply_text(
        "Действие отменено. Открываю главное меню.",
        reply_markup=main_menu_keyboard(),
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await _send_profile(update.message, update.effective_user)


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
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    if not context.args:
        await update.message.reply_text(
            "Использование: /broadcast <текст>\n"
            "Текст будет отправлен всем пользователям бота."
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
        await update.message.reply_text(
            "Использование: /find_user <telegram_id или applicant_id>"
        )
        return
    query_val = args[0]
    profile = None
    try:
        profile = get_user_by_telegram(int(query_val))
    except ValueError:
        pass
    if not profile:
        profile = get_user_by_applicant(query_val)
    if not profile:
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    status = check_subscription_status(profile)
    await update.message.reply_text(
        f"👤 *Пользователь*\n\n"
        f"Telegram ID: `{profile.get('telegram_id')}`\n"
        f"Applicant ID: `{profile.get('applicant_id') or '—'}`\n"
        f"Подписка: {status}\n"
        f"Истекает: {_fmt_date(profile.get('subscription_expire'))}",
        parse_mode="Markdown",
    )


# ── Удалённое управление: стоп-кран + арбитраж ────────────────────────────────

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
                 "/hide <slug> · /show <slug> · /disputes")
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


async def disputes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/disputes — спорные сделки; /resolve для решения."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    from vacantrix.telegram.supabase import get_open_disputes
    deals = get_open_disputes()
    if not deals:
        await update.message.reply_text("✅ Открытых споров нет.")
        return
    lines = ["⚖️ *Споры на арбитраж*\n"]
    for d in deals[:20]:
        lines.append(f"`{d['id']}`\n  сумма {d.get('amount')} ₽ · задача "
                     f"{str(d.get('task_id'))[:8]}")
    lines.append("\nРешение: /resolve <deal_id> <release|refund> <причина>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def resolve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resolve <deal_id> <release|refund> <причина> — арбитраж спора."""
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if len(args) < 3 or args[1] not in ("release", "refund"):
        await update.message.reply_text(
            "Использование: /resolve <deal_id> <release|refund> <причина>\n"
            "release — деньги исполнителю, refund — заказчику.")
        return
    deal_id, decision, reason = args[0], args[1], " ".join(args[2:])
    from vacantrix.telegram.supabase import resolve_dispute
    ok, note = resolve_dispute(deal_id, decision, reason)
    await update.message.reply_text(
        f"{'✅ Арбитраж выполнен' if ok else '❌ Не удалось'}: {note}")


# ── Обработчик кнопок ─────────────────────────────────────────────────────────

async def _cb_profile(query, context, user) -> None:
    await _send_profile(query.message, user)


async def _cb_link(query, context, user) -> None:
    await query.message.reply_text(
        "🔗 *Привязать ID соискателя*\n\n"
        "Введите ID соискателя, который отображается в приложении Vacantrix.\n\n"
        "Зачем это нужно:\n"
        "• приложение проверяет подписку по этому ID\n"
        "• один ID — один аккаунт\n\n"
        "Чтобы отменить — введите /cancel.",
        reply_markup=back_keyboard(),
        parse_mode="Markdown",
    )
    context.user_data["awaiting_applicant_id"] = True


async def _cb_download_latest(query, context, user) -> None:
    await query.message.reply_text(
        "📥 *Скачать приложение*\n\n"
        "Прямые ссылки на последние версии:\n"
        "• *Vacantrix* — автоотклики на hh.ru\n"
        "• *Vacantrix Platform* — установщик всех инструментов экосистемы\n\n"
        "Подписки оформляются в приложении Vacantrix Platform.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🤖 Vacantrix (HH-бот)", url=HH_DOWNLOAD_URL)],
            [InlineKeyboardButton("🚀 Vacantrix Platform", url=PLATFORM_DOWNLOAD_URL)],
            [InlineKeyboardButton("🌐 Сайт Vacantrix", url=SITE_URL)],
            [InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_instructions(query, context, user) -> None:
    await query.message.reply_text(
        "📖 *Инструкция по настройке*\n\n"
        "1\\. Скачайте приложение в разделе «Скачать приложение»\n"
        "2\\. Запустите Vacantrix и авторизуйтесь в hh\\.ru\n"
        "3\\. Скопируйте ID соискателя из приложения\n"
        "4\\. Вернитесь сюда → «Привязать ID соискателя»\n"
        "5\\. Подписка оформляется в приложении Vacantrix Platform\n\n"
        "Подробная инструкция по кнопке ниже\\.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("📖 Открыть инструкцию", url=INSTRUCTION_URL)],
        ),
        parse_mode="MarkdownV2",
    )


async def _cb_support(query, context, user) -> None:
    await query.message.reply_text(
        "🛠 *Поддержка*\n\n"
        "Обращайтесь, если:\n"
        "• приложение не видит активную подписку\n"
        "• не удаётся войти в hh\\.ru\n"
        "• нужна помощь с установкой\n\n"
        "Подготовьте Telegram ID и ID соискателя из раздела «Мой профиль»\\.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🔗 Написать в поддержку", url=SUPPORT_URL)],
        ),
        parse_mode="MarkdownV2",
    )


async def _cb_faq(query, context, user) -> None:
    await query.message.reply_text(
        "❓ *Часто задаваемые вопросы*\n\n"
        "*Где оформить подписку?*\n"
        "В приложении Vacantrix Platform. Установщик — в разделе «Скачать приложение».\n\n"
        "*Зачем нужен ID соискателя?*\n"
        "По нему приложение проверяет, что у вас есть активная подписка.\n\n"
        "*Почему приложение просит войти заново?*\n"
        "Сессия hh.ru истекает. Нажмите «Обновить сессию» в приложении.\n\n"
        "*Где скачать новую версию?*\n"
        "В разделе «Скачать приложение».",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🔗 Полный FAQ", url=FAQ_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_about(query, context, user) -> None:
    await query.message.reply_text(
        "ℹ️ *О боте Vacantrix*\n\n"
        "Приложение Vacantrix автоматизирует отклики на вакансии hh.ru. "
        "Этот бот — информационный помощник экосистемы Vacantrix.\n\n"
        "Возможности:\n"
        "• Скачивание приложений (HH-бот, Vacantrix Platform)\n"
        "• Инструкции и FAQ\n"
        "• Справка о статусе подписки\n\n"
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
            [InlineKeyboardButton("🚀 Скачать Vacantrix Platform", url=PLATFORM_DOWNLOAD_URL)],
        ),
    )


# Устаревшие платёжные callback-и (кнопки в старых сообщениях)
_LEGACY_PAYMENT_CB = {"buy", "referral", "payment_history", "back_to_subscription"}

# Диспетчер callback-кнопок
_CB_DISPATCH = {
    "profile":         _cb_profile,
    "link":            _cb_link,
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
    if not context.user_data.get("awaiting_applicant_id"):
        await update.message.reply_text(
            "Используйте кнопки меню или команду /menu.",
            reply_markup=main_menu_keyboard(),
        )
        return

    applicant_id = update.message.text.strip()
    if not applicant_id or len(applicant_id) > 64:
        await update.message.reply_text(
            "❌ ID выглядит некорректно. Скопируйте его из приложения и отправьте ещё раз.",
            reply_markup=back_keyboard(),
        )
        return

    existing = get_user_by_applicant(applicant_id)
    if existing and existing["telegram_id"] != update.effective_user.id:
        await update.message.reply_text(
            "❌ Этот ID соискателя уже привязан к другому аккаунту.",
            reply_markup=back_keyboard(),
        )
        context.user_data["awaiting_applicant_id"] = False
        return

    resp = link_applicant(update.effective_user.id, applicant_id)
    context.user_data["awaiting_applicant_id"] = False

    if not resp or not resp.ok:
        await update.message.reply_text(
            "❌ Не удалось сохранить ID. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=back_keyboard([InlineKeyboardButton("🛠 Поддержка", url=SUPPORT_URL)]),
        )
        return

    await update.message.reply_text(
        "✅ ID соискателя привязан.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("📊 Мой профиль", callback_data="profile")],
        ),
    )


# ── Вспомогательный вывод профиля ────────────────────────────────────────────

async def _send_profile(message, user) -> None:
    profile = get_user_by_telegram(user.id)
    if not profile:
        await message.reply_text(
            "Профиль не найден. Используйте /start.", reply_markup=back_keyboard()
        )
        return
    status = check_subscription_status(profile)
    expire = _fmt_date(profile.get("subscription_expire"))
    text = (
        "📊 *Мой профиль*\n\n"
        f"Telegram ID: `{user.id}`\n"
        f"ID соискателя: `{profile.get('applicant_id') or 'не привязан'}`\n"
        f"Статус подписки: {status}\n"
        f"Действует до: {expire}\n\n"
        "Статус показан как справка. Подписки теперь оформляются "
        "в приложении Vacantrix Platform."
    )
    await message.reply_text(
        text,
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🚀 Скачать Vacantrix Platform", url=PLATFORM_DOWNLOAD_URL)],
        ),
        parse_mode="Markdown",
    )
