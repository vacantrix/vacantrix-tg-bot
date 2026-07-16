# CLAUDE.md — vacantrix-tg-bot

Telegram-бот Vacantrix — **шлюз уведомлений экосистемы**: доставляет события приложений
(результаты мониторингов Monitor, «оплата прошла», «подписка истекает», «новая версия»)
в личку Telegram + информационные разделы (скачивание → сайт, инструкция, FAQ, поддержка)
+ админ-управление (статистика, сервисная рассылка, стоп-кран инструментов).
**Оплаты в боте НЕТ** (удалена 2026-07-02); привязка HH-ID и арбитраж Robokassa
**вырезаны** при переосмыслении 2026-07-16.

**Хостинг:** Render.com, аккаунт `VACANTRIX.official@` (free web-сервис, webhook-режим —
Telegram сам будит уснувший сервис) | **БД:** self-host Supabase `https://api.vacantrix.ru`
(Cloud.ru) | **Код:** GitVerse `git@gitverse.ru:vacantrix/vacantrix-tg-bot.git`

> ⚖️ Юр-рамка (ревью 2026-07-16): бот у самозанятого законен; линковка ≠ авторизация.
> ДО живого запуска TG-уведомлений: уведомления РКН (обработка ПДн + трансграничная
> передача: Render US + Telegram) и правки privacy/consent на сайте. `/broadcast` —
> ТОЛЬКО сервисные сообщения (рекламные требуют Согласия №2 с каналом Telegram).

---

## Архитектура (webhook-шлюз)

Один процесс: PTB v21+ в webhook-режиме через starlette/uvicorn (паттерн PTB
`customwebhookbot` — апдейты кладутся в `application.update_queue`). Маршруты:

| Маршрут | Что делает |
|---|---|
| `POST /telegram` | Telegram-webhook (проверка `X-Telegram-Bot-Api-Secret-Token` = `WEBHOOK_SECRET`). При старте бот сам делает `setWebhook` на `{RENDER_EXTERNAL_URL}/telegram` |
| `POST /notify` | Доставка уведомлений Monitor — контракт Edge `notify-send` 1-в-1 (Bearer user-JWT → GoTrue-валидация → `notify_channels` → sendMessage; `{"tg","max"}` → `{"telegram","max"}`). На ВМ nginx проксирует `/functions/v1/notify-send` → сюда |
| `POST /tick?key=TICK_KEY` | Push недоставленных ЛИЧНЫХ `platform_notifications` привязанным юзерам; дедуп — `tg_push_log` (anti-join в RPC `tg_pending_pushes`). Будильник — cron на ВМ раз в 5 мин (заодно греет free-сервис) |
| `GET /healthz` | health-check Render |

Локально (`WEBHOOK_URL`/`RENDER_EXTERNAL_URL` не задан) — dev-режим: обычный polling.

## Структура

```
vacantrix-tg-bot/
├── main.py                        ← точка входа
├── render.yaml                    ← ДОКУМЕНТАЦИЯ настроек Render (сервис создаётся вручную:
│                                    New → Web Service → Public Git Repository → URL GitVerse;
│                                    Blueprint с публичным URL не работает)
├── requirements.txt               ← PTB, starlette, uvicorn, requests, dotenv
├── .env                           ← локальная разработка (НЕ коммитить)
├── .env.render                    ← готовые значения для дашборда Render (НЕ коммитить)
├── migrations/
│   ├── migrate_lock_bot_tables.py ← RLS-лок таблиц бота (историческая, применена)
│   └── migrate_tg_push_log.py     ← журнал доставки + RPC tg_pending_pushes
│                                    (self-host, SSH→psql; применена 2026-07-16)
└── vacantrix/telegram/
    ├── app.py                     ← webhook-ASGI (маршруты) + dev-polling + сборка Application
    ├── handlers.py                ← команды/кнопки (меню: Уведомления/Скачать/Инструкция/FAQ/…)
    ├── config.py                  ← env (webhook-режим по RENDER_EXTERNAL_URL)
    └── supabase.py                ← users, линковка, push-очередь, стоп-кран
```

## Функции

| Раздел | Что делает |
|--------|-----------|
| 🔔 Уведомления | Статус привязки (reverse-lookup `notify_channels.telegram_chat_id`); как подключить |
| `/start <код>` | Линковка из приложений (Monitor → «Подключить Telegram»): гасит `notify_link_codes`, пишет `chat_id` в `notify_channels` — всё на self-host |
| 📥/📖/❓/🛠 | Сайт vacantrix.ru (единый хаб загрузок), инструкция, FAQ, поддержка |
| Админ | `/stats`, `/broadcast` (ТОЛЬКО сервисные), `/find_user <tg_id>`, стоп-кран `/apps /stop /unstop /hide /show` |
| Легаси-кнопки | Старые `buy`/`sub_*` → «подписки в Platform»; `profile` → Уведомления; `link` → «привязка больше не нужна» |

## Переменные окружения

```
BOT_TOKEN=              # @Vacantrix_bot (ЕДИНСТВЕННЫЙ бот на этом токене после cutover)
SUPABASE_URL=https://api.vacantrix.ru
SUPABASE_KEY=           # legacy anon JWT (eyJ…) — self-host Kong НЕ принимает sb_publishable_
SUPABASE_SERVICE_KEY=   # legacy service_role JWT — обязателен (RLS)
ADMIN_ID=               # Telegram ID администратора
WEBHOOK_SECRET=         # секрет Telegram-webhook (обязателен в webhook-режиме)
TICK_KEY=               # ключ /tick (обязателен в webhook-режиме)
INSTRUCTION_URL / SUPPORT_URL / FAQ_URL / SITE_URL   # опционально (есть дефолты)
MAX_BOT_TOKEN=          # опционально: доставка в MAX (контракт notify-send)
# RENDER_EXTERNAL_URL и PORT Render задаёт сам; локально их НЕ задавать (dev = polling)
```

## Supabase-таблицы/RPC (self-host)

| Объект | Доступ бота |
|---------|-------------|
| `users` | чтение; запись только `telegram_id` (регистрация по /start) |
| `notify_link_codes` / `notify_channels` | линковка (service_role) |
| `platform_notifications` | чтение через RPC `tg_pending_pushes` (только service_role) |
| `tg_push_log` | журнал доставки /tick (только service_role) |
| `tools` | стоп-кран: PATCH `enabled`/`status`/`disabled_message` |
| GoTrue `/auth/v1/user` | валидация user-JWT для `/notify` |

## Деплой / обновление

1. `git push gitverse` (репо ПУБЛИЧНЫЙ на GitVerse — секретов в коде/истории нет, проверено).
2. Дашборд Render → сервис `vacantrix-tg-bot` → **Manual Deploy** (авто-деплоя с публичным
   Git-URL у Render нет).
3. Смена env в дашборде → Render сам перезапустит сервис.
4. На ВМ (Cloud.ru): nginx-прокси `/functions/v1/notify-send` → Render `/notify` и cron
   `/tick` — ставит `migration/setup_tg_gateway_vm.py` (из корня монорепо).

## Быстрый запуск (локально, dev polling)

```bash
cd vacantrix-tg-bot
python main.py
```

## История

- 2026-07-02: оплата удалена (Payments/YooKassa, напоминания, реф-бонусы).
- 2026-07-16: переосмысление → webhook-шлюз уведомлений; вырезаны привязка HH-ID,
  профиль-подписка, арбитраж Robokassa; переезд на новый аккаунт Render;
  бэкенд облако → self-host; GitHub-раздача → сайт vacantrix.ru.
