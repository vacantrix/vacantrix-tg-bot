# tests/test_bot_texts.py
"""Оффлайн-проверки текстов Базикса и клавиатур бота (без сети и Telegram API)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vacantrix.telegram import texts  # noqa: E402


# ── Профиль BotFather: лимиты Telegram ────────────────────────────────────────

def test_botfather_limits():
    assert 0 < len(texts.BOT_NAME) <= 64
    assert 0 < len(texts.BOT_SHORT_DESCRIPTION) <= 120
    assert 0 < len(texts.BOT_DESCRIPTION) <= 512


def test_bot_commands_shape():
    assert texts.BOT_COMMANDS, "команды не должны быть пустыми"
    for cmd, desc in texts.BOT_COMMANDS:
        assert cmd.isascii() and cmd.islower() and " " not in cmd
        assert 3 <= len(desc) <= 256  # лимит Telegram на описание команды


# ── Форматтеры ────────────────────────────────────────────────────────────────

def test_main_menu_escapes_name():
    out = texts.main_menu("<b>Рома</b>")
    assert "Базикс" in out
    assert "<b>Рома</b>" not in out          # HTML из имени экранирован
    assert texts.main_menu(None)             # без имени тоже живо
    assert "None" not in texts.main_menu(None)


def test_subscription_card_full():
    subs = [{"expires_at": "2026-08-12T10:00:00+00:00",
             "tools": {"slug": "publisher", "name": "Vacantrix Publisher"},
             "plans": {"name": "Pro"}}]
    out = texts.subscription_card(subs, hh_used=3, avito_used=0, display_name="Рома")
    assert "Vacantrix Publisher" in out
    assert "12.08.2026" in out
    assert "«Pro»" in out
    assert "осталось 7 из 10" in out         # HH: 10-3
    assert "осталось 10 из 10" in out        # Avito: 10-0
    assert "Рома" in out
    assert "None" not in out


def test_subscription_card_empty_and_partial():
    out = texts.subscription_card([], hh_used=None, avito_used=None)
    assert "подписок пока нет" in out.lower() or "Активных подписок" in out
    assert "из 10" not in out                # лимиты недоступны — не показываем
    assert "None" not in out
    # расход больше лимита не уводит в минус
    out2 = texts.subscription_card([], hh_used=15, avito_used=None)
    assert "осталось 0 из 10" in out2


def test_subscription_card_broken_rows():
    subs = [{"expires_at": None, "tools": None, "plans": None}]
    out = texts.subscription_card(subs, hh_used=0, avito_used=None)
    assert "None" not in out
    assert "Инструмент" in out               # фолбэк имени


def test_tools_catalog():
    rows = [
        {"name": "Vacantrix", "status": "active", "tagline": "Автоотклики на hh.ru"},
        {"name": "Tasks", "status": "coming_soon", "tagline": ""},
        {"name": "Секретный", "status": "hidden", "tagline": "не показывать"},
        {"name": "", "status": "active"},    # битая строка — скип
    ]
    out = texts.tools_catalog(rows)
    assert "🟢 <b>Vacantrix</b> — Автоотклики на hh.ru" in out
    assert "🟡 <b>Tasks</b> — скоро!" in out
    assert "Секретный" not in out
    assert "None" not in out


def test_tools_catalog_empty():
    out = texts.tools_catalog([])
    assert "сайте" in out.lower() or "позже" in out.lower()


def test_news_list():
    rows = [
        {"title": "Вышел Monitor", "body": "х" * 500, "tag": "release",
         "published_at": "2026-07-12"},
        {"title": "", "body": "без заголовка — скип"},
        {"title": "Анонс <тег>", "body": None, "tag": "announce",
         "published_at": None},
    ]
    out = texts.news_list(rows)
    assert "🚀 12.07.2026 — <b>Вышел Monitor</b>" in out
    assert "…" in out                        # длинный body обрезан
    assert "х" * 201 not in out
    assert "Анонс &lt;тег&gt;" in out        # HTML экранирован
    assert "None" not in out


def test_news_list_empty():
    assert "тихо" in texts.news_list([]).lower()


def test_notif_status_variants():
    for linked, muted in ((False, False), (True, False), (True, True)):
        out = texts.notif_status(linked, muted)
        assert "<b>Уведомления</b>" in out
    assert "паузе" in texts.notif_status(True, True)
    assert "Подключить Telegram" in texts.notif_status(False)


# ── Клавиатуры и диспетчер (нужны telegram + .env; смоук) ─────────────────────

def test_keyboards_and_dispatch():
    from vacantrix.telegram import handlers

    menu = handlers.main_menu_keyboard()
    menu_cb = {b.callback_data for row in menu.inline_keyboard for b in row}
    assert menu_cb == {"notifications", "subscription", "tools_catalog", "news",
                       "download_latest", "faq", "support", "about"}
    # каждая кнопка меню имеет обработчик
    assert menu_cb <= set(handlers._CB_DISPATCH)

    nk = handlers._notif_keyboard(linked=True, muted=False)
    flat = [b for row in nk.inline_keyboard for b in row]
    assert any(b.callback_data == "notif_mute" for b in flat)
    nk2 = handlers._notif_keyboard(linked=True, muted=True)
    flat2 = [b for row in nk2.inline_keyboard for b in row]
    assert any(b.callback_data == "notif_unmute" for b in flat2)
    nk3 = handlers._notif_keyboard(linked=False, muted=False)
    flat3 = [b for row in nk3.inline_keyboard for b in row]
    assert not any(b.callback_data in ("notif_mute", "notif_unmute") for b in flat3)


def test_app_smoke_import():
    from vacantrix.telegram import app  # noqa: F401
    assert callable(app.main)
