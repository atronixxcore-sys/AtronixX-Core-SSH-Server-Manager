"""
admin.py - پنل مدیریت (فقط ادمین)

کاربران (لیست/جستجو/بن/حذف/پیام)، اعطای اشتراک با آیدی عددی، سوییچ مود رایگان/اشتراکی،
پیام همگانی، کانال‌های جوین اجباری، بکاپ/ریستور، نشست‌های زنده، تنظیمات، آمار و لاگ.
ادمین هرگز پسورد/کلید یا آیپی سرورهای کاربران را نمی‌بیند.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime

from telegram import Update
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from config import settings
from core import registry
from core.backup import build_backup, inspect_backup, restore_backup
from core.crypto import BACKUP_MAGIC, CryptoError
from core.jdate import fmt_dt, fmt_num
from core.security import is_admin
from database.db import db
from handlers import gate, terminal
from handlers import keyboards as K
from handlers import ui
from locales.strings import Raw, t

logger = logging.getLogger("atronixx.admin")

PER_PAGE = 8
BOOT = time.time()
MAX_RESTORE_BYTES = 20 * 1024 * 1024
_broadcast_running = False


# ================================================================== کمکی‌ها
async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_admin(update.effective_user.id):
        return True
    if update.callback_query:
        await update.callback_query.answer()
    return False


def _uid_from(data: str, idx: int = 2) -> int:
    return int(data.split(":")[idx])


def _parse_int(text: str, lo: int, hi: int) -> int | None:
    text = text.strip()
    if not re.fullmatch(r"\d{1,16}", text):
        return None
    n = int(text)
    return n if lo <= n <= hi else None


def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _fmt_uptime(seconds: float, lang: str) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return fmt_num(f"{d}d {h}h {m}m" if lang == "en" else f"{d} روز و {h} ساعت و {m} دقیقه", lang)


def _display(u: dict) -> str:
    name = (u.get("first_name") or "").strip() or (f"@{u['username']}" if u.get("username") else "—")
    return name[:22]


def _sub_state(u: dict) -> str:
    now = time.time()
    if u["is_banned"]:
        return "🚫"
    exp = u["sub_expires_at"]
    if exp and exp > now:
        return "💎"
    return "⌛" if exp else "⚪"


# ================================================================== خانه و آمار
async def _home_view(lang: str) -> tuple[str, object]:
    st = await db.stats()
    mode = await db.get_mode()
    text = t(
        "adm_home", lang,
        mode=t("mode_paid" if mode == "paid" else "mode_free", lang),
        users=fmt_num(st["users"], lang), subs=fmt_num(st["subs"], lang), banned=fmt_num(st["banned"], lang),
        live=fmt_num(len(registry.active_sessions), lang), max=fmt_num(await db.get_max_live(), lang),
    )
    kb = K.kb(
        [
            [K.btn(t("b_users", lang), "adm:users:all:0", K.PRIMARY), K.btn(t("b_grant", lang), "adm:grantnew", K.SUCCESS)],
            [K.btn(t("b_stats", lang), "adm:st"), K.btn(t("b_mode", lang), "adm:mode")],
            [K.btn(t("b_broadcast", lang), "adm:bc"), K.btn(t("b_channels", lang), "adm:ch")],
            [K.btn(t("b_backup", lang), "adm:bk"), K.btn(t("b_sessions", lang), "adm:ss")],
            [K.btn(t("b_settings", lang), "adm:set"), K.btn(t("b_adm_log", lang), "adm:log")],
            [K.btn(t("b_menu", lang), "menu:home")],
        ]
    )
    return text, kb


async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    await ui.show(update, context, *await _home_view(ui.lang_of(context)))


async def cb_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    st = await db.stats()
    mode = await db.get_mode()
    text = t(
        "adm_stats", lang,
        mode=t("mode_paid" if mode == "paid" else "mode_free", lang),
        users=fmt_num(st["users"], lang), subs=fmt_num(st["subs"], lang), banned=fmt_num(st["banned"], lang),
        hosts=fmt_num(st["hosts"], lang), live=fmt_num(len(registry.active_sessions), lang),
        sftp=fmt_num(len(registry.sftp_state), lang), ram=fmt_num(f"{_rss_mb():.0f}", lang),
        uptime=_fmt_uptime(time.time() - BOOT, lang),
    )
    await ui.show(update, context, text, K.kb([[K.btn(t("b_refresh", lang), "adm:st"), K.btn(t("b_back", lang), "adm:home")]]))


# ================================================================== کاربران
async def cb_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    _, _, flt, page = update.callback_query.data.split(":")
    page = int(page)
    rows_db, total = await db.list_users(flt, page * PER_PAGE, PER_PAGE)
    pages = max(1, math.ceil(total / PER_PAGE))
    rows = [
        [K.btn(f"{_sub_state(u)} {_display(u)} · {u['id']}", f"adm:u:{u['id']}")] for u in rows_db
    ]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(K.btn("◀️", f"adm:users:{flt}:{page - 1}"))
        nav.append(K.btn(f"{fmt_num(page + 1, lang)}/{fmt_num(pages, lang)}", "noop"))
        if page < pages - 1:
            nav.append(K.btn("▶️", f"adm:users:{flt}:{page + 1}"))
        rows.append(nav)
    flt_row = []
    for key, label in (("all", "f_all"), ("sub", "f_sub"), ("exp", "f_exp"), ("banned", "f_banned")):
        mark = "• " if key == flt else ""
        flt_row.append(K.btn(mark + t(label, lang), f"adm:users:{key}:0"))
    rows.append(flt_row[:2])
    rows.append(flt_row[2:])
    rows.append([K.btn(t("b_find", lang), "adm:find", K.PRIMARY), K.btn(t("b_back", lang), "adm:home")])
    await ui.show(update, context, t("adm_users_title", lang, total=fmt_num(total, lang)), K.kb(rows))


async def _user_view(uid: int, lang: str) -> tuple[str, object] | None:
    u = await db.user_with_hosts(uid)
    if not u:
        return None
    now = time.time()
    exp = u["sub_expires_at"]
    if exp and exp > now:
        sub = t("sub_active", lang, until=fmt_dt(exp, lang), days=fmt_num(ui.days_left(exp), lang), max=fmt_num(u["sub_max_hosts"], lang))
    elif exp:
        sub = t("sub_expired", lang, until=fmt_dt(exp, lang))
    else:
        sub = t("sub_none", lang)
    text = t(
        "adm_user", lang,
        name=_display(u), username=f"@{u['username']}" if u["username"] else "—", uid=str(uid),
        joined=fmt_dt(u["created_at"], lang), seen=fmt_dt(u["last_seen"], lang), hosts=fmt_num(u["host_count"], lang),
        sub=Raw(sub), banned=t("yes" if u["is_banned"] else "no", lang),
        live=t("yes" if uid in registry.active_sessions else "no", lang),
        started=t("yes" if u["started"] else "no", lang),
    )
    rows = [[K.btn(t("b_grant", lang), f"adm:grant:{uid}", K.SUCCESS)]]
    if exp:
        rows[0].append(K.btn(t("b_revoke", lang), f"adm:revoke:{uid}", K.DANGER))
    if not is_admin(uid):
        rows.append(
            [K.btn(t("b_unban", lang), f"adm:unban:{uid}", K.SUCCESS) if u["is_banned"] else K.btn(t("b_ban", lang), f"adm:ban:{uid}", K.DANGER),
             K.btn(t("b_delete_user", lang), f"adm:del:{uid}", K.DANGER)]
        )
    rows.append([K.btn(t("b_message", lang), f"adm:msg:{uid}"), K.btn(t("b_back", lang), "adm:users:all:0")])
    return text, K.kb(rows)


async def _show_user(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, edit_mid: int | None = None) -> None:
    lang = ui.lang_of(context)
    view = await _user_view(uid, lang)
    if view is None:
        text, kb = t("adm_user_missing", lang, uid=str(uid)), K.back_kb(lang, "adm:users:all:0")
    else:
        text, kb = view
    if edit_mid:
        await ui.edit(context.bot, update.effective_chat.id, edit_mid, text, kb)
    else:
        await ui.show(update, context, text, kb)


async def cb_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    await _show_user(update, context, _uid_from(update.callback_query.data))


async def cb_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    uid, lang = _uid_from(q.data), ui.lang_of(context)
    if is_admin(uid):
        await q.answer(t("adm_cannot_self", lang), show_alert=True)
        return
    await q.answer()
    banning = q.data.startswith("adm:ban:")
    await db.ensure_placeholder(uid)
    await db.set_banned(uid, banning)
    await db.log("ban" if banning else "unban", str(uid))
    if banning:
        await registry.reset_chat(uid, keep_terminal=False)
        u = await db.get_user(uid)
        await ui.send(context.bot, uid, t("banned", u["language"] if u else "fa", support=settings.support_username))
    await _show_user(update, context, uid)


async def cb_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang, uid = ui.lang_of(context), _uid_from(q.data)
    if q.data.startswith("adm:revokeyes"):
        await db.revoke(uid)
        await db.log("revoke", str(uid))
        from core import housekeeping

        await housekeeping.enforce_access(context.bot)
        await _show_user(update, context, uid)
        return
    await ui.show(update, context, t("adm_revoke_confirm", lang, uid=str(uid)), K.danger_confirm_kb(lang, f"adm:revokeyes:{uid}", f"adm:u:{uid}"))


async def cb_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    lang, uid = ui.lang_of(context), _uid_from(q.data)
    if is_admin(uid):
        await q.answer(t("adm_cannot_self", lang), show_alert=True)
        return
    await q.answer()
    if q.data.startswith("adm:delyes"):
        await registry.reset_chat(uid, keep_terminal=False)
        await db.delete_user(uid)
        await db.log("delete_user", str(uid))
        await ui.show(update, context, t("adm_user_deleted", lang, uid=str(uid)), K.back_kb(lang, "adm:users:all:0"))
        return
    await ui.show(update, context, t("adm_delete_confirm", lang, uid=str(uid)), K.danger_confirm_kb(lang, f"adm:delyes:{uid}", f"adm:u:{uid}", t("b_delete_yes", lang)))


# ================================================================== اعطای اشتراک
async def cb_grant_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    msg = await ui.show(update, context, t("adm_grant_ask_id", lang), K.back_kb(lang, "adm:home"))
    context.user_data["flow"] = {"type": "adm_grant_id", "mid": msg.message_id}


async def cb_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang, uid = ui.lang_of(context), _uid_from(update.callback_query.data)
    msg = await ui.show(update, context, t("adm_grant_ask_days", lang, uid=str(uid)), K.back_kb(lang, f"adm:u:{uid}"))
    context.user_data["flow"] = {"type": "adm_grant_days", "uid": uid, "mid": msg.message_id}


async def cb_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    msg = await ui.show(update, context, t("adm_find_ask", lang), K.back_kb(lang, "adm:users:all:0"))
    context.user_data["flow"] = {"type": "adm_find", "mid": msg.message_id}


async def cb_message_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang, uid = ui.lang_of(context), _uid_from(update.callback_query.data)
    msg = await ui.show(update, context, t("adm_msg_ask", lang, uid=str(uid)), K.back_kb(lang, f"adm:u:{uid}"))
    context.user_data["flow"] = {"type": "adm_msg", "uid": uid, "mid": msg.message_id}


# ================================================================== مود
async def cb_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    if q.data.startswith("adm:modeyes"):
        new = q.data.split(":")[2]
        await db.set_mode(new)
        await db.log("mode", new)
        from core import housekeeping

        await housekeeping.enforce_access(context.bot)
        await ui.show(update, context, t("adm_mode_changed", lang, mode=t("mode_paid" if new == "paid" else "mode_free", lang)), K.back_kb(lang, "adm:home"))
        return
    cur = await db.get_mode()
    target = "free" if cur == "paid" else "paid"
    text = t("adm_mode", lang, mode=t("mode_paid" if cur == "paid" else "mode_free", lang), note=t("adm_mode_note_" + target, lang))
    label = t("b_to_free" if target == "free" else "b_to_paid", lang)
    await ui.show(update, context, text, K.kb([[K.btn(label, f"adm:modeyes:{target}", K.PRIMARY)], [K.btn(t("b_back", lang), "adm:home")]]))


# ================================================================== پیام همگانی
async def cb_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    if q.data.startswith("adm:bcaud"):
        aud = q.data.split(":")[2]
        msg = await ui.show(update, context, t("adm_bc_ask", lang), K.back_kb(lang, "adm:bc"))
        context.user_data["flow"] = {"type": "adm_bc", "audience": aud, "mid": msg.message_id}
        return
    rows = [
        [K.btn(t("aud_all", lang), "adm:bcaud:all", K.PRIMARY), K.btn(t("aud_subs", lang), "adm:bcaud:subs", K.PRIMARY)],
        [K.btn(t("b_back", lang), "adm:home")],
    ]
    await ui.show(update, context, t("adm_bc_audience", lang), K.kb(rows))


async def _bc_capture(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict) -> None:
    msg, lang = update.message, ui.lang_of(context)
    if msg.photo:
        flow["payload"] = {"kind": "photo", "file_id": msg.photo[-1].file_id, "html": msg.caption_html or ""}
    else:
        flow["payload"] = {"kind": "text", "html": msg.text_html}
    ids = await db.audience_ids(flow["audience"])
    flow["count"] = len(ids)
    p = flow["payload"]
    try:
        if p["kind"] == "photo":
            await context.bot.send_photo(msg.chat_id, p["file_id"], caption=p["html"] or None, parse_mode=ui.PM)
        else:
            await context.bot.send_message(msg.chat_id, p["html"], parse_mode=ui.PM)
    except TelegramError as exc:
        flow.pop("payload", None)
        await ui.edit(context.bot, msg.chat_id, flow["mid"], t("adm_bc_bad_format", lang, err=str(exc)[:150]) + "\n\n" + t("adm_bc_ask", lang), K.back_kb(lang, "adm:bc"))
        return
    rows = [[K.btn(t("b_send_now", lang, n=fmt_num(len(ids), lang)), "af:bcgo", K.SUCCESS)], [K.btn(t("b_cancel", lang), "adm:bc", K.DANGER)]]
    await ui.edit(context.bot, msg.chat_id, flow["mid"], t("adm_bc_confirm", lang, n=fmt_num(len(ids), lang), aud=t("aud_" + flow["audience"], lang)), K.kb(rows))


async def cb_bc_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _broadcast_running
    if not await _guard(update, context):
        return
    q = update.callback_query
    lang = ui.lang_of(context)
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") != "adm_bc" or "payload" not in flow:
        await q.answer(t("flow_expired", lang), show_alert=True)
        return
    if _broadcast_running:
        await q.answer(t("adm_bc_busy", lang), show_alert=True)
        return
    await q.answer()
    context.user_data.pop("flow", None)
    ids = await db.audience_ids(flow["audience"])
    _broadcast_running = True
    await ui.show(update, context, t("adm_bc_progress", lang, done="0", total=fmt_num(len(ids), lang)), None)
    context.application.create_task(_broadcast(context.bot, q.message.chat_id, q.message.message_id, ids, flow["payload"], lang))


async def _send_one(bot, uid: int, payload: dict) -> None:
    if payload["kind"] == "photo":
        await bot.send_photo(uid, payload["file_id"], caption=payload["html"] or None, parse_mode=ui.PM)
    else:
        await bot.send_message(uid, payload["html"], parse_mode=ui.PM)


async def _broadcast(bot, chat_id: int, mid: int, ids: list[int], payload: dict, lang: str) -> None:
    global _broadcast_running
    sent = failed = blocked = 0
    try:
        for i, uid in enumerate(ids, 1):
            try:
                try:
                    await _send_one(bot, uid, payload)
                except RetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 1)
                    await _send_one(bot, uid, payload)
                sent += 1
            except Forbidden:
                blocked += 1
            except TelegramError:
                failed += 1
            if i % 25 == 0:
                await ui.edit(bot, chat_id, mid, t("adm_bc_progress", lang, done=fmt_num(i, lang), total=fmt_num(len(ids), lang)))
            await asyncio.sleep(0.06)   # ≈ ۱۶ پیام در ثانیه؛ زیر سقف تلگرام
        await db.log("broadcast", f"{sent}/{len(ids)}")
        await ui.edit(
            bot, chat_id, mid,
            t("adm_bc_done", lang, sent=fmt_num(sent, lang), blocked=fmt_num(blocked, lang), failed=fmt_num(failed, lang)),
            K.back_kb(lang, "adm:home"),
        )
    finally:
        _broadcast_running = False


# ================================================================== کانال‌های جوین اجباری
async def cb_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    if q.data.startswith("adm:chdel"):
        await db.remove_channel(_uid_from(q.data))
        await db.log("channel_remove", q.data.split(":")[2])
    channels = await db.list_channels()
    rows = [[K.btn(f"🗑 {c['title'] or c['username'] or c['chat_id']}", f"adm:chdel:{c['id']}", K.DANGER)] for c in channels]
    rows.append([K.btn(t("b_add_channel", lang), "adm:chadd", K.SUCCESS), K.btn(t("b_back", lang), "adm:home")])
    text = t("adm_channels", lang, n=fmt_num(len(channels), lang)) if channels else t("adm_channels_empty", lang)
    await ui.show(update, context, text, K.kb(rows))


async def cb_channel_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    msg = await ui.show(update, context, t("adm_channel_ask", lang), K.back_kb(lang, "adm:ch"))
    context.user_data["flow"] = {"type": "adm_chadd", "mid": msg.message_id}


async def _channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict) -> None:
    msg, lang, bot = update.message, ui.lang_of(context), context.bot
    ref = None
    origin = getattr(msg, "forward_origin", None)
    if origin is not None and getattr(origin, "chat", None) is not None:
        ref = origin.chat.id
    else:
        text = (msg.text or "").strip()
        text = re.sub(r"^https?://t\.me/", "@", text)
        if re.fullmatch(r"-?\d{5,}", text):
            ref = int(text)
        elif re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,}", text):
            ref = "@" + text.lstrip("@")
    err = None
    if ref is None:
        err = t("adm_channel_bad", lang)
    else:
        try:
            chat = await bot.get_chat(ref)
            me = await bot.get_chat_member(chat.id, bot.id)
            if me.status not in ("administrator", "creator"):
                err = t("adm_channel_not_admin", lang)
            else:
                link = f"https://t.me/{chat.username}" if chat.username else getattr(chat, "invite_link", None)
                if not link:
                    try:
                        link = await bot.export_chat_invite_link(chat.id)
                    except TelegramError:
                        link = None
                await db.add_channel(chat.id, chat.title or "", chat.username, link)
                await db.log("channel_add", str(chat.id), chat.title)
        except TelegramError as exc:
            err = t("adm_channel_error", lang, err=str(exc)[:120])
    if err:
        await ui.edit(bot, msg.chat_id, flow["mid"], err + "\n\n" + t("adm_channel_ask", lang), K.back_kb(lang, "adm:ch"))
        return
    context.user_data.pop("flow", None)
    channels = await db.list_channels()
    rows = [[K.btn(f"🗑 {c['title'] or c['username'] or c['chat_id']}", f"adm:chdel:{c['id']}", K.DANGER)] for c in channels]
    rows.append([K.btn(t("b_add_channel", lang), "adm:chadd", K.SUCCESS), K.btn(t("b_back", lang), "adm:home")])
    await ui.edit(bot, msg.chat_id, flow["mid"], t("adm_channel_added", lang) + "\n\n" + t("adm_channels", lang, n=fmt_num(len(channels), lang)), K.kb(rows))


# ================================================================== بکاپ / ریستور
async def cb_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    if q.data == "adm:bknow":
        context.application.create_task(send_backup(context.bot, q.message.chat_id, lang, manual=True))
        return
    last = await db.get_setting("last_backup")
    text = t("adm_backup", lang, hours=fmt_num(settings.backup_interval_hours, lang), last=fmt_dt(int(last), lang) if last else "—")
    rows = [
        [K.btn(t("b_backup_now", lang), "adm:bknow", K.SUCCESS), K.btn(t("b_restore", lang), "adm:rs", K.DANGER)],
        [K.btn(t("b_back", lang), "adm:home")],
    ]
    await ui.show(update, context, text, K.kb(rows))


async def send_backup(bot, chat_id: int, lang: str, manual: bool = False) -> bool:
    """بکاپ رمزگذاری‌شده را می‌سازد و برای ادمین می‌فرستد (دستی یا خودکار)."""
    try:
        blob = await build_backup()
        import io

        bio = io.BytesIO(blob)
        bio.name = f"AtronixX-Core_{datetime.now().strftime('%Y%m%d_%H%M')}.atxbak"
        st = await db.stats()
        await bot.send_document(
            chat_id, bio,
            caption=t("adm_backup_caption", lang, users=fmt_num(st["users"], lang), hosts=fmt_num(st["hosts"], lang), when=fmt_dt(time.time(), lang)),
            parse_mode=ui.PM,
        )
        await db.set_setting("last_backup", str(int(time.time())))
        await db.log("backup", "manual" if manual else "auto")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("backup failed")
        await ui.send(bot, chat_id, t("adm_backup_failed", lang, err=str(exc)[:200]))
        return False


async def cb_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    msg = await ui.show(update, context, t("adm_restore_ask", lang), K.back_kb(lang, "adm:bk"))
    context.user_data["flow"] = {"type": "adm_rs_file", "mid": msg.message_id}


async def _restore_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict, passphrase: str) -> None:
    lang, bot, chat_id = ui.lang_of(context), context.bot, update.effective_chat.id
    if not flow["blob"].startswith(BACKUP_MAGIC):
        flow["type"] = "adm_rs_file"
        await ui.edit(bot, chat_id, flow["mid"], t("adm_restore_invalid", lang) + "\n\n" + t("adm_restore_ask", lang), K.back_kb(lang, "adm:bk"))
        return
    prev = flow["type"]
    try:
        meta = await inspect_backup(flow["blob"], passphrase)
    except CryptoError:
        flow["type"] = "adm_rs_pass"
        prefix = t("adm_restore_wrong", lang) + "\n\n" if prev == "adm_rs_pass" else ""
        await ui.edit(bot, chat_id, flow["mid"], prefix + t("adm_restore_pass", lang), K.back_kb(lang, "adm:bk"))
        return
    flow["pass"] = passphrase
    flow["type"] = "adm_rs_ready"
    rows = [[K.btn(t("b_restore_now", lang), "af:rsgo", K.DANGER)], [K.btn(t("b_cancel", lang), "adm:bk")]]
    await ui.edit(
        bot, chat_id, flow["mid"],
        t("adm_restore_confirm", lang, users=fmt_num(meta.get("users", "?"), lang), hosts=fmt_num(meta.get("hosts", "?"), lang), when=fmt_dt(meta.get("created"), lang)),
        K.kb(rows),
    )


async def cb_restore_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    lang = ui.lang_of(context)
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") != "adm_rs_ready":
        await q.answer(t("flow_expired", lang), show_alert=True)
        return
    await q.answer()
    context.user_data.pop("flow", None)
    await ui.show(update, context, t("adm_restore_working", lang), None)
    try:
        await restore_backup(flow["blob"], flow["pass"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore failed")
        await ui.show(update, context, t("adm_restore_failed", lang, err=str(exc)[:200]), K.back_kb(lang, "adm:bk"))
        return
    await registry.close_all()
    await db.log("restore", None)
    await ui.show(update, context, t("adm_restore_done", lang), K.back_kb(lang, "adm:home"))


# ================================================================== نشست‌های زنده
async def cb_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    lang = ui.lang_of(context)
    if q.data.startswith("adm:kill"):
        chat_id = _uid_from(q.data)
        u = await db.get_user(chat_id)
        if chat_id in registry.active_sessions:
            await terminal.close_with_notice(context.bot, chat_id, u["language"] if u else "fa", "session_killed")
            await db.log("kill_session", str(chat_id))
    await q.answer()
    rows = []
    now = time.monotonic()
    for chat_id, s in list(registry.active_sessions.items()):
        u = await db.get_user(chat_id)
        mins = int((now - s.started_at) / 60)
        rows.append([K.btn(f"🔌 {_display(u or {})} · {s.host['label']} · {fmt_num(mins, lang)}m"[:60], f"adm:kill:{chat_id}", K.DANGER)])
    rows.append([K.btn(t("b_refresh", lang), "adm:ss"), K.btn(t("b_back", lang), "adm:home")])
    text = t("adm_sessions", lang, n=fmt_num(len(registry.active_sessions), lang), max=fmt_num(await db.get_max_live(), lang))
    await ui.show(update, context, text, K.kb(rows))


# ================================================================== تنظیمات و لاگ
async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    cur = await db.get_max_live()
    if q.data.startswith("adm:live:"):
        cur = max(1, min(200, cur + int(q.data.split(":")[2])))
        await db.set_setting("max_live", str(cur))
        await db.log("max_live", str(cur))
    rows = [
        [K.btn("−5", "adm:live:-5"), K.btn("−1", "adm:live:-1"), K.btn("+1", "adm:live:1"), K.btn("+5", "adm:live:5")],
        [K.btn(t("b_back", lang), "adm:home")],
    ]
    await ui.show(update, context, t("adm_settings", lang, max=fmt_num(cur, lang), idle=fmt_num(settings.idle_timeout_min, lang), life=fmt_num(settings.max_session_hours, lang)), K.kb(rows))


async def cb_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    lang = ui.lang_of(context)
    logs = await db.recent_logs(15)
    lines = [f"<code>{fmt_dt(r['ts'], lang)}</code> · <b>{ui.esc(r['action'])}</b> {ui.esc(r['target'] or '')}" for r in logs]
    text = t("adm_log", lang) + "\n\n" + ("\n".join(lines) if lines else t("adm_log_empty", lang))
    await ui.show(update, context, text, K.back_kb(lang, "adm:home"))


# ================================================================== ورودی‌های متنی و فایل
async def flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("flow")
    if not flow or not str(flow.get("type", "")).startswith("adm_"):
        return False
    if not is_admin(update.effective_user.id):
        context.user_data.pop("flow", None)
        return False
    msg, lang, bot = update.message, ui.lang_of(context), context.bot
    kind, chat_id = flow["type"], msg.chat_id
    text = (msg.text or "").strip()

    if kind == "adm_bc":                       # محتوای پیام همگانی (پیام ادمین را نگه می‌داریم تا پیش‌نمایش روشن باشد)
        await _bc_capture(update, context, flow)
        return True
    if kind == "adm_chadd":
        await _channel_input(update, context, flow)
        return True
    await ui.delete_message(msg)

    if kind == "adm_find":
        uid = _parse_int(text, 1, 10**15)
        if uid is None:
            await ui.edit(bot, chat_id, flow["mid"], t("adm_bad_id", lang) + "\n\n" + t("adm_find_ask", lang), K.back_kb(lang, "adm:users:all:0"))
            return True
        context.user_data.pop("flow", None)
        await _show_user(update, context, uid, edit_mid=flow["mid"])
    elif kind == "adm_grant_id":
        uid = _parse_int(text, 1, 10**15)
        if uid is None:
            await ui.edit(bot, chat_id, flow["mid"], t("adm_bad_id", lang) + "\n\n" + t("adm_grant_ask_id", lang), K.back_kb(lang, "adm:home"))
            return True
        flow.update(type="adm_grant_days", uid=uid)
        await ui.edit(bot, chat_id, flow["mid"], t("adm_grant_ask_days", lang, uid=str(uid)), K.back_kb(lang, "adm:home"))
    elif kind == "adm_grant_days":
        days = _parse_int(text, 1, 3650)
        if days is None:
            await ui.edit(bot, chat_id, flow["mid"], t("adm_bad_number", lang) + "\n\n" + t("adm_grant_ask_days", lang, uid=str(flow["uid"])), K.back_kb(lang, "adm:home"))
            return True
        flow.update(type="adm_grant_hosts", days=days)
        await ui.edit(bot, chat_id, flow["mid"], t("adm_grant_ask_hosts", lang, uid=str(flow["uid"]), days=fmt_num(days, lang)), K.back_kb(lang, "adm:home"))
    elif kind == "adm_grant_hosts":
        n = _parse_int(text, 1, 1000)
        if n is None:
            await ui.edit(bot, chat_id, flow["mid"], t("adm_bad_number", lang) + "\n\n" + t("adm_grant_ask_hosts", lang, uid=str(flow["uid"]), days=fmt_num(flow["days"], lang)), K.back_kb(lang, "adm:home"))
            return True
        context.user_data.pop("flow", None)
        uid, days = flow["uid"], flow["days"]
        user = await db.grant(uid, days, n)
        await db.log("grant", str(uid), f"{days}d/{n}")
        sent = await gate.send_activation(bot, uid)
        result = t(
            "adm_granted", lang, uid=str(uid), days=fmt_num(days, lang), hosts=fmt_num(n, lang),
            until=fmt_dt(user["sub_expires_at"], lang), notified=t("notified_yes" if sent else "notified_later", lang),
        )
        await ui.edit(bot, chat_id, flow["mid"], result, K.kb([[K.btn(t("b_user_page", lang), f"adm:u:{uid}", K.PRIMARY)], [K.btn(t("b_back", lang), "adm:home")]]))
    elif kind == "adm_msg":
        context.user_data.pop("flow", None)
        uid = flow["uid"]
        try:
            await bot.send_message(uid, t("msg_from_support", await _lang_of(uid)) + "\n\n" + msg.text_html, parse_mode=ui.PM)
            await db.log("message", str(uid))
            note = t("adm_msg_sent", lang)
        except TelegramError as exc:
            note = t("adm_msg_failed", lang, err=str(exc)[:120])
        await ui.edit(bot, chat_id, flow["mid"], note, K.back_kb(lang, f"adm:u:{uid}"))
    elif kind == "adm_rs_pass":
        await _restore_preview(update, context, flow, text)
    return True


async def _lang_of(uid: int) -> str:
    u = await db.get_user(uid)
    return u["language"] if u else "fa"


async def flow_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فایل بکاپ برای ریستور، یا عکس برای پیام همگانی."""
    flow = context.user_data.get("flow")
    if not flow or not is_admin(update.effective_user.id):
        return False
    msg, lang = update.message, ui.lang_of(context)
    if flow.get("type") == "adm_chadd":
        await _channel_input(update, context, flow)
        return True
    if flow.get("type") == "adm_bc" and msg.photo:
        await _bc_capture(update, context, flow)
        return True
    if flow.get("type") == "adm_rs_file" and msg.document:
        doc = msg.document
        if (doc.file_size or 0) > MAX_RESTORE_BYTES:
            await ui.edit(context.bot, msg.chat_id, flow["mid"], t("adm_restore_toobig", lang) + "\n\n" + t("adm_restore_ask", lang), K.back_kb(lang, "adm:bk"))
            return True
        try:
            tg_file = await doc.get_file()
            flow["blob"] = bytes(await tg_file.download_as_bytearray())
        except TelegramError as exc:
            await ui.edit(context.bot, msg.chat_id, flow["mid"], t("adm_channel_error", lang, err=str(exc)[:120]), K.back_kb(lang, "adm:bk"))
            return True
        await _restore_preview(update, context, flow, settings.backup_passphrase)
        return True
    return False
