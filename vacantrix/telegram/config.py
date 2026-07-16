# vacantrix/telegram/config.py
"""Конфигурация Telegram-бота Vacantrix — единого Telegram-шлюза экосистемы.

Режимы:
  • Render (прод): задан RENDER_EXTERNAL_URL (Render выставляет сам) или VX_WEBHOOK_URL →
    webhook-режим (ASGI-сервер: /telegram, /notify, /tick, /healthz).
  • Локальная разработка: URL не задан → обычный long-polling.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


def _required_int_env(name: str) -> int:
    value = _required_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Переменная окружения {name} должна быть числом") from exc

# --- Telegram Bot ---
BOT_TOKEN = _required_env("BOT_TOKEN")

# --- Supabase (self-host, api.vacantrix.ru) ---
SUPABASE_URL = _required_env("SUPABASE_URL")
SUPABASE_KEY = _required_env("SUPABASE_KEY")          # legacy anon JWT (self-host Kong
                                                      # НЕ принимает sb_publishable_…)

# Бот — доверенный бэкенд. Серверные операции (users, notify_*, tools, tg_push_log)
# идут под service_role (обходит RLS).
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
if not SUPABASE_SERVICE_KEY:
    import sys as _sys
    print("[config] ВНИМАНИЕ: SUPABASE_SERVICE_KEY не задан — откат на anon-ключ. "
          "Серверные операции бота под RLS сломаются!",
          file=_sys.stderr)
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

# --- Администратор ---
ADMIN_ID = _required_int_env("ADMIN_ID")

# --- Webhook-режим (Render) ---
# RENDER_EXTERNAL_URL Render задаёт автоматически (https://<svc>.onrender.com);
# VX_WEBHOOK_URL — ручной override (например, свой домен). Пусто → dev-polling.
WEBHOOK_URL = (os.getenv("VX_WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
# Секрет Telegram-webhook (заголовок X-Telegram-Bot-Api-Secret-Token) и ключ /tick.
# Обязательны в webhook-режиме — проверяется на старте в app.py.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
TICK_KEY = os.getenv("TICK_KEY", "")
PORT = int(os.getenv("PORT", "10000"))

# --- Доставка в MAX (опционально; тот же контракт, что у Edge notify-send) ---
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")

# --- Ссылки ---
INSTRUCTION_URL = os.getenv("INSTRUCTION_URL", "https://t.me/VacantrixB_O_T/14")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/VacantrixB_O_T/2")
FAQ_URL = os.getenv("FAQ_URL", "https://t.me/VacantrixB_O_T/6")
# Сайт — единый хаб загрузок (GitHub-раздача удалена 2026-07-16).
SITE_URL = os.getenv("SITE_URL", "https://vacantrix.ru")

# --- HTTP заголовки для Supabase (service_role) ---
HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
