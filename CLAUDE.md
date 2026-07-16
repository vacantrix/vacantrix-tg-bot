# CLAUDE.md — vacantrix-tg-bot

Telegram-бот Vacantrix — **Базикс, шлюз уведомлений и «пульт» экосистемы**: доставляет
события приложений (находки мониторингов Monitor, «оплата прошла», «подписка истекает»,
«новая версия») в личку Telegram + витрина «пульта» (моя подписка и freemium-лимиты,
живой каталог инструментов, «что нового») + информационные разделы (скачивание → сайт,
инструкция, FAQ, поддержка) + админ-управление (статистика, сервисная рассылка,
стоп-кран инструментов). **Персона бота = Базикс** (маскот из 6 приложений): все
пользовательские тексты — от его лица, «на ты» (адаптация 2026-07-16).
**Оплаты в боте НЕТ** (удалена 2026-07-02); привязка HH-ID и арбитраж Robokassa
**вырезаны** при переосмыслении 2026-07-16.

**Хостинг:** Amvera Cloud (РФ), проект `VACANTRIX` (slug `vacantrix`), домен
`https://vacantrix-vacantrix.amvera.io`, режим **POLLING** (Telegram не достукивается
входящим webhook'ом до РФ-IP; исходящий прокси Amvera к api.telegram.org работает) +
HTTP-сервер для `/notify`/`/tick`/`/healthz` | **БД:** self-host Supabase
`https://api.vacantrix.ru` (Cloud.ru) | **Код:** GitVerse (основной) + публичное
GitHub-зеркало + remote `amvera` (деплой)

> ⚖️ Юр-рамка (ревью 2026-07-16): бот у самозанятого законен; линковка ≠ авторизация.
> ДО живого запуска TG-уведомлений: уведомления РКН + правки privacy/consent
> (трансграничка — только сам Telegram, хостинг теперь РФ/Amvera). `/broadcast` —
> ТОЛЬКО сервисные сообщения (рекламные требуют Согласия №2 с каналом Telegram).

---

## Архитектура (polling-шлюз)

Один процесс (`_run_polling_gateway`): PTB v21+ поллит getUpdates через прокси Amvera,
рядом uvicorn/starlette держит HTTP-маршруты (их зовёт ВМ Cloud.ru — ей вход открыт):

| Маршрут | Что делает |
|---|---|
| `POST /notify` | Доставка уведомлений Monitor — контракт Edge `notify-send` 1-в-1 (Bearer user-JWT → GoTrue-валидация → `notify_channels` → sendMessage; `{"tg","max"}` → `{"telegram","max"}`; пауза → `"muted"`). На ВМ nginx проксирует `/functions/v1/notify-send` → сюда |
| `POST /tick?key=TICK_KEY` | Push недоставленных ЛИЧНЫХ `platform_notifications` привязанным юзерам; дедуп — `tg_push_log` (anti-join + фильтр паузы в RPC `tg_pending_pushes`). Будильник — cron на ВМ раз в 5 мин |
| `GET /healthz` | health-check |
| `POST /telegram` | Telegram-webhook — только за флагом `VX_USE_WEBHOOK=1` (не-РФ хостинг) |

Локально — тот же polling. ⚠️ **С боевым токеном локально НЕ запускать** — Amvera уже
поллит, будет Conflict 409. Анти-зомби: при env `RENDER` (его ставит Render) бот
отказывается стартовать (старый Render-сервис автодеплоился с GitHub и дрался за
getUpdates) — override `VX_ALLOW_RENDER=1`.

## Структура

```
vacantrix-tg-bot/
├── main.py                        ← точка входа
├── amvera.yml                     ← конфиг Amvera (containerPort 10000)
├── render.yaml                    ← УСТАРЕЛ (история Render-варианта)
├── requirements.txt               ← PTB, starlette, uvicorn, requests, dotenv
├── .env / .env.render             ← локально / значения для хостинга (НЕ коммитить)
├── resources/bazix_avatar.png     ← аватарка бота (BotFather → /setuserpic, руками)
├── tests/test_bot_texts.py        ← оффлайн: тексты/лимиты BotFather/клавиатуры
├── migrations/
│   ├── migrate_lock_bot_tables.py ← RLS-лок таблиц бота (историческая, применена)
│   ├── migrate_tg_push_log.py     ← журнал доставки + RPC tg_pending_pushes (применена)
│   └── migrate_tg_mute.py         ← пауза уведомлений: notify_channels.telegram_muted
│                                    + RPC с фильтром (применена 2026-07-16)
└── vacantrix/telegram/
    ├── app.py                     ← polling-шлюз + ASGI-маршруты + post_init
    │                                (профиль BotFather из кода: getMy*→setMy* при отличии)
    ├── handlers.py                ← команды/кнопки (логика; тексты — в texts.py)
    ├── texts.py                   ← ВСЕ фразы Базикса + BOT_NAME/DESCRIPTION/COMMANDS
    ├── config.py                  ← env
    └── supabase.py                ← users, линковка, подписки/лимиты/каталог/новости,
                                     push-очередь, mute, стоп-кран
```

## Функции (меню 2×4)

| Раздел | Что делает |
|--------|-----------|
| 🔔 Уведомления | Статус привязки + **пауза** «⏸ Приостановить / ▶ Возобновить» (`notify_channels.telegram_muted`; chat_id НЕ трогается — пауза ≠ отвязка, полная отвязка в приложении) |
| 💎 Моя подписка | Активные `subscriptions` (embed tools/plans) + остатки freemium: `hh_free_usage` и `avito_free_usage` (ключ ОБОИХ = platform-uuid, v2-миграции O1), «N из 10 в этом месяце»; имя из `vx_profiles.display_name` |
| 🧰 Инструменты | ЖИВОЙ каталог из `tools` (status active/coming_soon, sort_order) — 🟢/🟡, tagline |
| 🆕 Что нового | Последние 5 `web_news` (published, тег-эмодзи 🚀/📦/📣, body ~200 симв.) |
| 📥/❓/🛠/ℹ️ | Скачать (сайт), FAQ, поддержка, «Про меня» |
| `/start <код>` | Линковка из приложений: гасит `notify_link_codes`, пишет `chat_id` в `notify_channels` |
| Команды | `/start /menu /notifications /subscription` (ставятся из `texts.BOT_COMMANDS`) |
| Админ | `/stats`, `/broadcast` (ТОЛЬКО сервисные), `/find_user <tg_id>` (+статус паузы), стоп-кран `/apps /stop /unstop /hide /show` |
| Легаси-кнопки | Старые `buy`/`sub_*` → «подписки в Platform»; `profile` → «Моя подписка»; `link` → «привязка больше не нужна» |

**Тексты**: всё пользовательское — `texts.py`, голос Базикса (эталон тона —
`vacantrix-publisher/publisher_app/core/bazix_hints.py`), parse_mode **HTML**
(динамика из БД экранируется). Профиль BotFather (имя/био/описание/команды)
ставится в `post_init` из кода — правки профиля = правки `texts.py` + деплой.
Аватарка — только руками: BotFather → `/setuserpic` → `resources/bazix_avatar.png`.

## Переменные окружения

```
BOT_TOKEN=              # @Vacantrix_bot (ЕДИНСТВЕННЫЙ инстанс — на Amvera)
SUPABASE_URL=https://api.vacantrix.ru
SUPABASE_KEY=           # legacy anon JWT (self-host Kong НЕ принимает sb_publishable_)
SUPABASE_SERVICE_KEY=   # legacy service_role JWT — обязателен (RLS)
ADMIN_ID=               # Telegram ID администратора
TICK_KEY=               # ключ /tick
WEBHOOK_SECRET=         # нужен только в webhook-режиме (VX_USE_WEBHOOK=1)
INSTRUCTION_URL / SUPPORT_URL / FAQ_URL / SITE_URL   # опционально (есть дефолты)
MAX_BOT_TOKEN=          # опционально: доставка в MAX (контракт notify-send)
# PORT задаёт хостинг (Amvera containerPort 10000)
```

## Supabase-таблицы/RPC (self-host, всё под service_role)

| Объект | Доступ бота |
|---------|-------------|
| `users` | чтение; запись только `telegram_id` (регистрация по /start) |
| `notify_link_codes` / `notify_channels` | линковка + пауза (`telegram_muted`) |
| `subscriptions` (+embed `tools`/`plans`), `vx_profiles` | «Моя подписка» (чтение) |
| `hh_free_usage` / `avito_free_usage` | остатки freemium «N из 10» (чтение) |
| `tools` | живой каталог (чтение) + стоп-кран (PATCH `enabled`/`status`/`disabled_message`) |
| `web_news` | «Что нового» (чтение published) |
| `platform_notifications` | чтение через RPC `tg_pending_pushes` (фильтрует паузу) |
| `tg_push_log` | журнал доставки /tick |
| GoTrue `/auth/v1/user` | валидация user-JWT для `/notify` |

## Деплой / обновление

```bash
python -m pytest tests/ -q            # оффлайн-тесты перед пушем
git push gitverse main                # основной хостинг кода
git push origin main                  # публичное GitHub-зеркало
git push amvera main:master           # ДЕПЛОЙ: Amvera пересобирает ~2-3 мин
```
Статусы сборки — кабинет Amvera (BUILD_STARTED→STARTING→RUNNING); push в `amvera`
требует логин/пароль кабинета. Переменные/домен — только вкладки кабинета
(«Переменные»/«Домены»), у API их нет. На ВМ (Cloud.ru): nginx-прокси
`/functions/v1/notify-send` → Amvera `/notify` и cron `/tick` (стоит).

## История

- 2026-07-02: оплата удалена (Payments/YooKassa, напоминания, реф-бонусы).
- 2026-07-16: переосмысление → шлюз уведомлений; вырезаны привязка HH-ID,
  профиль-подписка, арбитраж Robokassa; бэкенд облако → self-host; GitHub-раздача →
  сайт vacantrix.ru; Render → **Amvera (РФ, polling)**.
- 2026-07-16 (вечер): **адаптация под Базикса** — texts.py (все фразы + профиль
  BotFather из кода), «пульт» (подписка/лимиты/каталог/новости), пауза уведомлений
  (`migrate_tg_mute.py` применена), анти-зомби гард Render, аватарка, тесты.
