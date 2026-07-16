# vacantrix/telegram/supabase.py
"""Работа с Supabase (self-host): пользователи бота, линковка уведомлений,
стоп-кран инструментов, очередь push-уведомлений экосистемы."""

import logging
from datetime import datetime, timezone

import requests

from vacantrix.telegram.config import SUPABASE_URL, SUPABASE_KEY, HEADERS

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12


def _request(method: str, table: str, **kwargs):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    try:
        response = requests.request(method, url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
        if not response.ok:
            logger.warning("Supabase %s %s failed: %s %s", method, table, response.status_code, response.text[:500])
        return response
    except requests.RequestException as exc:
        logger.error("Supabase %s %s request error: %s", method, table, exc)
        return None


def supabase_get(table, params=None):
    resp = _request("GET", table, params=params)
    if not resp or not resp.ok:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        logger.error("Supabase %s returned invalid JSON: %s", table, exc)
        return None


def supabase_post(table, data):
    return _request("POST", table, json=data)


def supabase_patch(table, filters_dict, data):
    params = {k: f"eq.{v}" for k, v in filters_dict.items()}
    return _request("PATCH", table, params=params, json=data)


# ---------- Пользователи бота ----------

def get_user_by_telegram(telegram_id):
    rows = supabase_get("users", {"telegram_id": f"eq.{telegram_id}"})
    return rows[0] if rows else None


def create_user(telegram_id):
    return supabase_post("users", {"telegram_id": telegram_id})


# ---------- Линковка уведомлений (notify_link_codes / notify_channels) ----------

def redeem_link_code(code: str, telegram_chat_id: int, channel: str = "telegram"):
    """Погасить одноразовый код линковки и записать chat_id в notify_channels.

    Вызывается из /start <code> (deep-link из приложения). Возвращает user_id при
    успехе, None — если код неверный/использован/ошибка. Работает service_role'ом
    (обходит RLS notify_link_codes/notify_channels)."""
    if not code or len(code) > 32:
        return None
    rows = supabase_get("notify_link_codes", {
        "code": f"eq.{code}", "used": "eq.false", "channel": f"eq.{channel}",
        "select": "user_id,channel"})
    if not rows:
        return None                              # неверный/использованный код
    uid = rows[0]["user_id"]
    field = "telegram_chat_id" if channel == "telegram" else "max_user_id"
    at_field = "telegram_linked_at" if channel == "telegram" else "max_linked_at"
    now = datetime.now(timezone.utc).isoformat()
    data = {field: telegram_chat_id, at_field: now}
    existing = supabase_get("notify_channels", {"user_id": f"eq.{uid}", "select": "user_id"})
    if existing:
        resp = supabase_patch("notify_channels", {"user_id": uid}, data)
    else:
        data["user_id"] = uid
        resp = supabase_post("notify_channels", data)
    if not (resp and resp.ok):
        return None
    supabase_patch("notify_link_codes", {"code": code}, {"used": True})
    return uid


def get_link_by_chat(telegram_chat_id: int):
    """Обратный поиск линковки: чей это Telegram-чат. None — не привязан.

    select=* — терпимо к отсутствию колонки telegram_muted до миграции."""
    rows = supabase_get("notify_channels", {
        "telegram_chat_id": f"eq.{telegram_chat_id}", "select": "*"})
    return rows[0] if rows else None


def get_channels_for_user(user_id: str):
    """Каналы доставки пользователя (для /notify). None — не привязан."""
    rows = supabase_get("notify_channels", {
        "user_id": f"eq.{user_id}", "select": "*"})
    return rows[0] if rows else None


def set_telegram_muted(user_id: str, muted: bool) -> bool:
    """Пауза/возобновление Telegram-уведомлений (chat_id НЕ трогаем — это пауза,
    не отвязка; клиентский notify_unlink NULL-ит chat_id, мы — нет)."""
    resp = supabase_patch("notify_channels", {"user_id": user_id}, {
        "telegram_muted": muted,
        "updated_at": datetime.now(timezone.utc).isoformat()})
    return bool(resp and resp.ok)


# ---------- Витрина «пульта»: подписка, лимиты, каталог, новости ----------

def _month_key() -> str:
    """Ключ месяца freemium-счётчиков — 'YYYY-MM' в UTC (как в RPC hh/avito)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_active_subscriptions(user_id: str) -> list | None:
    """Активные подписки пользователя (+имена инструмента/плана embed'ом).
    [] — подписок нет, None — сервер недоступен."""
    now = datetime.now(timezone.utc).isoformat()
    return supabase_get("subscriptions", {
        "user_id": f"eq.{user_id}", "status": "eq.active",
        "expires_at": f"gt.{now}",
        "select": "expires_at,tools(slug,name),plans(name)",
        "order": "expires_at.desc"})


def get_profile(user_id: str):
    """Кросс-проектный профиль vx_profiles (display_name, avito_user_id…)."""
    rows = supabase_get("vx_profiles", {
        "web_user_id": f"eq.{user_id}",
        "select": "display_name,avito_user_id,subscription_expire"})
    return rows[0] if rows else None


def get_hh_free_used(user_id: str) -> int | None:
    """Расход freemium HH за текущий месяц (ключ v2 = platform-uuid как text).
    0 — расхода не было, None — сервер недоступен."""
    rows = supabase_get("hh_free_usage", {
        "applicant_id": f"eq.{user_id}", "month": f"eq.{_month_key()}",
        "select": "used"})
    if rows is None:
        return None
    return int(rows[0].get("used") or 0) if rows else 0


def get_avito_free_used(user_id: str) -> int | None:
    """Расход freemium Avito за текущий месяц.

    Ключ v2 (migrate_avito_free_limit_v2, фикс O1) — platform-uuid как text
    в колонке avito_user_id (как у HH; старые строки по реальному avito-id инертны)."""
    rows = supabase_get("avito_free_usage", {
        "avito_user_id": f"eq.{user_id}", "month": f"eq.{_month_key()}",
        "select": "used"})
    if rows is None:
        return None
    return int(rows[0].get("used") or 0) if rows else 0


def get_tools_catalog() -> list | None:
    """Живой каталог инструментов для витрины (active/coming_soon).
    select=* — терпимо к составу колонок (tagline может отсутствовать)."""
    return supabase_get("tools", {
        "status": "in.(active,coming_soon)",
        "order": "sort_order.asc", "select": "*"})


def get_news(limit: int = 5) -> list | None:
    """Последние опубликованные новости сайта (web_news)."""
    return supabase_get("web_news", {
        "published": "eq.true", "order": "published_at.desc",
        "limit": str(limit), "select": "*"})


def get_user_id_from_jwt(jwt: str):
    """Валидация пользовательского JWT через GoTrue (как auth.getUser() в Edge).

    Возвращает user_id или None. Ходит под anon-apikey + Bearer=JWT юзера —
    подпись/срок проверяет сам GoTrue."""
    if not jwt:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {jwt}"},
            timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        return (r.json() or {}).get("id")
    except (requests.RequestException, ValueError) as exc:
        logger.error("auth/v1/user error: %s", exc)
        return None


# ---------- Push-очередь экосистемы (platform_notifications → Telegram) ----------

def fetch_pending_pushes(limit: int = 50) -> list:
    """Недоставленные ЛИЧНЫЕ уведомления платформы для привязанных юзеров.

    RPC tg_pending_pushes (self-host, SECURITY DEFINER, только service_role):
    anti-join c tg_push_log + join notify_channels."""
    resp = _request("POST", "rpc/tg_pending_pushes", json={"p_limit": limit})
    if not resp or not resp.ok:
        return []
    try:
        return resp.json() or []
    except ValueError:
        return []


def mark_pushed(notification_id: str) -> bool:
    """Журнал доставки: уведомление отправлено (или чат мёртв — не ретраить)."""
    resp = supabase_post("tg_push_log", {"notification_id": notification_id})
    # 409 (дубль PK) тоже считаем успехом — уже журналировано.
    return bool(resp is not None and (resp.ok or resp.status_code == 409))


# ---------- Административные функции ----------

def get_stats() -> dict:
    """Возвращает счётчик пользователей бота."""
    rows = supabase_get("users", {}) or []
    return {"total": len(rows)}


def get_all_telegram_ids() -> list:
    """Возвращает список всех telegram_id для рассылки."""
    rows = supabase_get("users", {}) or []
    return [r["telegram_id"] for r in rows if r.get("telegram_id")]


# ---------- Удалённое управление: стоп-кран инструментов ----------

def get_tools_admin() -> list:
    """Список инструментов со статусом магазина и флагом стоп-крана."""
    rows = supabase_get("tools", {"select": "slug,name,status,enabled",
                                  "order": "sort_order.asc"})
    return rows or []


def set_tool_field(slug: str, field: str, value) -> bool:
    """PATCH одного поля tools по slug. Разрешены только безопасные поля."""
    if field not in ("enabled", "status", "disabled_message"):
        return False
    resp = supabase_patch("tools", {"slug": slug}, {field: value})
    return bool(resp and resp.ok)


def stop_tool(slug: str, message: str | None) -> bool:
    """Стоп-кран: enabled=false (+ сообщение для стоп-экрана)."""
    data = {"enabled": False}
    if message:
        data["disabled_message"] = message
    resp = supabase_patch("tools", {"slug": slug}, data)
    return bool(resp and resp.ok)
