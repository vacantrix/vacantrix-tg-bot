# vacantrix/telegram/supabase.py
"""Функции работы с Supabase (включая реферальную систему, хранение последней версии и напоминания)."""

import logging
from datetime import datetime, timezone, timedelta

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


def create_user(telegram_id, applicant_id=None, referral_code=None, referred_by=None):
    data = {"telegram_id": telegram_id}
    if applicant_id:
        data["applicant_id"] = applicant_id
    if referral_code:
        data["referral_code"] = referral_code
    if referred_by:
        data["referred_by"] = referred_by
    return supabase_post("users", data)


def link_applicant(telegram_id, applicant_id):
    return supabase_patch("users", {"telegram_id": telegram_id},
                          {"applicant_id": applicant_id})


def update_subscription(applicant_id, expire_date):
    return supabase_patch("users", {"applicant_id": applicant_id},
                          {"subscription_expire": expire_date.isoformat()})


def update_subscription_by_telegram(telegram_id, expire_date):
    return supabase_patch("users", {"telegram_id": telegram_id},
                          {"subscription_expire": expire_date.isoformat()})


def count_referrals(referral_code):
    rows = supabase_get("users", {"referred_by": f"eq.{referral_code}"})
    return len(rows) if rows else 0


def get_referrer_telegram_id(user_telegram_id: int):
    user = get_user_by_telegram(user_telegram_id)
    if not user:
        return None
    referred_by = user.get("referred_by")
    if not referred_by:
        return None
    rows = supabase_get("users", {"referral_code": f"eq.{referred_by}"})
    if rows:
        return rows[0].get("telegram_id")
    return None


def add_subscription_days(telegram_id: int, days: int):
    user = get_user_by_telegram(telegram_id)
    if not user:
        return None
    current_expire = user.get("subscription_expire")
    now = datetime.now(timezone.utc)
    if current_expire:
        expire_dt = datetime.fromisoformat(current_expire.replace("Z", "+00:00"))
        if expire_dt > now:
            start_date = expire_dt
        else:
            start_date = now
    else:
        start_date = now
    new_expire = start_date + timedelta(days=days)
    return update_subscription_by_telegram(telegram_id, new_expire)


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


# ---------- Функции для хранения последней версии файла ----------
def save_latest_version(chat_id: int, message_id: int, file_name: str = ""):
    try:
        existing = supabase_get("latest_version", {"id": "eq.1"})
        if existing:
            return supabase_patch("latest_version", {"id": 1},
                                  {"chat_id": chat_id, "message_id": message_id,
                                   "file_name": file_name, "updated_at": "now()"})
        else:
            return supabase_post("latest_version",
                                 {"id": 1, "chat_id": chat_id, "message_id": message_id,
                                  "file_name": file_name})
    except Exception as e:
        logger.error(f"Ошибка сохранения последней версии: {e}")
        return None


def get_latest_version():
    rows = supabase_get("latest_version", {"id": "eq.1"})
    if rows:
        row = rows[0]
        return row.get("chat_id"), row.get("message_id")
    return None, None


# ---------- Функции для напоминаний об окончании подписки ----------
def get_reminder_status(user_id: int):
    rows = supabase_get("subscription_reminders", {"user_id": f"eq.{user_id}"})
    if rows:
        return rows[0]
    return None


def set_reminder_sent(user_id: int, reminder_type: str):
    data = {reminder_type: True}
    existing = get_reminder_status(user_id)
    if existing:
        return supabase_patch("subscription_reminders", {"user_id": user_id}, data)
    else:
        return supabase_post("subscription_reminders", {"user_id": user_id, reminder_type: True})


def reset_reminders(user_id: int):
    data = {"remind_12h_sent": False, "remind_1d_sent": False, "remind_3d_sent": False}
    existing = get_reminder_status(user_id)
    if existing:
        return supabase_patch("subscription_reminders", {"user_id": user_id}, data)
    else:
        return supabase_post("subscription_reminders", {"user_id": user_id, **data})


# ---------- Административные функции ----------

def get_stats() -> dict:
    """Возвращает статистику по пользователям и подпискам."""
    rows = supabase_get("users", {}) or []
    now = datetime.now(timezone.utc)
    active = expired = no_sub = 0
    for u in rows:
        expire = u.get("subscription_expire")
        if not expire:
            no_sub += 1
            continue
        try:
            dt = datetime.fromisoformat(expire.replace("Z", "+00:00"))
            if dt > now:
                active += 1
            else:
                expired += 1
        except ValueError:
            no_sub += 1
    return {"total": len(rows), "active": active, "expired": expired, "no_sub": no_sub}


def get_all_telegram_ids() -> list:
    """Возвращает список всех telegram_id для рассылки."""
    rows = supabase_get("users", {}) or []
    return [r["telegram_id"] for r in rows if r.get("telegram_id")]


def revoke_subscription(telegram_id: int):
    """Отзывает подписку пользователя (устанавливает expire = сейчас)."""
    now = datetime.now(timezone.utc)
    return supabase_patch("users", {"telegram_id": telegram_id},
                          {"subscription_expire": now.isoformat()})


# ---------- Новая функция для получения пользователей с истекающей подпиской ----------
def get_users_with_expiring_subscription():
    """
    Возвращает список пользователей, у которых подписка истекает в ближайшие 3 дня.
    Каждый элемент: {'telegram_id': int, 'days_left': float}
    """
    now = datetime.now(timezone.utc)
    # Получаем всех пользователей, у которых есть subscription_expire
    rows = supabase_get("users", {"subscription_expire": "not.is.null"})
    if not rows:
        return []
    result = []
    for user in rows:
        telegram_id = user.get("telegram_id")
        expire_str = user.get("subscription_expire")
        if not telegram_id or not expire_str:
            continue
        try:
            expire_dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Пропущена некорректная дата подписки user %s: %s", telegram_id, expire_str)
            continue
        time_left = expire_dt - now
        days_left = time_left.total_seconds() / 3600 / 24
        # Отбираем тех, у кого осталось от 0 до 3 дней (но не больше 3)
        if 0 < days_left <= 3:
            result.append({'telegram_id': telegram_id, 'days_left': days_left})
    return result
