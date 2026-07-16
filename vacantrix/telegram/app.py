# vacantrix/telegram/app.py
"""Telegram-шлюз экосистемы Vacantrix.

Прод (Render, free): webhook-режим — свой ASGI-сервер (starlette/uvicorn), апдейты
Telegram приходят HTTP-запросом и БУДЯТ уснувший free-сервис (канонический паттерн
PTB `customwebhookbot`: апдейты кладутся в application.update_queue).

Маршруты:
    POST /telegram  — Telegram-webhook (проверка X-Telegram-Bot-Api-Secret-Token);
    POST /notify    — доставка уведомлений Monitor (контракт Edge notify-send:
                      Authorization: Bearer <user JWT>, body {"tg": html, "max": text},
                      ответ {"telegram": "sent"|"not_linked"|..., "max": ...});
    POST /tick      — push НЕдоставленных личных platform_notifications в Telegram
                      (?key=TICK_KEY; будильник — cron на ВМ, заодно греет сервис);
    GET  /healthz   — health-check Render.

Локально (WEBHOOK_URL не задан) — обычный long-polling для разработки.
"""

import asyncio
import html as html_mod
import logging

import requests as _requests
from telegram import BotCommand, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

from vacantrix.telegram.config import (
    BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET, TICK_KEY, PORT, MAX_BOT_TOKEN,
)
from vacantrix.telegram.handlers import (
    start, menu_command, notifications_command,
    stats_command, broadcast_command, find_user,
    apps_command, stop_command, unstop_command, hide_command, show_command,
    button_handler, handle_text,
)
from vacantrix.telegram import supabase as sb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MAX_API = "https://platform-api2.max.ru"


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Ошибка бота. update=%s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Произошла ошибка. Попробуйте /menu.",
            )
        except Exception:
            pass


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start",         "Запустить бота"),
        BotCommand("menu",          "Главное меню"),
        BotCommand("notifications", "Уведомления: статус и подключение"),
    ])


def build_application(webhook: bool) -> Application:
    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    if webhook:
        builder = builder.updater(None)          # апдейты кладём в очередь сами
    app = builder.build()

    # Пользовательские команды
    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("menu",          menu_command))
    app.add_handler(CommandHandler("notifications", notifications_command))

    # Администраторские команды
    app.add_handler(CommandHandler("stats",     stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("find_user", find_user))

    # Удалённое управление (стоп-кран) — только ADMIN_ID
    app.add_handler(CommandHandler("apps",   apps_command))
    app.add_handler(CommandHandler("stop",   stop_command))
    app.add_handler(CommandHandler("unstop", unstop_command))
    app.add_handler(CommandHandler("hide",   hide_command))
    app.add_handler(CommandHandler("show",   show_command))

    # Кнопки и текст
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)
    return app


# ── Отправка в MAX (контракт как в Edge notify-send) ──────────────────────────

def _send_max(max_user_id, text: str) -> str:
    """Отправка в MAX Bot API. Возвращает статус-строку контракта notify-send."""
    if not MAX_BOT_TOKEN:
        return "no_token"
    try:
        r = _requests.post(
            f"{MAX_API}/messages?user_id={max_user_id}",
            headers={"Content-Type": "application/json", "Authorization": MAX_BOT_TOKEN},
            json={"text": text, "notify": True},
            timeout=15)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.ok and not j.get("code") and not j.get("error"):
            return "sent"
        return j.get("message") or j.get("error") or f"http_{r.status_code}"
    except _requests.RequestException as exc:
        return str(exc)


# ── ASGI-приложение (webhook-режим) ───────────────────────────────────────────

def _build_asgi(ptb: Application):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, PlainTextResponse, Response
    from starlette.routing import Route

    async def telegram_route(request):
        """Вход Telegram-webhook → очередь PTB."""
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return Response(status_code=403)
        try:
            data = await request.json()
        except Exception:
            return Response(status_code=400)
        await ptb.update_queue.put(Update.de_json(data=data, bot=ptb.bot))
        return Response()

    async def healthz_route(request):
        return PlainTextResponse("ok")

    async def notify_route(request):
        """Доставка уведомления Monitor — порт Edge notify-send (контракт 1-в-1).

        Вход: Authorization: Bearer <user JWT>, body {"tg"?: html, "max"?: text}.
        Выход: {"telegram": "sent"|"not_linked"|"no_token"|"skip"|<err>, "max": ...}.
        """
        if request.method != "POST":
            return JSONResponse({"error": "method not allowed"}, status_code=405)
        auth = request.headers.get("authorization", "")
        jwt = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        uid = await asyncio.to_thread(sb.get_user_id_from_jwt, jwt)
        if not uid:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        tg_text = str(body.get("tg") or "")
        max_text = str(body.get("max") or "")

        ch = await asyncio.to_thread(sb.get_channels_for_user, uid) or {}
        out = {"telegram": "skip", "max": "skip"}

        if tg_text:
            chat_id = ch.get("telegram_chat_id")
            if not chat_id:
                out["telegram"] = "not_linked"
            else:
                try:
                    await ptb.bot.send_message(
                        chat_id=chat_id, text=tg_text, parse_mode="HTML",
                        disable_web_page_preview=True)
                    out["telegram"] = "sent"
                except Exception as exc:
                    out["telegram"] = str(exc)

        if max_text:
            max_uid = ch.get("max_user_id")
            if not max_uid:
                out["max"] = "not_linked"
            else:
                out["max"] = await asyncio.to_thread(_send_max, max_uid, max_text)

        return JSONResponse(out)

    async def tick_route(request):
        """Push недоставленных личных platform_notifications в Telegram."""
        if request.query_params.get("key") != TICK_KEY:
            return Response(status_code=403)
        rows = await asyncio.to_thread(sb.fetch_pending_pushes, 50)
        pushed = failed = 0
        for r in rows:
            text = f"<b>{html_mod.escape(r.get('title') or '')}</b>"
            if r.get("body"):
                text += f"\n{html_mod.escape(r['body'])}"
            journal = False
            try:
                await ptb.bot.send_message(
                    chat_id=r["chat_id"], text=text, parse_mode="HTML",
                    disable_web_page_preview=True)
                journal = True
                pushed += 1
            except (Forbidden, BadRequest) as exc:
                # Заблокировал бота / чат мёртв / кривой текст — журналируем,
                # чтобы не молотить одно и то же каждый тик.
                logger.warning("tick: перманентная ошибка chat=%s: %s", r.get("chat_id"), exc)
                journal = True
                failed += 1
            except Exception as exc:
                # Сетевая/временная — НЕ журналируем, ретрай следующим тиком.
                logger.warning("tick: временная ошибка chat=%s: %s", r.get("chat_id"), exc)
                failed += 1
            if journal:
                await asyncio.to_thread(sb.mark_pushed, r["notification_id"])
        return JSONResponse({"pushed": pushed, "failed": failed})

    return Starlette(routes=[
        Route("/telegram", telegram_route, methods=["POST"]),
        Route("/notify",   notify_route,   methods=["POST"]),
        Route("/tick",     tick_route,     methods=["POST", "GET"]),
        Route("/healthz",  healthz_route,  methods=["GET", "HEAD"]),
    ])


async def _run_webhook(ptb: Application) -> None:
    import uvicorn

    asgi = _build_asgi(ptb)
    server = uvicorn.Server(uvicorn.Config(
        app=asgi, host="0.0.0.0", port=PORT, log_level="info"))

    async with ptb:                                  # initialize (+post_init)
        await ptb.bot.set_webhook(
            url=f"{WEBHOOK_URL}/telegram",
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("Webhook установлен: %s/telegram", WEBHOOK_URL)
        await ptb.start()
        logger.info("Бот запущен (webhook-шлюз: /telegram /notify /tick /healthz, порт %s)", PORT)
        await server.serve()
        await ptb.stop()


def main() -> None:
    if WEBHOOK_URL:
        if not WEBHOOK_SECRET or not TICK_KEY:
            raise RuntimeError("В webhook-режиме обязательны WEBHOOK_SECRET и TICK_KEY")
        asyncio.run(_run_webhook(build_application(webhook=True)))
    else:
        logger.info("WEBHOOK_URL/RENDER_EXTERNAL_URL не задан — dev-режим: polling")
        app = build_application(webhook=False)
        logger.info("Бот запущен (dev polling)")
        app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
