#!/usr/bin/env python3
"""
migrate_tg_mute.py — пауза Telegram-уведомлений из бота (notify_channels.telegram_muted).

Зачем: кнопка «⏸ Приостановить / ▶ Возобновить» в боте. Пауза ≠ отвязка:
клиентский RPC notify_unlink NULL-ит telegram_chat_id (включить обратно можно
только новым кодом из приложения), а пауза лишь поднимает флаг — chat_id цел,
возобновление мгновенное. Оба пути доставки живут в самом боте и уважают флаг:
  • /notify (порт Edge notify-send) — проверка в коде бота (app.py);
  • /tick — фильтр в RPC tg_pending_pushes (пересоздаётся здесь).

Идемпотентна. Канал применения — как у migrate_tg_push_log.py: SSH → docker
exec psql (Cloud.ru ВМ user1@82.202.137.129, ключ migration/secrets/cloudru_id_ed25519):

    python migrations/migrate_tg_mute.py
    #   env-override: VX_SSH_HOST, VX_SSH_USER, VX_SSH_KEY, VX_DB_CONTAINER, VX_DB_USER
    python migrations/migrate_tg_mute.py --dsn "postgresql://..."   # на ВМ/туннель
    python migrations/migrate_tg_mute.py --print-sql

ВНИМАНИЕ: применять ТОЛЬКО к self-host (api.vacantrix.ru), НЕ к облачному проекту.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DDL_COLUMN = """
ALTER TABLE public.notify_channels
    ADD COLUMN IF NOT EXISTS telegram_muted boolean NOT NULL DEFAULT false;
"""

# Пересоздание RPC из migrate_tg_push_log.py: добавлен фильтр telegram_muted.
DDL_RPC = """
CREATE OR REPLACE FUNCTION public.tg_pending_pushes(p_limit int DEFAULT 50)
RETURNS TABLE (
    notification_id uuid,
    chat_id         bigint,
    title           text,
    body            text,
    source          text,
    created_at      timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT n.id, c.telegram_chat_id, n.title, n.body, n.source, n.created_at
    FROM public.platform_notifications n
    JOIN public.notify_channels c
      ON c.user_id = n.user_id AND c.telegram_chat_id IS NOT NULL
    WHERE n.user_id IS NOT NULL
      AND NOT COALESCE(c.telegram_muted, false)
      AND (n.expires_at IS NULL OR n.expires_at > now())
      AND n.created_at > now() - interval '3 days'
      AND NOT EXISTS (
          SELECT 1 FROM public.tg_push_log l WHERE l.notification_id = n.id)
    ORDER BY n.created_at
    LIMIT greatest(1, least(coalesce(p_limit, 50), 200));
$$;

REVOKE ALL ON FUNCTION public.tg_pending_pushes(int) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tg_pending_pushes(int) TO service_role;
"""

VERIFY_SQL = """
SELECT
  (SELECT count(*) FROM information_schema.columns
    WHERE table_schema='public' AND table_name='notify_channels'
      AND column_name='telegram_muted')                                  AS col_present,
  (SELECT count(*) FROM public.notify_channels
    WHERE telegram_muted)                                                AS muted_now,
  (SELECT (prosrc LIKE '%telegram_muted%')::int FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname='public' AND p.proname='tg_pending_pushes')          AS rpc_filters_mute,
  (SELECT count(*) FROM public.tg_pending_pushes(50))                    AS pending_now;
"""

STEPS: list[tuple[str, str]] = [
    ("1/2  Колонка notify_channels.telegram_muted", DDL_COLUMN),
    ("2/2  RPC tg_pending_pushes с фильтром паузы", DDL_RPC),
]


def _psql_argv_dsn(dsn: str) -> list[str]:
    return ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q"]


def _ssh_argv(host: str, user: str, key: str, container: str, db_user: str) -> list[str]:
    remote = f"docker exec -i {container} psql -U {db_user} -v ON_ERROR_STOP=1 -q"
    return ["ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=20", f"{user}@{host}", remote]


def _run(argv: list[str], sql: str, label: str) -> bool:
    sql = "SET client_encoding TO 'UTF8';\n" + sql
    try:
        r = subprocess.run(argv, input=sql, capture_output=True, text=True,
                           encoding="utf-8", timeout=90)
    except FileNotFoundError:
        sys.exit(f"[X] не найден исполняемый файл: {argv[0]} (нужен psql/ssh в PATH)")
    except subprocess.TimeoutExpired:
        print(f"  [X]  {label}: таймаут")
        return False
    if r.returncode != 0:
        print(f"  [X]  {label}\n{(r.stderr or r.stdout).strip()[:600]}")
        return False
    out = (r.stdout or "").strip()
    print(f"  [OK]  {label}" + (f"\n{out}" if out else ""))
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Миграция telegram_muted на self-host Supabase.")
    ap.add_argument("--dsn", nargs="?", const="__env__",
                    help="прямой psql по DSN; без значения берёт VX_DB_DSN")
    ap.add_argument("--print-sql", action="store_true", help="только вывести SQL")
    args = ap.parse_args()

    full_sql = "\n".join(sql for _, sql in STEPS) + "\n" + VERIFY_SQL + "\n"
    if args.print_sql:
        print(full_sql)
        return

    print("\nМиграция telegram_muted (self-host Supabase, Cloud.ru)\n" + "=" * 54)

    repo_root = Path(__file__).resolve().parents[2]      # OpenIDEProjects
    if args.dsn is not None:
        dsn = None if args.dsn == "__env__" else args.dsn
        dsn = dsn or os.environ.get("VX_DB_DSN") or input("DB DSN (postgresql://...): ").strip()
        argv_factory = lambda: _psql_argv_dsn(dsn)  # noqa: E731
        print("канал: прямой psql по DSN\n")
    else:
        host = os.environ.get("VX_SSH_HOST", "82.202.137.129")
        user = os.environ.get("VX_SSH_USER", "user1")
        key = os.environ.get("VX_SSH_KEY",
                             str(repo_root / "migration" / "secrets" / "cloudru_id_ed25519"))
        container = os.environ.get("VX_DB_CONTAINER", "supabase-db")
        db_user = os.environ.get("VX_DB_USER", "postgres")
        argv_factory = lambda: _ssh_argv(host, user, key, container, db_user)  # noqa: E731
        print(f"канал: SSH {user}@{host} → docker exec {container} psql\n")

    ok = True
    for label, sql in STEPS:
        ok = _run(argv_factory(), sql, label) and ok
    if ok:
        _run(argv_factory(), VERIFY_SQL, "verify (колонка/паузы/RPC/ожидающие)")
        print("\n[DONE] миграция применена")
    else:
        sys.exit("\n[FAIL] есть ошибки — см. выше")


if __name__ == "__main__":
    main()
