"""
terminal.py - نشست زنده‌ی ترمینال (قلب شبیه‌سازی Termius) + مسیر اتصال مشترک

جریان: دکمه‌ی اتصال → open_trusted (اثرانگشت TOFU) → [تایید کاربر] → PTY → یک تسک پس‌زمینه
هر ~۱ ثانیه پیام تلگرام را با صفحه‌ی فعلی pyte آپدیت می‌کند.

عملیات‌های SFTP و مانیتورینگ هم از همین مسیر اتصال/تایید اثرانگشت استفاده می‌کنند
(registry.actions) تا هیچ اتصالی بدون تایید کلید میزبان برقرار نشود.
اتصال‌ها در تسک پس‌زمینه انجام می‌شوند تا کاربر بتواند «لغو» بزند و بقیه‌ی کاربران معطل نمانند.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from html import escape

from telegram import Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import ContextTypes

from config import settings
from core import registry
from core.errors import HostKeyUntrusted, SSHConnectError, TargetBlocked
from core.registry import PendingTrust
from core.security import connect_limiter
from core.ssh_manager import TerminalSession, open_trusted
from database.db import db
from handlers import keyboards as K
from handlers import ui
from locales.strings import t

logger = logging.getLogger("atronixx.terminal")

ACTION_OF = {"conn": "terminal", "sftp": "sftp", "mon": "monitor"}


def refresh_interval() -> float:
    """با زیاد شدن نشست‌های هم‌زمان، فاصله‌ی آپدیت بیشتر می‌شود (محدودیت تلگرام + CPU کم)."""
    n = len(registry.active_sessions)
    return 1.0 if n <= 12 else min(3.0, n / 12)


def _compose(session: TerminalSession, label: str, lang: str) -> str:
    """پیام زنده‌ی ترمینال با HTML. خروجی سرور کاملاً escape می‌شود."""
    body = session.render()
    if len(body) > 3300:
        body = body[-3300:]
    hint = t("terminal_hint", lang).strip()
    return (
        f"<b>{escape(settings.brand_header)}</b> 🖥 {escape(label)}\n"
        f"<code>{escape(session.host['ip'])}</code>\n\n"
        f"<pre>{escape(body)}</pre>\n"
        f"{hint}"
    )


# ================================================================== مسیر اتصال مشترک
async def cb_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """h:conn:<id> | h:sftp:<id> | h:mon:<id> | h:connf:<id> (اتصال اجباری؛ نشست قبلی بسته شود)"""
    q = update.callback_query
    _, kind, hid = q.data.split(":")
    force = kind == "connf"
    action = ACTION_OF["conn" if force else kind]
    await start_action(update, context, int(hid), action, force)


async def start_action(update: Update, context: ContextTypes.DEFAULT_TYPE, host_id: int, action: str, force: bool = False) -> None:
    q = update.callback_query
    uid, chat_id, lang = update.effective_user.id, update.effective_chat.id, ui.lang_of(context)
    if not connect_limiter.allow(uid):
        await q.answer(t("rate_limited", lang, sec=str(connect_limiter.retry_after(uid))), show_alert=True)
        return
    if action == "terminal":
        existing = registry.active_sessions.get(chat_id)
        if existing is not None and not existing.is_closed:
            if not force:
                await q.answer()
                await ui.show(
                    update, context, t("session_exists", lang),
                    K.kb([[K.btn(t("b_reconnect", lang), f"h:connf:{host_id}", K.DANGER)], [K.btn(t("b_back", lang), f"h:view:{host_id}")]]),
                )
                return
            await registry.close_session(chat_id)
        elif existing is not None:
            await registry.close_session(chat_id)
        if len(registry.active_sessions) >= await db.get_max_live():
            await q.answer(t("busy", lang), show_alert=True)
            return
    await q.answer()
    host = await db.get_host_decrypted(host_id, uid)
    if host is None:
        await ui.show(update, context, t("host_missing", lang), K.back_kb(lang, "menu:hosts"))
        return
    await registry.reset_chat(chat_id, keep_terminal=True)
    task = context.application.create_task(_run_action(context.bot, chat_id, q.message.message_id, uid, lang, host, action))
    registry.connecting[chat_id] = task


async def _run_action(bot, chat_id: int, mid: int, uid: int, lang: str, host: dict, action: str) -> None:
    me = asyncio.current_task()
    back = K.back_kb(lang, f"h:view:{host['id']}")
    try:
        await ui.edit(bot, chat_id, mid, t("connecting", lang, label=host["label"]), K.connecting_kb(lang))
        try:
            conn = await open_trusted(host)
        except HostKeyUntrusted as exc:
            registry.pending_trust[chat_id] = PendingTrust(host["id"], exc.connection, exc.fingerprint, host, action, mid)
            key = "host_key_changed" if exc.stored else "host_key_prompt"
            await ui.edit(bot, chat_id, mid, t(key, lang, fp=exc.fingerprint, old=exc.stored or ""), K.trust_kb(lang))
            return
        except TargetBlocked:
            await ui.edit(bot, chat_id, mid, t("err_blocked_full", lang), back)
            return
        except SSHConnectError as exc:
            await ui.edit(bot, chat_id, mid, t("connect_failed", lang, err=str(exc)[:300]), back)
            return
        await _dispatch(bot, chat_id, mid, uid, lang, host, conn, action)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("connect flow failed")
        await ui.edit(bot, chat_id, mid, t("internal_error", lang), back)
    finally:
        if registry.connecting.get(chat_id) is me:
            registry.connecting.pop(chat_id, None)


async def _dispatch(bot, chat_id, mid, uid, lang, host, conn, action) -> None:
    try:
        await registry.actions[action](bot, chat_id, mid, uid, lang, host, conn)
    except asyncio.CancelledError:
        await registry._close_conn(conn)
        raise
    except Exception:  # noqa: BLE001
        logger.exception("action %s failed", action)
        await registry._close_conn(conn)
        if action == "terminal":
            await registry.close_session(chat_id)
        await ui.edit(bot, chat_id, mid, t("internal_error", lang), K.back_kb(lang, f"h:view:{host['id']}"))


async def cb_conn_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    registry.cancel_connecting(update.effective_chat.id)
    await ui.show_main(update, context)


async def cb_trust_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    chat_id, uid, lang = update.effective_chat.id, update.effective_user.id, ui.lang_of(context)
    pending = registry.pending_trust.pop(chat_id, None)
    if pending is None:
        await q.answer(t("flow_expired", lang), show_alert=True)
        return
    await q.answer()
    await db.set_host_fingerprint(pending.host_id, pending.fingerprint)
    pending.host["host_fingerprint"] = pending.fingerprint
    task = context.application.create_task(
        _dispatch_pending(context.bot, chat_id, q.message.message_id, uid, lang, pending)
    )
    registry.connecting[chat_id] = task


async def _dispatch_pending(bot, chat_id, mid, uid, lang, pending: PendingTrust) -> None:
    me = asyncio.current_task()
    try:
        await _dispatch(bot, chat_id, mid, uid, lang, pending.host, pending.connection, pending.action)
    finally:
        if registry.connecting.get(chat_id) is me:
            registry.connecting.pop(chat_id, None)


async def cb_trust_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await registry.discard_pending_trust(update.effective_chat.id)
    lang = ui.lang_of(context)
    await ui.show(update, context, t("cancelled", lang), K.back_kb(lang, "menu:hosts"))


# ================================================================== ترمینال زنده
async def launch_terminal(bot, chat_id: int, mid: int, uid: int, lang: str, host: dict, conn) -> None:
    session = await TerminalSession.open(host, conn)
    registry.active_sessions[chat_id] = session
    ok = await ui.edit(bot, chat_id, mid, _compose(session, host["label"], lang), K.terminal_kb(lang))
    if not ok:
        msg = await ui.send(bot, chat_id, _compose(session, host["label"], lang), K.terminal_kb(lang))
        mid = msg.message_id if msg else mid
    registry.live_message[chat_id] = mid
    registry.render_tasks[chat_id] = asyncio.create_task(_render_loop(bot, chat_id, host["label"], lang))


async def _render_loop(bot, chat_id: int, label: str, lang: str) -> None:
    """هر ~۱ ثانیه اگر خروجی جدیدی آمده باشد پیام زنده را ادیت می‌کند."""
    last_text = None
    try:
        while True:
            await asyncio.sleep(refresh_interval())
            session = registry.active_sessions.get(chat_id)
            if session is None:
                return
            if session.is_closed:
                await close_with_notice(bot, chat_id, lang, "session_closed")
                return
            if not session.consume_dirty():
                continue
            text = _compose(session, label, lang)
            if text == last_text:
                continue
            msg_id = registry.live_message.get(chat_id)
            if msg_id is None:
                return
            try:
                await bot.edit_message_text(
                    text, chat_id=chat_id, message_id=msg_id, reply_markup=K.terminal_kb(lang), parse_mode=ui.PM
                )
                last_text = text
            except RetryAfter as exc:
                session.mark_dirty()
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    last_text = text
                else:
                    logger.warning("terminal edit failed: %s", exc)
            except (TimedOut, NetworkError) as exc:
                logger.warning("network error on terminal edit: %s", exc)
                session.mark_dirty()
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001
        logger.exception("terminal render loop crashed")


async def close_with_notice(bot, chat_id: int, lang: str, key: str) -> None:
    """بستن نشست + آخرین وضعیت صفحه + پیام دلیل بسته شدن."""
    session = registry.active_sessions.get(chat_id)
    msg_id = registry.live_message.get(chat_id)
    if session is not None and msg_id is not None:
        await ui.edit(bot, chat_id, msg_id, _compose(session, session.host["label"], lang), None)
    await registry.close_session(chat_id)
    await ui.send(bot, chat_id, t(key, lang), K.back_kb(lang))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """متن کاربر مستقیم داخل ترمینال تایپ می‌شود (و پیامش پاک می‌شود؛ پسوردهای sudo نمی‌مانند)."""
    chat_id = update.effective_chat.id
    session = registry.active_sessions.get(chat_id)
    if session is None or session.is_closed:
        return False
    text = update.message.text.replace("\r\n", "\n").replace("\n", "\r")
    await session.send_text(text)
    await ui.delete_message(update.message)
    return True


async def cb_control_pad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    chat_id, uid, lang = update.effective_chat.id, update.effective_user.id, ui.lang_of(context)
    session = registry.active_sessions.get(chat_id)
    action = q.data.split(":")[1]
    if session is None or session.is_closed:
        await q.answer(t("no_session", lang), show_alert=True)
        return

    if action == "log":
        await q.answer()
        data = session.full_log().encode("utf-8", "replace") or b" "
        bio = io.BytesIO(data)
        bio.name = f"atronixx_{int(time.time())}.txt"
        try:
            await context.bot.send_document(chat_id, bio, caption=t("log_caption", lang, label=session.host["label"]), parse_mode=ui.PM)
        except TelegramError:
            logger.warning("log export failed", exc_info=True)
        return
    if action == "snip":
        snippets = await db.list_snippets(uid)
        if not snippets:
            await q.answer(t("no_snippets", lang), show_alert=True)
            return
        await q.answer()
        rows = [[K.btn(f"⚡ {s['name']}", f"snip:run:{s['id']}")] for s in snippets[:30]]
        rows.append([K.btn(t("b_close", lang), "snip:close")])
        await ui.send(context.bot, chat_id, t("snip_pick", lang), K.kb(rows))
        return
    await q.answer()
    if action == "ctrlc":
        await session.send_ctrl_c()
    elif action == "ctrld":
        await session.send_ctrl_d()
    elif action == "tab":
        await session.send_tab()
    elif action == "esc":
        await session.send_escape()
    elif action in ("up", "down", "left", "right"):
        await session.send_arrow(action)
    elif action == "clear":
        await session.send_raw(b"\x0c")
    elif action == "exit":
        host_id = session.host["id"]
        await registry.close_session(chat_id)
        await ui.show(
            update, context, t("session_closed", lang),
            K.kb([[K.btn(t("b_host", lang), f"h:view:{host_id}")], [K.btn(t("b_menu", lang), "menu:home")]]),
        )
