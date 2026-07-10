# vacantrix/telegram/supabase.py
"""Функции работы с Supabase (пользователи бота — только чтение подписки как справка)."""

import logging
from datetime import datetime, timezone

import requests

from vacantrix.telegram.config import SUPABASE_URL, HEADERS

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


def get_user_by_telegram(telegram_id):
    rows = supabase_get("users", {"telegram_id": f"eq.{telegram_id}"})
    return rows[0] if rows else None


def get_user_by_applicant(applicant_id):
    rows = supabase_get("users", {"applicant_id": f"eq.{applicant_id}"})
    return rows[0] if rows else None


def create_user(telegram_id, applicant_id=None):
    data = {"telegram_id": telegram_id}
    if applicant_id:
        data["applicant_id"] = applicant_id
    return supabase_post("users", data)


def link_applicant(telegram_id, applicant_id):
    return supabase_patch("users", {"telegram_id": telegram_id},
                          {"applicant_id": applicant_id})


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


def check_subscription_status(profile):
    if not profile:
        return "❌ Не активна"
    expire = profile.get("subscription_expire")
    if not expire:
        return "❌ Не активна"
    try:
        expire_dt = datetime.fromisoformat(expire.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Некорректная дата подписки: %s", expire)
        return "⚠️ Требует проверки"
    if expire_dt > datetime.now(timezone.utc):
        days_left = (expire_dt - datetime.now(timezone.utc)).days
        return f"✅ Активна ({days_left} дн.)"
    return "❌ Истекла"


# ---------- Административные функции ----------

def get_stats() -> dict:
    """Возвращает счётчик пользователей бота."""
    rows = supabase_get("users", {}) or []
    return {"total": len(rows)}


def get_all_telegram_ids() -> list:
    """Возвращает список всех telegram_id для рассылки."""
    rows = supabase_get("users", {}) or []
    return [r["telegram_id"] for r in rows if r.get("telegram_id")]


# ---------- Удалённое управление: стоп-кран + арбитраж ----------

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


def get_open_disputes() -> list:
    """Спорные сделки биржи (для арбитража)."""
    rows = supabase_get("tasks_deals", {
        "status": "eq.disputed",
        "select": "id,amount,fee,task_id,customer_id,executor_id,held_at",
        "order": "held_at.desc.nullslast"})
    return rows or []


def resolve_dispute(deal_id: str, decision: str, reason: str) -> tuple[bool, str]:
    """Арбитраж через Edge tasks-robokassa-resolve (x-admin-secret + service_role)."""
    import requests
    from vacantrix.telegram.config import (
        FUNCTIONS_URL, ADMIN_SECRET, SUPABASE_SERVICE_KEY,
    )
    if not ADMIN_SECRET:
        return False, "ADMIN_SECRET не задан в окружении бота — арбитраж недоступен"
    try:
        r = requests.post(
            f"{FUNCTIONS_URL}/tasks-robokassa-resolve",
            json={"deal_id": deal_id, "decision": decision, "reason": reason},
            headers={"x-admin-secret": ADMIN_SECRET,
                     "apikey": SUPABASE_SERVICE_KEY,
                     "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                     "Content-Type": "application/json"},
            timeout=30)
        if r.ok:
            return True, "готово"
        if r.status_code == 404:
            return False, "Edge-функция не задеплоена (контур Robokassa ещё не готов)"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except requests.RequestException as exc:
        return False, str(exc)
