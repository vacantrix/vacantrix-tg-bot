# bot_config.py
"""Конфигурация Telegram-бота с поддержкой платежей через ЮKassa."""

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

# --- Платёжный токен (ЮKassa) ---
# Получите в @BotFather: Payments → Connect YooKassa → после привязки скопируйте токен.
PROVIDER_TOKEN = _required_env("PROVIDER_TOKEN")

# --- Supabase ---
SUPABASE_URL = _required_env("SUPABASE_URL")
SUPABASE_KEY = _required_env("SUPABASE_KEY")

# --- Администратор ---
ADMIN_ID = _required_int_env("ADMIN_ID")

# --- Группа для хранения версий (чат/канал) ---
VERSIONS_GROUP_ID = _required_int_env("VERSIONS_GROUP_ID")

# --- Инструкция пользователю ---
INSTRUCTION_URL = os.getenv("INSTRUCTION_URL", "https://t.me/VacantrixB_O_T/14")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/VacantrixB_O_T/2")
FAQ_URL = os.getenv("FAQ_URL", "https://t.me/VacantrixB_O_T/6")

# --- HTTP заголовки для Supabase ---
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
