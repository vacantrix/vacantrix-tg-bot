# bot_config.py
"""Конфигурация Telegram-бота Vacantrix (информационный бот — без оплаты)."""

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

# --- Supabase ---
SUPABASE_URL = _required_env("SUPABASE_URL")
SUPABASE_KEY = _required_env("SUPABASE_KEY")          # publishable (anon)

# Бот — доверенный бэкенд (Render). Серверные операции с users идут под
# service_role (обходит RLS) — таблица закрыта RLS для клиентских ролей.
# На Render должна быть задана переменная SUPABASE_SERVICE_KEY.
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
if not SUPABASE_SERVICE_KEY:
    import sys as _sys
    print("[config] ВНИМАНИЕ: SUPABASE_SERVICE_KEY не задан — откат на anon-ключ. "
          "После включения RLS на users операции бота сломаются!",
          file=_sys.stderr)
    SUPABASE_SERVICE_KEY = SUPABASE_KEY

# --- Администратор ---
ADMIN_ID = _required_int_env("ADMIN_ID")

# --- Удалённое управление (стоп-кран + арбитраж) ---
# FUNCTIONS_URL — для вызова Edge tasks-robokassa-resolve; ADMIN_SECRET — его гейт
# (тот же секрет, что в env функции). Если не задан — /resolve honestly ответит,
# что арбитраж недоступен.
FUNCTIONS_URL = os.getenv("VX_FUNCTIONS_URL", f"{SUPABASE_URL}/functions/v1")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# --- Инструкция пользователю ---
INSTRUCTION_URL = os.getenv("INSTRUCTION_URL", "https://t.me/VacantrixB_O_T/14")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/VacantrixB_O_T/2")
FAQ_URL = os.getenv("FAQ_URL", "https://t.me/VacantrixB_O_T/6")

# --- Прямые ссылки на скачивание (GitHub *-dist Releases) и сайт (GitHub Pages) ---
# Откат с Яндекса на GitHub (2026-07-04): на self-host вернёмся перед запуском.
HH_DOWNLOAD_URL = os.getenv(
    "HH_DOWNLOAD_URL",
    "https://github.com/vacantrix/vacantrix-hh-dist/releases/latest/download/Vacantrix.exe",
)
PLATFORM_DOWNLOAD_URL = os.getenv(
    "PLATFORM_DOWNLOAD_URL",
    "https://github.com/vacantrix/vacantrix-platform-dist/releases/latest/download/VacantrixSetup.exe",
)
SITE_URL = os.getenv("SITE_URL", "https://vacantrix.github.io/vacantrix-web/")

# --- HTTP заголовки для Supabase ---
HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
