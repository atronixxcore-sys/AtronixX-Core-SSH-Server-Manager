"""
sftp.py - مرور، دانلود، آپلود، پوشه‌ی جدید و حذف فایل روی سرور (شبیه Termius Files)

- یک اتصال SFTP برای هر چت باز نگه داشته می‌شود (نه اتصال جدید برای هر کلیک) و با بیکاری بسته می‌شود.
- محدودیت‌های Bot API استاندارد: آپلود تا ۲۰MB و دانلود تا ۵۰MB.
- تعداد انتقال هم‌زمان در کل بات محدود است (سرور کم‌رم).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import posixpath
import re
import time
import uuid

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import settings
from core import registry
from database.db import db
from core.ssh_manager import SFTPBrowser
from handlers import keyboards as K
from handlers import ui
from locales.strings import t

logger = logging.getLogger("atronixx.sftp")

PER_PAGE = 8
MAX_DOWNLOAD = 50 * 1024 * 1024   # سقف ارسال فایل توسط بات
MAX_UPLOAD = 20 * 1024 * 1024     # سقف دریافت فایل توسط بات
MAX_TRANSFERS = 3
_transfer_sem: asyncio.Semaphore | None = None
_BAD_NAME = re.compile(r'[\\/\x00]')


def _sem() -> asyncio.Semaphore:
    global _transfer_sem
    if _transfer_sem is None:
        _transfer_sem = asyncio.Semaphore(MAX_TRANSFERS)
    return _transfer_sem


# ================================================================== رندر
async def _render(bot, chat_id: int, state: dict, lang: str, notice: str | None = None, new_message: bool = False) -> bool:
    """لیست پوشه را می‌خواند و پیام مرورگر را ادیت (یا پایین چت دوباره ارسال) می‌کند."""
    try:
        entries = await state["browser"].listdir(state["path"])
    except Exception as exc:  # noqa: BLE001
        logger.info("sftp listdir failed: %s", exc)
        kb = K.kb([[K.btn(t("b_refresh", lang), "sf:rf"), K.btn(t("b_up", lang), "sf:up")], [K.btn(t("b_exit", lang), "sf:exit", K.DANGER)]])
        await ui.edit(bot, chat_id, state["mid"], t("sftp_error", lang, err=str(exc)[:200]), kb)
        return False
    state["entries"] = entries
    pages = max(1, math.ceil(len(entries) / PER_PAGE))
    state["page"] = max(0, min(state.get("page", 0), pages - 1))
    text = t("sftp_browsing", lang, path=state["path"], count=str(len(entries)))
    if not entries:
        text += "\n" + t("sftp_empty", lang)
    if state["mode"] == "delete":
        text += "\n\n" + t("sftp_mode_delete", lang)
    if notice:
        text += "\n\n" + notice
    kb = K.sftp_kb(lang, entries, state["page"], pages, PER_PAGE, state["path"] == "/", state["mode"])
    if new_message:
        old = state["mid"]
        msg = await ui.send(bot, chat_id, text, kb)
        if msg:
            state["mid"] = msg.message_id
            try:
                await bot.delete_message(chat_id, old)
            except TelegramError:
                pass
        return True
    await ui.edit(bot, chat_id, state["mid"], text, kb)
    return True


async def launch_sftp(bot, chat_id: int, mid: int, uid: int, lang: str, host: dict, conn) -> None:
    """اکشن ثبت‌شده در registry.actions (بعد از اتصال و تایید اثرانگشت)."""
    browser = await SFTPBrowser(conn).start()
    state = {
        "browser": browser, "host_id": host["id"], "label": host["label"], "path": await browser.realpath("."),
        "entries": [], "page": 0, "mode": "browse", "mid": mid, "last": time.monotonic(),
    }
    old = registry.sftp_state.pop(chat_id, None)
    if old and old.get("browser"):
        await old["browser"].close()
    registry.sftp_state[chat_id] = state
    await _render(bot, chat_id, state, lang)


# ================================================================== ناوبری
async def cb_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    chat_id, lang = update.effective_chat.id, ui.lang_of(context)
    state = registry.sftp_state.get(chat_id)
    if state is None:
        await q.answer(t("sftp_expired", lang), show_alert=True)
        await ui.show_main(update, context)
        return
    state["last"] = time.monotonic()
    state["mid"] = q.message.message_id
    parts = q.data.split(":")
    verb = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    bot = context.bot

    if verb == "uph":
        await q.answer(t("sftp_upload_alert", lang), show_alert=True)
        return
    await q.answer()

    if verb == "exit":
        host_id = state["host_id"]
        await registry.close_sftp(chat_id)
        view = await ui.host_view(host_id, update.effective_user.id, lang)
        if view:
            await ui.show(update, context, *view)
        else:
            await ui.show_main(update, context)
    elif verb == "rf":
        context.user_data.pop("flow", None)
        await _render(bot, chat_id, state, lang)
    elif verb == "pg":
        state["page"] = int(arg)
        await _render(bot, chat_id, state, lang)
    elif verb == "up":
        state["path"] = posixpath.dirname(state["path"].rstrip("/")) or "/"
        state["page"] = 0
        await _render(bot, chat_id, state, lang)
    elif verb == "dm":
        state["mode"] = "browse" if state["mode"] == "delete" else "delete"
        await _render(bot, chat_id, state, lang)
    elif verb == "md":
        msg = await ui.show(update, context, t("sftp_mkdir_prompt", lang, path=state["path"]), K.back_kb(lang, "sf:rf"))
        context.user_data["flow"] = {"type": "sftp_mkdir", "mid": msg.message_id}
    elif verb in ("cd", "dl", "del", "delyes"):
        entries = state.get("entries", [])
        idx = int(arg)
        if idx >= len(entries):
            await _render(bot, chat_id, state, lang)
            return
        entry = entries[idx]
        full = posixpath.join(state["path"], entry["name"])
        if verb == "cd":
            old = state["path"]
            state["path"], state["page"] = full, 0
            if not await _render(bot, chat_id, state, lang):
                state["path"] = old
        elif verb == "dl":
            await _start_download(context, chat_id, state, entry, full, lang)
        elif verb == "del":
            kind = "📁" if entry["is_dir"] and not entry.get("is_link") else "📄"
            await ui.show(
                update, context, t("sftp_delete_confirm", lang, kind=kind, name=entry["name"]),
                K.danger_confirm_kb(lang, f"sf:delyes:{idx}", "sf:rf", t("b_delete_yes", lang)),
            )
        else:  # delyes
            try:
                await state["browser"].remove(full, entry["is_dir"] and not entry.get("is_link"))
                notice = t("sftp_deleted", lang, name=entry["name"])
            except Exception as exc:  # noqa: BLE001
                notice = t("sftp_op_failed", lang, err=str(exc)[:150])
            await _render(bot, chat_id, state, lang, notice)


# ================================================================== دانلود
async def _start_download(context, chat_id: int, state: dict, entry: dict, remote: str, lang: str) -> None:
    if entry["is_dir"]:
        return
    if entry["size"] == 0:
        await context.bot.send_message(chat_id, t("sftp_empty_file", lang), parse_mode=ui.PM)
        return
    if entry["size"] > MAX_DOWNLOAD:
        await context.bot.send_message(chat_id, t("sftp_too_big_dl", lang, size=ui.fmt_size(entry["size"])), parse_mode=ui.PM)
        return
    context.application.create_task(_download(context.bot, chat_id, state, entry, remote, lang))


async def _download(bot, chat_id: int, state: dict, entry: dict, remote: str, lang: str) -> None:
    os.makedirs(settings.tmp_dir, exist_ok=True)
    local = os.path.join(settings.tmp_dir, f"{chat_id}_{uuid.uuid4().hex}")
    status = await ui.send(bot, chat_id, t("sftp_downloading", lang, name=entry["name"]))
    try:
        async with _sem():
            await state["browser"].download(remote, local)
            with open(local, "rb") as fh:
                await bot.send_document(chat_id, fh, filename=entry["name"])
    except Exception as exc:  # noqa: BLE001
        await ui.send(bot, chat_id, t("sftp_op_failed", lang, err=str(exc)[:150]))
    finally:
        if os.path.exists(local):
            os.remove(local)
        if status:
            try:
                await status.delete()
            except TelegramError:
                pass
    if registry.sftp_state.get(chat_id) is state:
        await _render(bot, chat_id, state, lang, new_message=True)


# ================================================================== آپلود و ورودی متن
async def on_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """سند ارسالی کاربر در حالت SFTP → آپلود در پوشه‌ی فعلی."""
    chat_id, lang = update.effective_chat.id, ui.lang_of(context)
    state = registry.sftp_state.get(chat_id)
    doc = update.message.document
    if state is None or doc is None:
        return False
    state["last"] = time.monotonic()
    if (doc.file_size or 0) > MAX_UPLOAD:
        await ui.send(context.bot, chat_id, t("sftp_too_big_ul", lang, size=ui.fmt_size(doc.file_size or 0)))
        return True
    name = _BAD_NAME.sub("_", os.path.basename(doc.file_name or f"file_{doc.file_unique_id}")) or "file"
    context.application.create_task(_upload(context.bot, chat_id, state, doc, name, update.message, lang))
    return True


async def _upload(bot, chat_id: int, state: dict, doc, name: str, user_msg, lang: str) -> None:
    os.makedirs(settings.tmp_dir, exist_ok=True)
    local = os.path.join(settings.tmp_dir, f"{chat_id}_{uuid.uuid4().hex}")
    status = await ui.send(bot, chat_id, t("sftp_uploading", lang, name=name))
    notice = None
    try:
        async with _sem():
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(local)
            await state["browser"].upload(local, posixpath.join(state["path"], name))
        notice = t("sftp_upload_done", lang, name=name)
    except Exception as exc:  # noqa: BLE001
        notice = t("sftp_op_failed", lang, err=str(exc)[:150])
    finally:
        if os.path.exists(local):
            os.remove(local)
        for m in (status, user_msg):
            if m:
                try:
                    await m.delete()
                except TelegramError:
                    pass
    if registry.sftp_state.get(chat_id) is state:
        await _render(bot, chat_id, state, lang, notice, new_message=True)


async def flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """نام پوشه‌ی جدید."""
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") != "sftp_mkdir":
        return False
    chat_id, lang = update.effective_chat.id, ui.lang_of(context)
    state = registry.sftp_state.get(chat_id)
    name = (update.message.text or "").strip()
    await ui.delete_message(update.message)
    if state is None:
        context.user_data.pop("flow", None)
        return True
    if not name or name in (".", "..") or _BAD_NAME.search(name) or len(name) > 100:
        await ui.edit(context.bot, chat_id, flow["mid"], t("err_folder_name", lang) + "\n\n" + t("sftp_mkdir_prompt", lang, path=state["path"]), K.back_kb(lang, "sf:rf"))
        return True
    context.user_data.pop("flow", None)
    state["mid"] = flow["mid"]
    try:
        await state["browser"].mkdir(posixpath.join(state["path"], name))
        notice = t("sftp_folder_made", lang, name=name)
    except Exception as exc:  # noqa: BLE001
        notice = t("sftp_op_failed", lang, err=str(exc)[:150])
    await _render(context.bot, chat_id, state, lang, notice)
    return True
