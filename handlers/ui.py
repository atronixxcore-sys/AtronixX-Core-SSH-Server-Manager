"""ui.py - ابزارهای مشترک نمایش پیام (ادیت/ارسال امن) و نماهای تکراری (منوی اصلی، جزئیات سرور)"""

from __future__ import annotations

import html
import logging
import math
import time
from typing import Optional

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from core.jdate import fmt_dt, fmt_num
from core.security import is_admin
from database.db import db
from handlers import keyboards as K
from locales.strings import Raw, t

logger = logging.getLogger("atronixx.ui")

PM = ParseMode.HTML
esc = html.escape


def lang_of(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "fa")


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> Optional[Message]:
    """اگر از دکمه آمده پیام را ادیت می‌کند، وگرنه پیام جدید می‌فرستد."""
    q = update.callback_query
    if q is not None and q.message is not None:
        try:
            res = await q.edit_message_text(text, reply_markup=kb, parse_mode=PM)
            return res if isinstance(res, Message) else q.message
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return q.message
            logger.debug("edit failed, sending new message: %s", exc)
        except TelegramError:
            logger.debug("edit failed", exc_info=True)
    chat = update.effective_chat
    return await context.bot.send_message(chat.id, text, reply_markup=kb, parse_mode=PM)


async def edit(bot, chat_id: int, mid: int, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> bool:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=kb, parse_mode=PM)
        return True
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return True
        logger.debug("edit failed: %s", exc)
    except TelegramError:
        logger.debug("edit failed", exc_info=True)
    return False


async def send(bot, chat_id: int, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> Optional[Message]:
    try:
        return await bot.send_message(chat_id, text, reply_markup=kb, parse_mode=PM)
    except TelegramError as exc:
        logger.warning("send to %s failed: %s", chat_id, exc)
        return None


async def delete_message(msg: Optional[Message]) -> None:
    if msg is None:
        return
    try:
        await msg.delete()
    except TelegramError:
        pass


def days_left(exp: Optional[int]) -> int:
    return max(0, math.ceil(((exp or 0) - time.time()) / 86400))


# ------------------------------------------------------------------ نماها
async def status_line(user: dict, lang: str, hosts_n: int) -> str:
    if is_admin(user["id"]):
        return t("st_admin", lang)
    if await db.get_mode() == "free":
        return t("st_free", lang)
    return t(
        "st_paid",
        lang,
        until=fmt_dt(user["sub_expires_at"], lang),
        days=fmt_num(days_left(user["sub_expires_at"]), lang),
        used=fmt_num(hosts_n, lang),
        max=fmt_num(user["sub_max_hosts"], lang),
    )


async def main_menu_view(user: dict, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    n = await db.count_hosts(user["id"])
    text = t(
        "main_menu",
        lang,
        name=user["first_name"] or t("friend", lang),
        status=Raw(await status_line(user, lang, n)),
        hosts=fmt_num(n, lang),
    )
    return text, K.main_menu_kb(lang, is_admin(user["id"]))


async def show_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    user = await db.get_user(uid)
    lang = user["language"] if user else "fa"
    context.user_data["lang"] = lang
    text, kb = await main_menu_view(user, lang)
    await show(update, context, text, kb)


async def host_view(host_id: int, uid: int, lang: str) -> Optional[tuple[str, InlineKeyboardMarkup]]:
    host = await db.get_host(host_id, uid)
    if not host:
        return None
    group = await db.get_group(host["group_id"], uid) if host["group_id"] else None
    text = t(
        "host_detail",
        lang,
        label=host["label"],
        addr=f"{host['ip']}:{host['port']}",
        user=host["username"],
        auth=t("auth_pw" if host["auth_type"] == "password" else "auth_key", lang),
        fp=(host["host_fingerprint"] or "—")[:28],
        group=group["name"] if group else "—",
        date=fmt_dt(host["created_at"], lang, with_time=False),
    )
    return text, K.host_detail_kb(lang, host)
