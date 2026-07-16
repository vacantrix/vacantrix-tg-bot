# vacantrix/telegram/handlers.py
"""Обработчики команд и кнопок Telegram-бота (Базикс — шлюз экосистемы Vacantrix).

Все пользовательские тексты — в texts.py (голос Базикса, HTML).
Сетевые обращения к Supabase в обработчиках — через asyncio.to_thread
(requests синхронный, event loop не блокируем)."""

import asyncio
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from vacantrix.telegram import texts
from vacantrix.telegram.config import (
    ADMIN_ID, INSTRUCTION_URL, SUPPORT_URL, FAQ_URL, SITE_URL,
)
from vacantrix.telegram.supabase import (
    get_user_by_telegram, create_user, get_link_by_chat, get_profile,
    get_active_subscriptions, get_hh_free_used, get_avito_free_used,
    get_tools_catalog, get_news, set_telegram_muted,
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

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_NOTIFICATIONS, callback_data="notifications"),
         InlineKeyboardButton(texts.BTN_SUBSCRIPTION,  callback_data="subscription")],
        [InlineKeyboardButton(texts.BTN_TOOLS,         callback_data="tools_catalog"),
         InlineKeyboardButton(texts.BTN_NEWS,          callback_data="news")],
        [InlineKeyboardButton(texts.BTN_DOWNLOAD,      callback_data="download_latest"),
         InlineKeyboardButton(texts.BTN_FAQ,           callback_data="faq")],
        [InlineKeyboardButton(texts.BTN_SUPPORT,       callback_data="support"),
         InlineKeyboardButton(texts.BTN_ABOUT,         callback_data="about")],
    ])


def back_keyboard(*extra_rows) -> InlineKeyboardMarkup:
    rows = list(extra_rows)
    rows.append([InlineKeyboardButton(texts.BTN_BACK, callback_data="back_to_main")])
    return InlineKeyboardMarkup(rows)


def _notif_keyboard(linked: bool, muted: bool) -> InlineKeyboardMarkup:
    rows = []
    if linked:
        if muted:
            rows.append([InlineKeyboardButton(texts.BTN_UNMUTE, callback_data="notif_unmute")])
        else:
            rows.append([InlineKeyboardButton(texts.BTN_MUTE, callback_data="notif_mute")])
    rows.append([InlineKeyboardButton(texts.BTN_SITE_APPS, url=SITE_URL)])
    return back_keyboard(*rows)


# ── Основные команды ──────────────────────────────────────────────────────────

async def safe_delete(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def send_main_menu(message, name: str):
    return await message.reply_text(
        texts.main_menu(name),
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


async def _display_name(chat_id: int, fallback: str) -> str:
    """Имя для приветствия: display_name из vx_profiles (если привязан), иначе имя TG."""
    try:
        link = await asyncio.to_thread(get_link_by_chat, chat_id)
        if link:
            profile = await asyncio.to_thread(get_profile, link["user_id"])
            name = ((profile or {}).get("display_name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return fallback


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    user = update.effective_user

    # deep-link линковка уведомлений: /start <code> из приложения Vacantrix
    # («Уведомления → Подключить Telegram»). Гасим код → пишем chat_id.
    args = context.args or []
    if args:
        from vacantrix.telegram.supabase import redeem_link_code
        if await asyncio.to_thread(redeem_link_code, args[0].strip(), user.id, "telegram"):
            await update.message.reply_text(texts.LINKED_OK)
            return
        await update.message.reply_text(texts.LINK_FAIL)
        # дальше покажем обычное меню

    profile = await asyncio.to_thread(get_user_by_telegram, user.id)
    if not profile:
        await asyncio.to_thread(create_user, user.id)
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

    name = await _display_name(update.effective_chat.id, user.first_name)
    msg = await send_main_menu(update.message, name)
    context.user_data["last_bot_message"] = msg


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await send_main_menu(update.message, update.effective_user.first_name)


async def notifications_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await _send_notifications_status(update.message, update.effective_chat.id)


async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await _send_subscription(update.message, update.effective_chat.id)


# ── Админ-команды ─────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    s = await asyncio.to_thread(get_stats)
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
    ids = await asyncio.to_thread(get_all_telegram_ids)
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
    profile = await asyncio.to_thread(get_user_by_telegram, tg_id)
    if not profile:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    link = await asyncio.to_thread(get_link_by_chat, tg_id)
    if link:
        notif = "🔕 на паузе" if link.get("telegram_muted") else "🔔 подключены"
    else:
        notif = "— не подключены"
    await update.message.reply_text(
        f"👤 *Пользователь*\n\n"
        f"Telegram ID: `{profile.get('telegram_id')}`\n"
        f"Уведомления: {notif}",
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
    tools = await asyncio.to_thread(get_tools_admin)
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
    ok = await asyncio.to_thread(stop_tool, slug, reason)
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
    ok = await asyncio.to_thread(set_tool_field, args[0], "enabled", True)
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
    ok = await asyncio.to_thread(set_tool_field, args[0], "status", "hidden")
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
    ok = await asyncio.to_thread(set_tool_field, args[0], "status", "active")
    await update.message.reply_text(
        f"{'✅' if ok else '❌'} `{args[0]}` снова в магазине.", parse_mode="Markdown")


# ── Раздел «Уведомления» (статус + пауза) ─────────────────────────────────────

async def _send_notifications_status(message, chat_id: int) -> None:
    link = await asyncio.to_thread(get_link_by_chat, chat_id)
    linked = bool(link)
    muted = bool(link and link.get("telegram_muted"))
    await message.reply_text(
        texts.notif_status(linked, muted),
        reply_markup=_notif_keyboard(linked, muted),
        parse_mode="HTML",
    )


async def _toggle_mute(query, muted: bool) -> None:
    chat_id = query.message.chat_id
    link = await asyncio.to_thread(get_link_by_chat, chat_id)
    if not link:
        await _send_notifications_status(query.message, chat_id)
        return
    ok = await asyncio.to_thread(set_telegram_muted, link["user_id"], muted)
    if not ok:
        await query.message.reply_text(
            texts.MUTE_FAIL, reply_markup=_notif_keyboard(True, bool(link.get("telegram_muted"))))
        return
    await query.message.reply_text(
        texts.MUTED_ON if muted else texts.MUTED_OFF,
        reply_markup=_notif_keyboard(True, muted),
    )


# ── Раздел «Моя подписка» ─────────────────────────────────────────────────────

async def _send_subscription(message, chat_id: int) -> None:
    link = await asyncio.to_thread(get_link_by_chat, chat_id)
    if not link:
        await message.reply_text(
            texts.SUBSCRIPTION_NOT_LINKED,
            reply_markup=back_keyboard(
                [InlineKeyboardButton(texts.BTN_SITE_APPS, url=SITE_URL)]),
            parse_mode="HTML",
        )
        return
    uid = link["user_id"]
    subs = await asyncio.to_thread(get_active_subscriptions, uid)
    if subs is None:
        await message.reply_text(texts.SUBSCRIPTION_FAIL, reply_markup=back_keyboard())
        return
    profile = await asyncio.to_thread(get_profile, uid) or {}
    hh_used = await asyncio.to_thread(get_hh_free_used, uid)
    avito_used = await asyncio.to_thread(get_avito_free_used, uid)
    await message.reply_text(
        texts.subscription_card(subs, hh_used, avito_used, profile.get("display_name")),
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SITE, url=SITE_URL)]),
        parse_mode="HTML",
    )


# ── Обработчики кнопок ────────────────────────────────────────────────────────

async def _cb_notifications(query, context, user) -> None:
    await _send_notifications_status(query.message, query.message.chat_id)


async def _cb_notif_mute(query, context, user) -> None:
    await _toggle_mute(query, True)


async def _cb_notif_unmute(query, context, user) -> None:
    await _toggle_mute(query, False)


async def _cb_subscription(query, context, user) -> None:
    await _send_subscription(query.message, query.message.chat_id)


async def _cb_tools_catalog(query, context, user) -> None:
    rows = await asyncio.to_thread(get_tools_catalog)
    text = texts.TOOLS_FAIL if rows is None else texts.tools_catalog(rows)
    await query.message.reply_text(
        text,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SITE, url=SITE_URL)]),
        parse_mode="HTML",
    )


async def _cb_news(query, context, user) -> None:
    rows = await asyncio.to_thread(get_news)
    text = texts.NEWS_FAIL if rows is None else texts.news_list(rows)
    await query.message.reply_text(
        text,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SITE, url=SITE_URL)]),
        parse_mode="HTML",
    )


async def _cb_download_latest(query, context, user) -> None:
    await query.message.reply_text(
        texts.DOWNLOAD,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SITE, url=SITE_URL)],
            [InlineKeyboardButton(texts.BTN_INSTRUCTION, url=INSTRUCTION_URL)],
        ),
        parse_mode="HTML",
    )


async def _cb_instructions(query, context, user) -> None:
    await query.message.reply_text(
        texts.INSTRUCTIONS,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_INSTRUCTION, url=INSTRUCTION_URL)],
        ),
        parse_mode="HTML",
    )


async def _cb_support(query, context, user) -> None:
    await query.message.reply_text(
        texts.SUPPORT,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SUPPORT_LINK, url=SUPPORT_URL)],
        ),
        parse_mode="HTML",
    )


async def _cb_faq(query, context, user) -> None:
    await query.message.reply_text(
        texts.FAQ,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_FAQ_LINK, url=FAQ_URL)],
        ),
        parse_mode="HTML",
    )


async def _cb_about(query, context, user) -> None:
    await query.message.reply_text(
        texts.ABOUT,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


async def _cb_legacy_payment(query, context, user) -> None:
    """Кнопки покупки из старых сообщений бота — подсказываем новый путь."""
    await query.message.reply_text(
        texts.LEGACY_PAYMENT,
        reply_markup=back_keyboard(
            [InlineKeyboardButton(texts.BTN_SITE, url=SITE_URL)],
        ),
    )


async def _cb_legacy_link(query, context, user) -> None:
    """Кнопка «Привязать ID соискателя» из старых сообщений — привязка больше не нужна."""
    await query.message.reply_text(
        texts.LEGACY_LINK,
        reply_markup=back_keyboard(),
    )


# Устаревшие платёжные callback-и (кнопки в старых сообщениях)
_LEGACY_PAYMENT_CB = {"buy", "referral", "payment_history", "back_to_subscription"}

# Диспетчер callback-кнопок. «profile»/«link»/«instructions» — кнопки из СТАРЫХ
# сообщений: profile ведёт в «Моя подписка», link — объясняет, что привязка не нужна.
_CB_DISPATCH = {
    "notifications":   _cb_notifications,
    "notif_mute":      _cb_notif_mute,
    "notif_unmute":    _cb_notif_unmute,
    "subscription":    _cb_subscription,
    "tools_catalog":   _cb_tools_catalog,
    "news":            _cb_news,
    "profile":         _cb_subscription,
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
        await query.answer("Секундочку…", show_alert=False)
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
        await query.message.reply_text(texts.UNKNOWN_BUTTON)


# ── Обработчик текста ─────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    await update.message.reply_text(
        texts.TEXT_FALLBACK,
        reply_markup=main_menu_keyboard(),
    )
