"""
gate.py - دروازه‌ی دسترسی (قبل از همه‌ی هندلرها اجرا می‌شود)

ترتیب بررسی: بن → اشتراک (در مود اشتراکی) → قوانین → جوین اجباری
ادمین همیشه عبور می‌کند. هر ناوبری با دکمه، ویزارد/فلوی نیمه‌کاره را لغو می‌کند.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ChatType
from telegram.error import Forbidden, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import settings
from core.jdate import fmt_dt, fmt_num
from core.security import is_admin
from database.db import db
from handlers import keyboards as K
from handlers import ui
from locales.strings import Raw, t

logger = logging.getLogger("atronixx.gate")

ALWAYS_OK = ("lang:", "terms:", "join:", "noop")
FLOW_PREFIXES = ("wz:", "af:", "noop")   # دکمه‌هایی که خودشان بخشی از یک فلوی فعال‌اند
_member_cache: dict[tuple[int, int], float] = {}


def has_access(user: dict, mode: str) -> bool:
    if user["is_banned"]:
        return False
    if mode == "free":
        return True
    exp = user["sub_expires_at"]
    return bool(exp and exp > time.time())


async def _missing_channels(bot, uid: int) -> list[dict]:
    missing = []
    now = time.time()
    for ch in await db.list_channels():
        key = (uid, ch["chat_id"])
        if _member_cache.get(key, 0) > now:
            continue
        try:
            member = await bot.get_chat_member(ch["chat_id"], uid)
            ok = member.status in ("member", "administrator", "creator") or (
                member.status == "restricted" and getattr(member, "is_member", False)
            )
        except TelegramError as exc:
            # ربات دسترسی ندارد/کانال حذف شده → کاربر را قفل نکن
            logger.warning("membership check failed for channel %s: %s", ch["chat_id"], exc)
            continue
        if ok:
            _member_cache[key] = now + 300
        else:
            missing.append(ch)
    if len(_member_cache) > 5000:
        _member_cache.clear()
    return missing


def forget_membership(uid: int) -> None:
    for key in [k for k in _member_cache if k[0] == uid]:
        _member_cache.pop(key, None)


async def evaluate(bot, user: dict) -> tuple[str | None, list | None]:
    """دلیل مسدودی را برمی‌گرداند (یا None اگر آزاد است)."""
    if is_admin(user["id"]):
        return None, None
    if user["is_banned"]:
        return "banned", None
    mode = await db.get_mode()
    if not has_access(user, mode):
        return "noaccess", None
    if not user["terms_accepted"]:
        return "terms", None
    missing = await _missing_channels(bot, user["id"])
    if missing:
        return "join", missing
    return None, None


async def render_block(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str, extra, user: dict) -> None:
    lang = user["language"]
    support = settings.support_username
    if reason == "banned":
        await ui.show(update, context, t("banned", lang, support=support), K.gate_kb(lang))
    elif reason == "noaccess":
        key = "no_access_expired" if user["sub_expires_at"] else "no_access"
        await ui.show(update, context, t(key, lang, support=support, uid=str(user["id"])), K.gate_kb(lang))
    elif reason == "terms":
        rows = [[K.btn(t("b_accept", lang), "terms:accept", K.SUCCESS)], [K.btn(t("b_lang", lang), "lang:toggle")]]
        await ui.show(update, context, t("terms", lang), K.kb(rows))
    elif reason == "join":
        rows = []
        for ch in extra:
            link = ch.get("link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
            if link:
                rows.append([K.btn(f"📢 {ch['title'] or ch['username'] or ''}".strip(), url=link)])
        rows.append([K.btn(t("b_joined", lang), "join:check", K.SUCCESS)])
        rows.append([K.btn(t("b_lang", lang), "lang:toggle")])
        await ui.show(update, context, t("join_required", lang), K.kb(rows))


async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """بعد از تغییر زبان/قبول قوانین/جوین: صفحه‌ی درست را نشان می‌دهد. True اگر آزاد بود."""
    user = await db.get_user(update.effective_user.id)
    context.user_data["lang"] = user["language"]
    reason, extra = await evaluate(context.bot, user)
    if reason:
        await render_block(update, context, reason, extra, user)
        return False
    await ui.show_main(update, context)
    return True


async def send_activation(bot, uid: int) -> bool:
    """پیام «اشتراک فعال شد» برای کاربر. اگر هنوز ربات را استارت نزده باشد False (بعداً ارسال می‌شود)."""
    user = await db.get_user(uid)
    if not user:
        return False
    lang = user["language"]
    text = t(
        "activated",
        lang,
        days=fmt_num(ui.days_left(user["sub_expires_at"]), lang),
        hosts=fmt_num(user["sub_max_hosts"], lang),
        until=fmt_dt(user["sub_expires_at"], lang),
    )
    try:
        await bot.send_message(uid, text, reply_markup=K.main_menu_kb(lang, False), parse_mode=ui.PM)
    except (Forbidden, TelegramError) as exc:
        logger.info("activation notice to %s deferred: %s", uid, exc)
        return False
    await db.clear_notify_pending(uid)
    return True


async def _notify_admin_new_user(context: ContextTypes.DEFAULT_TYPE, user: dict, tg_user) -> None:
    mode = await db.get_mode()
    lang = "fa"
    uname = f"@{tg_user.username}" if tg_user.username else "—"
    text = t("adm_new_user", lang, name=tg_user.full_name, username=uname, uid=str(user["id"]))
    rows = []
    if mode == "paid":
        rows.append([K.btn(t("b_grant", lang), f"adm:grant:{user['id']}", K.SUCCESS), K.btn(t("b_ban", lang), f"adm:ban:{user['id']}", K.DANGER)])
        await db.mark_request_notified(user["id"])
    await ui.send(context.bot, settings.admin_id, text, K.kb(rows) if rows else None)


async def pre_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user, chat = update.effective_user, update.effective_chat
    if tg_user is None or chat is None:
        return
    if chat.type != ChatType.PRIVATE:
        raise ApplicationHandlerStop
    user, created = await db.ensure_user(tg_user.id, tg_user.username, tg_user.full_name)
    context.user_data["lang"] = user["language"]
    q = update.callback_query
    data = q.data if q else ""

    # هر ناوبری با دکمه، فلوی نیمه‌کاره را لغو می‌کند
    if q and not data.startswith(FLOW_PREFIXES):
        context.user_data.pop("flow", None)

    if created and not is_admin(tg_user.id):
        await _notify_admin_new_user(context, user, tg_user)

    reason, extra = await evaluate(context.bot, user)
    if reason is None:
        if user["notify_pending"] and not is_admin(tg_user.id):
            await send_activation(context.bot, user["id"])
        return
    if data.startswith(ALWAYS_OK):
        return
    if q:
        try:
            await q.answer()
        except TelegramError:
            pass
    # درخواست دسترسی کاربر تازه‌ی بدون اشتراک (فقط یک‌بار) برای ادمین
    if reason == "noaccess" and not user["request_notified"] and not created:
        await _notify_admin_new_user(context, user, tg_user)
    await render_block(update, context, reason, extra, user)
    raise ApplicationHandlerStop
