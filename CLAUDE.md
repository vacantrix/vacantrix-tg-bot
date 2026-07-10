# CLAUDE.md — vacantrix-tg-bot

Telegram-бот Vacantrix — **информационный продукт**: скачивание приложений (прямые ссылки),
инструкции/FAQ, справка о подписке для старых подписчиков, админ-рассылки.
**Оплаты в боте НЕТ** (удалена 2026-07-02) — подписки оформляются только в приложении
**Vacantrix Platform**.

**Деплой:** Render (render.yaml) | **БД:** Supabase `fgcffgfyehequucnxegb`

---

## Быстрый запуск

```bash
cd vacantrix-tg-bot
python main.py
```

## Структура

```
vacantrix-tg-bot/
├── main.py                        ← точка входа (запуск polling)
├── render.yaml                    ← конфиг деплоя на Render
├── requirements.txt
├── .env
├── migrations/
│   └── migrate_lock_bot_tables.py ← RLS-лок таблиц бота (применена в проде)
└── vacantrix/telegram/
    ├── app.py                     ← инициализация Application (PTB)
    ├── handlers.py                ← обработчики команд и кнопок
    ├── config.py                  ← все env-переменные (BOT_TOKEN, ссылки, ...)
    └── supabase.py                ← работа с Supabase (пользователи, чтение подписки)
```

## Функции бота

| Раздел | Что делает |
|--------|-----------|
| 📥 Скачать приложение | inline-кнопки с ПРЯМЫМИ ссылками: HH-бот (`vacantrix-hh-dist`), установщик Vacantrix Platform (`vacantrix-platform-dist`), сайт |
| 📖 Инструкция / ❓ FAQ | справочные тексты + ссылки на посты сообщества |
| 📊 Мой профиль | ЧТЕНИЕ статуса подписки из `users` — справка для старых подписчиков + отсылка к Vacantrix Platform |
| 🔗 Привязать ID соискателя | пишет `users.applicant_id` (по нему HH-приложение проверяет подписку) |
| Админ | `/stats` (счётчик пользователей), `/broadcast` (рассылка), `/find_user` |

Старые платёжные кнопки в исторических сообщениях (`buy`, `sub_*`, `confirm_pay_*`,
`referral`, `payment_history`) обрабатываются заглушкой «Подписки теперь в Vacantrix Platform».

> ⚠️ Бот НИКОГДА не пишет `users.subscription_expire` — платёжный контур
> (Telegram Payments/YooKassa), напоминания о продлении (`subscription_reminders`),
> реферальные бонус-дни и пересылка EXE из `latest_version` удалены 2026-07-02.
> Таблицы `latest_version`/`subscription_reminders` в БД оставлены (не используются ботом).

## Переменные окружения (.env)

```
BOT_TOKEN=<Telegram Bot Token от @BotFather>
SUPABASE_URL=https://fgcffgfyehequucnxegb.supabase.co
SUPABASE_KEY=<anon key>
SUPABASE_SERVICE_KEY=<service_role — обязателен: users под RLS>
ADMIN_ID=<Telegram user ID администратора>
INSTRUCTION_URL=https://t.me/VacantrixB_O_T/14
SUPPORT_URL=https://t.me/VacantrixB_O_T/2
FAQ_URL=https://t.me/VacantrixB_O_T/6
# Опционально (есть дефолты в config.py):
# HH_DOWNLOAD_URL / PLATFORM_DOWNLOAD_URL / SITE_URL
```

Удалены из контура (убрать и из env Render): `PROVIDER_TOKEN`, `VERSIONS_GROUP_ID`.

## Стек

| Слой | Технология |
|------|-----------|
| Telegram | `python-telegram-bot` (PTB) |
| БД | Supabase (таблица `users`) |
| HTTP к Supabase | `requests` |
| Деплой | Render (free tier, polling) |

## Supabase-таблицы (используемые)

| Таблица | Поля | Доступ бота |
|---------|------|-------------|
| `users` | `telegram_id`, `applicant_id`, `subscription_expire` | чтение; запись только `telegram_id`/`applicant_id` |
