"""tools.py - اسنیپت‌ها، گروه‌بندی سرورها و مانیتورینگ سرور"""

from __future__ import annotations

import asyncio
import logging
import shlex

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from core import registry
from core.jdate import fmt_num
from database.db import db
from handlers import keyboards as K
from handlers import ui
from locales.strings import Raw, t

logger = logging.getLogger("atronixx.tools")

MAX_SNIPPETS = 50
MAX_GROUPS = 30


# ================================================================== اسنیپت‌ها
async def cb_snip_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    snippets = await db.list_snippets(uid)
    rows = [[K.btn(f"⚡ {s['name']}", f"snip:view:{s['id']}")] for s in snippets]
    rows.append([K.btn(t("b_add_snip", lang), "snip:add", K.SUCCESS), K.btn(t("b_back", lang), "menu:home")])
    text = t("snip_title", lang, count=fmt_num(len(snippets), lang)) if snippets else t("snip_empty", lang)
    await ui.show(update, context, text, K.kb(rows))


async def cb_snip_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    s = await db.get_snippet(int(q.data.split(":")[2]), uid)
    if not s:
        return
    rows = [[K.btn(t("b_delete", lang), f"snip:del:{s['id']}", K.DANGER), K.btn(t("b_back", lang), "snip:list")]]
    await ui.show(update, context, t("snip_view", lang, name=s["name"], cmd=s["command"][:1500]), K.kb(rows))


async def cb_snip_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    lang, sid = ui.lang_of(context), int(q.data.split(":")[2])
    if q.data.startswith("snip:delyes"):
        await db.delete_snippet(sid, update.effective_user.id)
        await cb_snip_list(update, context)
        return
    await ui.show(update, context, t("snip_delete_confirm", lang), K.danger_confirm_kb(lang, f"snip:delyes:{sid}", f"snip:view:{sid}", t("b_delete_yes", lang)))


async def cb_snip_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    if len(await db.list_snippets(uid)) >= MAX_SNIPPETS:
        await ui.show(update, context, t("snip_limit", lang), K.back_kb(lang, "snip:list"))
        return
    msg = await ui.show(update, context, t("snip_ask_name", lang), K.back_kb(lang, "snip:list"))
    context.user_data["flow"] = {"type": "snip_add", "step": "name", "mid": msg.message_id, "data": {}}


async def cb_snip_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اجرای اسنیپت داخل نشست زنده (از پیام انتخاب اسنیپت)."""
    q = update.callback_query
    uid, chat_id, lang = update.effective_user.id, update.effective_chat.id, ui.lang_of(context)
    session = registry.active_sessions.get(chat_id)
    if session is None or session.is_closed:
        await q.answer(t("no_session", lang), show_alert=True)
        return
    s = await db.get_snippet(int(q.data.split(":")[2]), uid)
    if not s:
        await q.answer()
        return
    await session.send_text(s["command"].replace("\r\n", "\n").replace("\n", "\r"))
    await q.answer(t("snip_sent", lang, name=s["name"]))


async def cb_snip_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await ui.delete_message(update.callback_query.message)


# ================================================================== گروه‌ها
async def cb_grp_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    groups = await db.list_groups(uid)
    rows = [[K.btn(f"🗂 {g['name']} ({fmt_num(g['n'], lang)})", f"grp:view:{g['id']}")] for g in groups]
    rows.append([K.btn(t("b_add_group", lang), "grp:add", K.SUCCESS), K.btn(t("b_back", lang), "menu:home")])
    await ui.show(update, context, t("groups_title", lang) if groups else t("groups_empty", lang), K.kb(rows))


async def cb_grp_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    gid = int(q.data.split(":")[2])
    g = await db.get_group(gid, uid)
    if not g:
        return
    n = len(await db.list_hosts(uid, gid))
    rows = [
        [K.btn(t("b_open", lang), f"hl:{gid}:0", K.PRIMARY)],
        [K.btn(t("b_delete", lang), f"grp:del:{gid}", K.DANGER), K.btn(t("b_back", lang), "grp:list")],
    ]
    await ui.show(update, context, t("group_view", lang, name=g["name"], count=fmt_num(n, lang)), K.kb(rows))


async def cb_grp_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    uid, lang, gid = update.effective_user.id, ui.lang_of(context), int(q.data.split(":")[2])
    if q.data.startswith("grp:delyes"):
        await db.delete_group(gid, uid)
        await cb_grp_list(update, context)
        return
    await ui.show(update, context, t("group_delete_confirm", lang), K.danger_confirm_kb(lang, f"grp:delyes:{gid}", f"grp:view:{gid}", t("b_delete_yes", lang)))


async def cb_grp_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid, lang = update.effective_user.id, ui.lang_of(context)
    if len(await db.list_groups(uid)) >= MAX_GROUPS:
        await ui.show(update, context, t("group_limit", lang), K.back_kb(lang, "grp:list"))
        return
    msg = await ui.show(update, context, t("group_ask_name", lang), K.back_kb(lang, "grp:list"))
    context.user_data["flow"] = {"type": "grp_add", "mid": msg.message_id}


async def flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    flow = context.user_data.get("flow")
    if not flow or flow.get("type") not in ("snip_add", "grp_add"):
        return False
    uid, chat_id, lang = update.effective_user.id, update.effective_chat.id, ui.lang_of(context)
    text = (update.message.text or "").strip()
    await ui.delete_message(update.message)
    bot, mid = context.bot, flow["mid"]

    if flow["type"] == "grp_add":
        if not 1 <= len(text) <= 24:
            await ui.edit(bot, chat_id, mid, t("err_group_name", lang) + "\n\n" + t("group_ask_name", lang), K.back_kb(lang, "grp:list"))
            return True
        await db.add_group(uid, text)
        context.user_data.pop("flow", None)
        groups = await db.list_groups(uid)
        rows = [[K.btn(f"🗂 {g['name']} ({fmt_num(g['n'], lang)})", f"grp:view:{g['id']}")] for g in groups]
        rows.append([K.btn(t("b_add_group", lang), "grp:add", K.SUCCESS), K.btn(t("b_back", lang), "menu:home")])
        await ui.edit(bot, chat_id, mid, t("group_created", lang) + "\n\n" + t("groups_title", lang), K.kb(rows))
        return True

    # snip_add
    if flow["step"] == "name":
        if not 1 <= len(text) <= 32:
            await ui.edit(bot, chat_id, mid, t("err_snip_name", lang) + "\n\n" + t("snip_ask_name", lang), K.back_kb(lang, "snip:list"))
            return True
        flow["data"]["name"] = text
        flow["step"] = "cmd"
        await ui.edit(bot, chat_id, mid, t("snip_ask_cmd", lang, name=text), K.back_kb(lang, "snip:list"))
        return True
    if not 1 <= len(text) <= 1000:
        await ui.edit(bot, chat_id, mid, t("err_snip_cmd", lang) + "\n\n" + t("snip_ask_cmd", lang, name=flow["data"]["name"]), K.back_kb(lang, "snip:list"))
        return True
    await db.add_snippet(uid, flow["data"]["name"], text)
    context.user_data.pop("flow", None)
    snippets = await db.list_snippets(uid)
    rows = [[K.btn(f"⚡ {s['name']}", f"snip:view:{s['id']}")] for s in snippets]
    rows.append([K.btn(t("b_add_snip", lang), "snip:add", K.SUCCESS), K.btn(t("b_back", lang), "menu:home")])
    await ui.edit(bot, chat_id, mid, t("snip_saved", lang) + "\n\n" + t("snip_title", lang, count=fmt_num(len(snippets), lang)), K.kb(rows))
    return True


# ================================================================== مانیتورینگ
_MON_SCRIPT = (
    "echo \"$(nproc 2>/dev/null)|$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)|"
    "$(free -m 2>/dev/null | awk '/^Mem:/{print $2\" \"$3}')|"
    "$(df -P / 2>/dev/null | awk 'NR==2{print $2\" \"$3}')|"
    "$(uptime -p 2>/dev/null)|$( (. /etc/os-release 2>/dev/null; echo \"$PRETTY_NAME\") )|$(hostname 2>/dev/null)\""
)
_MON_CMD = "sh -c " + shlex.quote(_MON_SCRIPT)


def _bar(pct: float) -> str:
    filled = max(0, min(10, round(pct / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def _ints(text: str, n: int) -> list[int] | None:
    parts = text.split()
    if len(parts) < n:
        return None
    try:
        return [int(x) for x in parts[:n]]
    except ValueError:
        return None


def format_monitor(out: str, lang: str) -> str:
    lines = (out or "").strip().splitlines()
    p = (lines[-1] if lines else "").split("|")
    p += [""] * (7 - len(p))
    cores, load, mem, disk, uptime, osname, hostname = (x.strip() for x in p[:7])
    rows: list[str] = []
    if osname or hostname:
        rows.append(f"🐧 <b>{t('mon_os', lang)}:</b> {ui.esc(osname or '—')}" + (f" · <code>{ui.esc(hostname)}</code>" if hostname else ""))
    m = _ints(mem, 2)
    if m and m[0] > 0:
        pct = m[1] * 100 / m[0]
        rows.append(f"🧠 <b>RAM</b>  {_bar(pct)} {fmt_num(round(pct), lang)}%\n      <code>{m[1]} / {m[0]} MB</code>")
    d = _ints(disk, 2)
    if d and d[0] > 0:
        pct = d[1] * 100 / d[0]
        rows.append(f"💽 <b>Disk</b> {_bar(pct)} {fmt_num(round(pct), lang)}%\n      <code>{d[1] / 1048576:.1f} / {d[0] / 1048576:.1f} GB</code>")
    if load:
        rows.append(f"⚙️ <b>CPU load:</b> <code>{ui.esc(load)}</code>" + (f" · {t('mon_cores', lang)}: <code>{ui.esc(cores)}</code>" if cores else ""))
    if uptime:
        rows.append(f"⏱ <b>{t('mon_uptime', lang)}:</b> {ui.esc(uptime)}")
    return "\n".join(rows) if rows else t("mon_no_data", lang)


async def launch_monitor(bot, chat_id: int, mid: int, uid: int, lang: str, host: dict, conn) -> None:
    """اکشن ثبت‌شده در registry.actions: یک بار آمار می‌گیرد و اتصال را می‌بندد."""
    try:
        res = await asyncio.wait_for(conn.run(_MON_CMD, check=False), 20)
        body = format_monitor(res.stdout if isinstance(res.stdout, str) else "", lang)
    except asyncio.TimeoutError:
        body = t("mon_timeout", lang)
    finally:
        await registry._close_conn(conn)
    kb = K.kb([[K.btn(t("b_refresh", lang), f"h:mon:{host['id']}", K.PRIMARY)], [K.btn(t("b_back", lang), f"h:view:{host['id']}")]])
    await ui.edit(bot, chat_id, mid, t("mon_view", lang, label=host["label"], body=Raw(body)), kb)
