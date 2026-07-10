#!/usr/bin/env python3
"""
migrate_lock_bot_tables.py — закрывает RLS-дыры в таблицах tg-bot.

ПРОБЛЕМА (аудит 2026-06-05): users, latest_version, subscription_reminders имели
RLS=OFF и полные гранты anon/authenticated. Любой с anon-ключом (он публичен, зашит
в клиентах) мог:
  - читать/менять/удалять данные всех telegram-пользователей (users);
  - переписать ссылку на пересылаемый релиз (latest_version) → раздача чужого файла;
  - менять счётчики напоминаний (subscription_reminders).

ПОСЛЕ: RLS включён, клиентские роли (anon/authenticated) доступа НЕ имеют. Бот работает
под service_role (обходит RLS) — см. config.py (SUPABASE_SERVICE_KEY).

⚠️ ПОРЯДОК: применять ТОЛЬКО ПОСЛЕ того, как tg-bot задеплоен с SUPABASE_SERVICE_KEY
   на Render (иначе живой бот потеряет доступ к этим таблицам). Проверено: кроме tg-bot
   к этим таблицам никто не обращается (hh/avito/platform/web — нет).

Запуск:
    $env:PYTHONIOENCODING="utf-8"
    $env:SUPABASE_PAT="sbp_..."   # Management API PAT
    python migrations/migrate_lock_bot_tables.py
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV = Path(__file__).parent.parent / ".env"
vals = {}
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()

SUPABASE_URL = vals.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
PAT = os.environ.get("SUPABASE_PAT") or vals.get("SUPABASE_PAT", "")
if not SUPABASE_URL:
    SUPABASE_URL = input("SUPABASE_URL: ").strip()
if not PAT:
    PAT = input("SUPABASE_PAT (Management API): ").strip()

PROJECT_ID = SUPABASE_URL.split("//")[1].split(".")[0]

SQL = """
-- users
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.users FROM anon, authenticated;

-- latest_version
ALTER TABLE public.latest_version ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.latest_version FROM anon, authenticated;

-- subscription_reminders
ALTER TABLE public.subscription_reminders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.subscription_reminders FROM anon, authenticated;
"""


def run_sql(sql: str) -> bool:
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_ID}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={
            "Authorization": f"Bearer {PAT}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            return True
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code} — {e.read().decode()[:400]}")
        return False


if __name__ == "__main__":
    print("=== Lock bot tables (users / latest_version / subscription_reminders) ===")
    print("ВНИМАНИЕ: бот должен быть уже задеплоен с SUPABASE_SERVICE_KEY!\n")
    print("Готово." if run_sql(SQL) else "Ошибка — см. выше.")
