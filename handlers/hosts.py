"""hosts.py - مدیریت سرورها: لیست، جزئیات، افزودن (ویزارد مرحله‌ای)، ویرایش، حذف، علاقه‌مندی، گروه"""

from __future__ import annotations

import logging
import math
import re

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import settings
from core.errors import SSHConnectError, TargetBlocked
from core.security import connect_limiter, is_admin, resolve_public, valid_address
from core.ssh_manager import key_needs_passphrase, test_connection
from database.db import db
from handlers import keyboards as K
from handlers import ui
from locales.strings import t

logger = logging.getLogger("atronixx.hosts")

PER_PAGE = 8
_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MAX_KEY_BYTES = 16 * 1024
STEP_NO = {"label": 1, "addr": 2, "port": 3, "user": 4, "auth": 5, "secret": 6, "pass": 6, "confirm": 7}
ORDER = ["label", "addr", "port", "user", "auth", "secret", "pass", "confirm"]


# ================================================================== لیست و جزئیات
async def cb_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    if q.data == "menu:hosts":
        gid, page = 0, 0
    else:
        _, g, p = q.data.split(":")
        gid, page = int(g), int(p)
    groups = await db.list_groups(uid) if gid == 0 else []
    hosts = await db.list_hosts(uid, gid)
    pages = max(1, math.ceil(len(hosts) / PER_PAGE))
    page = max(0, min(page, pages - 1))
    chunk = hosts[page * PER_PAGE : (page + 1) * PER_PAGE]
    if gid:
        group = await db.get_group(gid, uid)
        title = t("group_title", lang, name=group["name"] if group else "?", count=str(len(hosts)))
    elif not hosts and not groups:
        title = t("hosts_empty", lang)
    else:
        title = t("hosts_title", lang, count=str(await db.count_hosts(uid)))
    await ui.show(update, context, title, K.hosts_list_kb(lang, groups, chunk, gid, page, pages))


async def cb_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    view = await ui.host_view(int(q.data.split(":")[2]), update.effective_user.id, lang)
    if not view:
        await ui.show(update, context, t("host_missing", lang), K.back_kb(lang, "menu:hosts"))
        return
    await ui.show(update, context, *view)


async def cb_fav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, hid = update.effective_user.id, int(q.data.split(":")[2])
    await db.toggle_favorite(hid, uid)
    view = await ui.host_view(hid, uid, ui.lang_of(context))
    if view:
        await ui.show(update, context, *view)


async def cb_group_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang, hid = update.effective_user.id, ui.lang_of(context), int(q.data.split(":")[2])
    groups = await db.list_groups(uid)
    text = t("group_pick", lang) if groups else t("group_none_yet", lang)
    await ui.show(update, context, text, K.group_pick_kb(lang, hid, groups))


async def cb_set_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    _, _, hid, gid = q.data.split(":")
    if int(gid) == 0 or await db.get_group(int(gid), uid):
        await db.set_host_group(int(hid), uid, int(gid) or None)
    view = await ui.host_view(int(hid), uid, ui.lang_of(context))
    if view:
        await ui.show(update, context, *view)


async def cb_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid, lang, hid = update.effective_user.id, ui.lang_of(context), int(q.data.split(":")[2])
    if not connect_limiter.allow(uid):
        await q.answer(t("rate_limited", lang, sec=str(connect_limiter.retry_after(uid))), show_alert=True)
        return
    await q.answer(t("testing", lang))
    host = await db.get_host_decrypted(hid, uid)
    if not host:
        return
    await ui.show(update, context, t("testing_msg", lang, label=host["label"]), None)
    ok, ms, err = await test_connection(host)
    if ok:
        text = t("test_ok", lang, label=host["label"], ms=str(ms))
    else:
        text = t("test_fail", lang, label=host["label"], err=(err or "")[:300])
    await ui.show(update, context, text, K.back_kb(lang, f"h:view:{hid}"))


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang, hid = ui.lang_of(context), int(q.data.split(":")[2])
    host = await db.get_host(hid, update.effective_user.id)
    if not host:
        return
    await ui.show(
        update, context, t("host_delete_confirm", lang, label=host["label"]),
        K.danger_confirm_kb(lang, f"h:delyes:{hid}", f"h:view:{hid}", t("b_delete_yes", lang)),
    )


async def cb_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    await db.delete_host(int(q.data.split(":")[2]), update.effective_user.id)
    await ui.show(update, context, t("host_deleted", lang), K.back_kb(lang, "menu:hosts"))


# ================================================================== ویرایش
async def cb_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang, hid = ui.lang_of(context), int(q.data.split(":")[2])
    host = await db.get_host(hid, update.effective_user.id)
    if not host:
        return
    await ui.show(update, context, t("edit_pick", lang, label=host["label"]), K.edit_fields_kb(lang, host))


async def cb_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang = ui.lang_of(context)
    _, _, hid, field = q.data.split(":")
    hid = int(hid)
    host = await db.get_host(hid, update.effective_user.id)
    if not host or field not in ("label", "ip", "port", "username", "secret"):
        return
    msg = await ui.show(update, context, t("ef_" + field, lang), K.back_kb(lang, f"h:edit:{hid}"))
    context.user_data["flow"] = {"type": "edit_host", "host_id": hid, "field": field, "mid": msg.message_id}


async def _edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict) -> None:
    msg, uid, lang = update.message, update.effective_user.id, ui.lang_of(context)
    text = (msg.text or "").strip()
    await ui.delete_message(msg)   # پاک کردن پیام کاربر (ممکن است پسورد باشد)
    hid, field = flow["host_id"], flow["field"]
    err = await _validate_field(field, text, lang)
    if err:
        await ui.edit(context.bot, msg.chat_id, flow["mid"], err + "\n\n" + t("ef_" + field, lang), K.back_kb(lang, f"h:edit:{hid}"))
        return
    if field == "port":
        text = int(text)
    fields = {field: text}
    if field in ("ip", "port"):
        fields["host_fingerprint"] = None    # سرور عوض شده؛ دوباره باید تایید شود
    await db.update_host(hid, uid, **fields)
    context.user_data.pop("flow", None)
    view = await ui.host_view(hid, uid, lang)
    if view:
        await ui.edit(context.bot, msg.chat_id, flow["mid"], view[0], view[1])


async def _validate_field(field: str, text: str, lang: str) -> str | None:
    """پیام خطا یا None"""
    if field == "label":
        return None if 1 <= len(text) <= 32 else t("err_label", lang)
    if field == "ip":
        if not valid_address(text):
            return t("err_addr", lang)
        try:
            await resolve_public(text)
        except TargetBlocked:
            return t("err_blocked", lang)
        except SSHConnectError:
            return t("err_dns", lang)
        return None
    if field == "port":
        return None if text.isdigit() and 1 <= int(text) <= 65535 else t("err_port", lang)
    if field == "username":
        return None if _USER_RE.match(text) else t("err_user", lang)
    if field == "secret":
        return None if 1 <= len(text) <= 256 else t("err_secret", lang)
    return t("err_generic", lang)


# ================================================================== ویزارد افزودن
async def _limit_reached(uid: int) -> bool:
    if is_admin(uid) or await db.get_mode() == "free":
        return False
    user = await db.get_user(uid)
    return await db.count_hosts(uid) >= (user["sub_max_hosts"] or 0)


async def cb_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    if await _limit_reached(uid):
        user = await db.get_user(uid)
        await ui.show(update, context, t("limit_reached", lang, max=str(user["sub_max_hosts"]), support=settings.support_username), K.back_kb(lang, "menu:home"))
        return
    msg = q.message
    flow = {"type": "add_host", "step": "label", "data": {}, "mid": msg.message_id, "home": "menu:hosts"}
    context.user_data["flow"] = flow
    await _render(context.bot, msg.chat_id, flow, lang)


def _wiz_text(flow: dict, lang: str, notice: str | None = None) -> str:
    step, d = flow["step"], flow["data"]
    head = t("wz_head", lang, n=str(STEP_NO[step]), total="7")
    lines = []
    if d.get("label"):
        lines.append(f"🏷 <b>{ui.esc(d['label'])}</b>")
    if d.get("ip"):
        lines.append(f"🌐 <code>{ui.esc(d['ip'])}{':' + str(d['port']) if d.get('port') else ''}</code>")
    if d.get("username"):
        lines.append(f"👤 <code>{ui.esc(d['username'])}</code>")
    summary = ("\n".join(lines) + "\n\n") if lines and step != "confirm" else ""
    if step == "secret":
        prompt = t("wz_secret_key" if d.get("auth") == "key" else "wz_secret_pw", lang)
    elif step == "confirm":
        prompt = t(
            "wz_confirm", lang, label=d["label"], addr=f"{d['ip']}:{d['port']}", user=d["username"],
            auth=t("auth_pw" if d["auth"] == "password" else "auth_key", lang),
        )
    else:
        prompt = t("wz_" + step, lang)
    body = f"{head}\n\n{summary}{prompt}"
    if notice:
        body += f"\n\n{notice}"
    return body


async def _render(bot, chat_id: int, flow: dict, lang: str, notice: str | None = None) -> None:
    await ui.edit(bot, chat_id, flow["mid"], _wiz_text(flow, lang, notice), K.wizard_kb(lang, flow["step"], flow["home"]))


def _next(flow: dict) -> str:
    s, d = flow["step"], flow["data"]
    if s == "secret":
        return "pass" if d.get("needs_pass") else "confirm"
    return ORDER[ORDER.index(s) + 1]


def _prev(flow: dict) -> str | None:
    s, d = flow["step"], flow["data"]
    if s == "label":
        return None
    if s == "confirm":
        return "pass" if d.get("needs_pass") else "secret"
    return ORDER[ORDER.index(s) - 1]


async def cb_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    lang, uid = ui.lang_of(context), update.effective_user.id
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") != "add_host":
        await q.answer(t("flow_expired", lang), show_alert=True)
        await ui.show_main(update, context)
        return
    action = q.data.split(":", 1)[1]
    d = flow["data"]
    chat_id = q.message.chat_id
    flow["mid"] = q.message.message_id

    if action == "back":
        await q.answer()
        prev = _prev(flow)
        if prev is None:
            context.user_data.pop("flow", None)
            await ui.show_main(update, context)
            return
        flow["step"] = prev
        await _render(context.bot, chat_id, flow, lang)
    elif action == "port22":
        await q.answer()
        d["port"] = 22
        flow["step"] = "user"
        await _render(context.bot, chat_id, flow, lang)
    elif action == "userroot":
        await q.answer()
        d["username"] = "root"
        flow["step"] = "auth"
        await _render(context.bot, chat_id, flow, lang)
    elif action.startswith("auth:"):
        await q.answer()
        d["auth"] = action.split(":")[1]
        d.pop("secret", None)
        d.pop("needs_pass", None)
        d.pop("passphrase", None)
        flow["step"] = "secret"
        await _render(context.bot, chat_id, flow, lang)
    elif action in ("test", "save") and flow["step"] != "confirm":
        await q.answer()
    elif action == "test":
        if not connect_limiter.allow(uid):
            await q.answer(t("rate_limited", lang, sec=str(connect_limiter.retry_after(uid))), show_alert=True)
            return
        await q.answer(t("testing", lang))
        ok, ms, err = await test_connection(_temp_host(d))
        notice = t("test_ok_short", lang, ms=str(ms)) if ok else t("test_fail_short", lang, err=(err or "")[:200])
        await _render(context.bot, chat_id, flow, lang, notice)
    elif action == "save":
        if await _limit_reached(uid):
            await q.answer(t("limit_short", lang), show_alert=True)
            return
        await q.answer()
        hid = await db.add_host(
            uid, d["label"], d["ip"], d["port"], d["username"], d["auth"], d["secret"], d.get("passphrase")
        )
        context.user_data.pop("flow", None)
        view = await ui.host_view(hid, uid, lang)
        await ui.edit(context.bot, chat_id, flow["mid"], t("host_saved", lang) + "\n\n" + view[0], view[1])
    else:
        await q.answer()


def _temp_host(d: dict) -> dict:
    return {
        "ip": d["ip"], "port": d["port"], "username": d["username"], "auth_type": d["auth"],
        "secret": d["secret"], "passphrase": d.get("passphrase"),
    }


async def _wiz_input(update: Update, context: ContextTypes.DEFAULT_TYPE, flow: dict, text: str) -> None:
    """پردازش ورودی متنی یک مرحله‌ی ویزارد (پیام کاربر قبلاً پاک شده است)."""
    lang, chat_id = ui.lang_of(context), update.effective_chat.id
    step, d = flow["step"], flow["data"]
    err = None
    if step == "label":
        if 1 <= len(text) <= 32:
            d["label"] = text
        else:
            err = t("err_label", lang)
    elif step == "addr":
        err = await _validate_field("ip", text, lang)
        if not err:
            d["ip"] = text
    elif step == "port":
        err = await _validate_field("port", text, lang)
        if not err:
            d["port"] = int(text)
    elif step == "user":
        err = await _validate_field("username", text, lang)
        if not err:
            d["username"] = text
    elif step == "auth":
        err = t("err_use_buttons", lang)
    elif step == "secret":
        if d.get("auth") == "key":
            err = _accept_key(d, text, lang)
        else:
            err = await _validate_field("secret", text, lang)
            if not err:
                d["secret"] = text
    elif step == "pass":
        if 1 <= len(text) <= 256:
            d["passphrase"] = text
        else:
            err = t("err_secret", lang)
    elif step == "confirm":
        err = t("err_use_buttons", lang)
    if err:
        await _render(context.bot, chat_id, flow, lang, err)
        return
    flow["step"] = _next(flow)
    await _render(context.bot, chat_id, flow, lang)


def _accept_key(d: dict, text: str, lang: str) -> str | None:
    if len(text.encode()) > MAX_KEY_BYTES or "PRIVATE KEY" not in text:
        return t("err_key", lang)
    valid, needs = key_needs_passphrase(text)
    if not valid:
        return t("err_key", lang)
    d["secret"] = text.strip() + "\n"
    d["needs_pass"] = needs
    return None


# ================================================================== ورودی‌های آزاد (متن/فایل)
async def flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") not in ("add_host", "edit_host"):
        return False
    if flow["type"] == "edit_host":
        await _edit_text(update, context, flow)
        return True
    msg = update.message
    text = (msg.text or "").strip()
    await ui.delete_message(msg)   # پیام حاوی پسورد/کلید فوراً پاک می‌شود
    await _wiz_input(update, context, flow, text)
    return True


async def flow_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فایل کلید خصوصی در مرحله‌ی secret."""
    flow = context.user_data.get("flow")
    msg = update.message
    if not flow or flow.get("type") != "add_host" or flow["step"] != "secret" or flow["data"].get("auth") != "key":
        return False
    lang = ui.lang_of(context)
    doc = msg.document
    if doc is None:
        return False
    if (doc.file_size or 0) > MAX_KEY_BYTES:
        await ui.delete_message(msg)
        await _render(context.bot, msg.chat_id, flow, lang, t("err_key", lang))
        return True
    try:
        tg_file = await doc.get_file()
        text = bytes(await tg_file.download_as_bytearray()).decode("utf-8")
    except (TelegramError, UnicodeDecodeError):
        text = ""
    await ui.delete_message(msg)
    await _wiz_input(update, context, flow, text.strip())
    return True
