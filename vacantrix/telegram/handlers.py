# vacantrix/telegram/handlers.py
"""Обработчики команд и кнопок Telegram-бота."""

import time
import uuid
import logging
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from vacantrix.telegram.config import (
    ADMIN_ID, VERSIONS_GROUP_ID, INSTRUCTION_URL, SUPPORT_URL, FAQ_URL,
)
from vacantrix.telegram.supabase import (
    get_user_by_telegram, get_user_by_applicant,
    create_user, link_applicant,
    update_subscription_by_telegram, count_referrals,
    check_subscription_status, get_referrer_telegram_id,
    add_subscription_days, save_latest_version, get_latest_version,
    reset_reminders, get_stats, get_all_telegram_ids, revoke_subscription,
)
from vacantrix.telegram.payments import send_subscription_invoice

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

SUBSCRIPTION_PLANS = {1: 50, 2: 100, 3: 150, 4: 200, 5: 250, 10: 350, 20: 500, 30: 600}

MAIN_MENU_TEXT = (
    "👋 Привет, {name}!\n\n"
    "Это бот Vacantrix — управление подпиской для приложения автооткликов на hh.ru.\n\n"
    "Выберите нужный раздел:"
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Скачать приложение",       callback_data="download_latest")],
        [InlineKeyboardButton("📖 Инструкция",               callback_data="instructions")],
        [InlineKeyboardButton("💳 Купить подписку",          callback_data="buy")],
        [InlineKeyboardButton("📊 Мой профиль",              callback_data="profile")],
        [InlineKeyboardButton("🔗 Привязать ID соискателя",  callback_data="link")],
        [InlineKeyboardButton("👥 Реферальная программа",    callback_data="referral")],
        [InlineKeyboardButton("💰 История платежей",         callback_data="payment_history")],
        [InlineKeyboardButton("🛠 Поддержка",                callback_data="support")],
        [InlineKeyboardButton("❓ FAQ",                      callback_data="faq")],
        [InlineKeyboardButton("ℹ️ О боте",                   callback_data="about")],
    ])


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🔥 {d} дн. — {p}₽", callback_data=f"sub_{d}_{p}")]
        for d, p in SUBSCRIPTION_PLANS.items()
    ]
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(rows)


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
    profile = get_user_by_telegram(user.id)
    if not profile:
        ref_code = str(uuid.uuid4())[:8]
        referred_by = None
        args = context.args or []
        if args and args[0].startswith("ref"):
            referred_by = args[0][3:]
        create_user(user.id, referral_code=ref_code, referred_by=referred_by)
        # Уведомляем администратора о новом пользователе
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"👤 Новый пользователь: {user.full_name} (ID: {user.id})"
                         + (f"\nРеферер: {referred_by}" if referred_by else ""),
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

async def add_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if len(args) != 2:
        await update.message.reply_text(
            "Использование: /add_sub <telegram_id> <дней>\nПример: /add_sub 123456789 30"
        )
        return
    try:
        tg_id = int(args[0])
        days = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Оба параметра должны быть числами.")
        return
    if days <= 0:
        await update.message.reply_text("❌ Количество дней должно быть > 0.")
        return
    if not get_user_by_telegram(tg_id):
        await update.message.reply_text("❌ Пользователь не найден.")
        return

    new_expire = datetime.now(timezone.utc) + timedelta(days=days)
    resp = update_subscription_by_telegram(tg_id, new_expire)
    if not resp or not resp.ok:
        await update.message.reply_text("❌ Ошибка Supabase при обновлении подписки.")
        return

    bonus = ""
    ref_id = get_referrer_telegram_id(tg_id)
    if ref_id:
        r = add_subscription_days(ref_id, days)
        bonus = (f"\n🎁 Бонус рефереру {ref_id}: +{days} дн."
                 if r and r.ok else f"\n⚠️ Не удалось начислить бонус {ref_id}.")

    reset_reminders(tg_id)
    try:
        await context.bot.send_message(
            chat_id=tg_id,
            text=f"✅ Администратор активировал вашу подписку на {days} дней.\n"
                 f"Действует до: {_fmt_date(new_expire.isoformat())}",
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ Подписка {tg_id} активирована на {days} дн.\n"
        f"До: {_fmt_date(new_expire.isoformat())}{bonus}"
    )


async def revoke_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Использование: /revoke_sub <telegram_id>")
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID должен быть числом.")
        return
    resp = revoke_subscription(tg_id)
    if resp and resp.ok:
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text="⚠️ Ваша подписка была отозвана администратором.",
            )
        except Exception:
            pass
        await update.message.reply_text(f"✅ Подписка пользователя {tg_id} отозвана.")
    else:
        await update.message.reply_text("❌ Ошибка при отзыве подписки.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or not _is_admin(update):
        await update.message.reply_text("⛔ Нет прав.")
        return
    s = get_stats()
    await update.message.reply_text(
        "📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: *{s['total']}*\n"
        f"✅ Активных подписок: *{s['active']}*\n"
        f"❌ Истёкших: *{s['expired']}*\n"
        f"🔅 Без подписки: *{s['no_sub']}*",
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
    ref_code = profile.get("referral_code", "—")
    ref_count = count_referrals(ref_code)
    await update.message.reply_text(
        f"👤 *Пользователь*\n\n"
        f"Telegram ID: `{profile.get('telegram_id')}`\n"
        f"Applicant ID: `{profile.get('applicant_id') or '—'}`\n"
        f"Реф. код: `{ref_code}`\n"
        f"Приглашено: {ref_count}\n"
        f"Подписка: {status}\n"
        f"Истекает: {_fmt_date(profile.get('subscription_expire'))}",
        parse_mode="Markdown",
    )


# ── Обработчик кнопок ─────────────────────────────────────────────────────────

async def _cb_buy(query, context, user) -> None:
    await query.message.reply_text(
        "💳 *Купить подписку*\n\n"
        "Подписка открывает доступ к приложению Vacantrix: автоматические отклики на hh.ru, "
        "сохранение прогресса и проверка подписки по ID соискателя.\n\n"
        "Скидки уже включены в тарифы. Выберите срок:",
        reply_markup=subscription_keyboard(),
        parse_mode="Markdown",
    )


async def _cb_profile(query, context, user) -> None:
    await _send_profile(query.message, user)


async def _cb_link(query, context, user) -> None:
    await query.message.reply_text(
        "🔗 *Привязать ID соискателя*\n\n"
        "Введите ID соискателя, который отображается в приложении Vacantrix.\n\n"
        "Зачем это нужно:\n"
        "• бот связывает оплату с вашим приложением\n"
        "• приложение проверяет подписку по этому ID\n"
        "• один ID — один аккаунт\n\n"
        "Чтобы отменить — введите /cancel.",
        reply_markup=back_keyboard(),
        parse_mode="Markdown",
    )
    context.user_data["awaiting_applicant_id"] = True


async def _cb_referral(query, context, user) -> None:
    profile = get_user_by_telegram(user.id)
    if not profile:
        await query.message.reply_text(
            "Профиль не найден. Используйте /start.", reply_markup=back_keyboard()
        )
        return
    ref_code = profile.get("referral_code") or str(uuid.uuid4())[:8]
    bot_me = await context.bot.get_me()
    ref_count = count_referrals(ref_code)
    await query.message.reply_text(
        "👥 *Реферальная программа*\n\n"
        "Ваша реферальная ссылка:\n"
        f"`https://t.me/{bot_me.username}?start=ref{ref_code}`\n\n"
        f"Приглашено друзей: *{ref_count}*\n\n"
        "Когда приглашённый купит подписку — вы получите такой же срок бесплатно.",
        reply_markup=back_keyboard(),
        parse_mode="Markdown",
    )


async def _cb_download_latest(query, context, user) -> None:
    chat_id, msg_id = get_latest_version()
    if chat_id and msg_id:
        try:
            await query.message.reply_text(
                "📥 Отправляю последнюю версию Vacantrix...",
                parse_mode="Markdown",
            )
            await context.bot.copy_message(
                chat_id=query.message.chat_id,
                from_chat_id=chat_id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL)],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")],
                ]),
            )
        except Exception as e:
            logger.error("Ошибка пересылки файла: %s", e)
            await query.message.reply_text(
                "❌ Не удалось отправить файл. Попробуйте позже или обратитесь в поддержку.",
                reply_markup=back_keyboard(
                    [InlineKeyboardButton("🛠 Поддержка", url=SUPPORT_URL)],
                ),
            )
    else:
        await query.message.reply_text(
            "📥 *Скачать приложение*\n\n"
            "Файл пока не загружен администратором. Обратитесь в поддержку.",
            reply_markup=back_keyboard(
                [InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL)],
                [InlineKeyboardButton("🛠 Поддержка", url=SUPPORT_URL)],
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
        "5\\. Оплатите подписку → нажмите «Проверить подписку» в приложении\n\n"
        "Подробная инструкция по кнопке ниже\\.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("📖 Открыть инструкцию", url=INSTRUCTION_URL)],
        ),
        parse_mode="MarkdownV2",
    )


async def _cb_payment_history(query, context, user) -> None:
    profile = get_user_by_telegram(user.id)
    if not profile:
        await query.message.reply_text(
            "Профиль не найден.", reply_markup=back_keyboard()
        )
        return
    status = check_subscription_status(profile)
    expire = _fmt_date(profile.get("subscription_expire"))
    await query.message.reply_text(
        "💰 *История платежей*\n\n"
        f"Текущий статус: {status}\n"
        f"Действует до: {expire}\n\n"
        "Детальная история транзакций хранится в уведомлениях Telegram и чеках YooKassa. "
        "По вопросам конкретного платежа обратитесь в поддержку.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("🛠 Поддержка", url=SUPPORT_URL)],
        ),
        parse_mode="Markdown",
    )


async def _cb_support(query, context, user) -> None:
    await query.message.reply_text(
        "🛠 *Поддержка*\n\n"
        "Обращайтесь, если:\n"
        "• не получается оплатить подписку\n"
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
        "*Как активируется подписка?*\n"
        "После успешной оплаты бот продлевает доступ автоматически.\n\n"
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
        "Этот бот управляет подпиской и доступом.\n\n"
        "Возможности:\n"
        "• Автоматические отклики с обходом защиты\n"
        "• Оплата через YooKassa\n"
        "• Реферальная программа\n"
        "• Уведомления об истечении подписки",
        reply_markup=back_keyboard(),
        parse_mode="Markdown",
    )


async def _cb_select_plan(query, context, data: str) -> None:
    """Выбор тарифа подписки (sub_N_P)."""
    parts = data.split("_")
    if len(parts) != 3:
        await query.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return
    try:
        days, price = int(parts[1]), int(parts[2])
    except ValueError:
        await query.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return
    await query.message.reply_text(
        f"🛒 Подписка на *{days} дн.* — *{price} ₽*\n\n"
        "После подтверждения бот выставит счёт YooKassa. "
        "Подписка активируется автоматически после оплаты.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_pay_{days}_{price}")],
            [InlineKeyboardButton("🔙 Назад к выбору", callback_data="back_to_subscription")],
        ]),
        parse_mode="Markdown",
    )


async def _cb_confirm_pay(query, context, update, data: str) -> None:
    """Выставление счёта (confirm_pay_N_P)."""
    parts = data.split("_")
    if len(parts) != 4:
        await query.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return
    try:
        days, price = int(parts[2]), int(parts[3])
    except ValueError:
        await query.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return
    await send_subscription_invoice(update, context, days, price)


# Диспетчер callback-кнопок
_CB_DISPATCH = {
    "buy":             _cb_buy,
    "profile":         _cb_profile,
    "link":            _cb_link,
    "referral":        _cb_referral,
    "download_latest": _cb_download_latest,
    "instructions":    _cb_instructions,
    "payment_history": _cb_payment_history,
    "support":         _cb_support,
    "faq":             _cb_faq,
    "about":           _cb_about,
    "back_to_subscription": lambda q, c, u: q.message.reply_text(
        "📅 Выберите срок подписки:", reply_markup=subscription_keyboard()
    ),
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

    # Префиксные маршруты
    if data.startswith("sub_"):
        await safe_delete(query.message)
        await _cb_select_plan(query, context, data)
        return

    if data.startswith("confirm_pay_"):
        await safe_delete(query.message)
        await _cb_confirm_pay(query, context, update, data)
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
        "✅ ID соискателя привязан. Теперь вы можете оформить подписку.",
        reply_markup=back_keyboard(
            [InlineKeyboardButton("📊 Мой профиль", callback_data="profile")],
            [InlineKeyboardButton("💳 Купить подписку", callback_data="buy")],
        ),
    )


# ── Сохранение версии из канала ───────────────────────────────────────────────

async def version_publisher_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != VERSIONS_GROUP_ID:
        return
    if not update.effective_message.document:
        return
    doc = update.effective_message.document
    result = save_latest_version(
        update.effective_chat.id,
        update.effective_message.message_id,
        doc.file_name,
    )
    if result and result.ok:
        logger.info("Сохранена новая версия: %s (msg_id=%s)",
                    doc.file_name, update.effective_message.message_id)
        try:
            await update.effective_message.reply_text(
                f"✅ Версия *{doc.file_name}* сохранена и доступна пользователям.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    else:
        logger.error("Не удалось сохранить версию %s", doc.file_name)


# ── Вспомогательный вывод профиля ────────────────────────────────────────────

async def _send_profile(message, user) -> None:
    profile = get_user_by_telegram(user.id)
    if not profile:
        await message.reply_text(
            "Профиль не найден. Используйте /start.", reply_markup=back_keyboard()
        )
        return
    status = check_subscription_status(profile)
    ref_code = profile.get("referral_code", "—")
    ref_count = count_referrals(ref_code)
    expire = _fmt_date(profile.get("subscription_expire"))
    text = (
        "📊 *Мой профиль*\n\n"
        f"Telegram ID: `{user.id}`\n"
        f"ID соискателя: `{profile.get('applicant_id') or 'не привязан'}`\n"
        f"Статус подписки: {status}\n"
        f"Действует до: {expire}\n"
        f"Приглашено друзей: {ref_count}\n\n"
    )
    if not profile.get("applicant_id"):
        text += "Привяжите ID соискателя для активации доступа."
    await message.reply_text(
        text,
        reply_markup=back_keyboard(
            [InlineKeyboardButton("💳 Продлить подписку", callback_data="buy")],
        ),
        parse_mode="Markdown",
    )
