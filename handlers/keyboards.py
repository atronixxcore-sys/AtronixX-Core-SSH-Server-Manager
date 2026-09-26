"""
keyboards.py - کیبوردهای اینلاین مدرن

از قابلیت رنگی‌کردن دکمه‌ها (Bot API 9.4: style = primary/success/danger) استفاده می‌شود.
در کلاینت‌های قدیمی‌تر بدون رنگ نمایش داده می‌شوند و همه‌چیز عادی کار می‌کند.
"""

from __future__ import annotations

from typing import Iterable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from core.jdate import fmt_num
from locales.strings import t

PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"


def btn(text: str, data: Optional[str] = None, style: Optional[str] = None, url: Optional[str] = None):
    kw = {"url": url} if url else {"callback_data": data}
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kw)
        except TypeError:  # نسخه‌ی قدیمی‌تر کتابخانه
            return InlineKeyboardButton(text, api_kwargs={"style": style}, **kw)
    return InlineKeyboardButton(text, **kw)


def kb(rows: Iterable[list]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def back_kb(lang: str, target: str = "menu:home") -> InlineKeyboardMarkup:
    return kb([[btn(t("b_back", lang), target)]])


def confirm_kb(lang: str, yes: str, no: str, yes_label: Optional[str] = None) -> InlineKeyboardMarkup:
    return kb([[btn(yes_label or t("b_yes", lang), yes, SUCCESS), btn(t("b_cancel", lang), no, DANGER)]])


def danger_confirm_kb(lang: str, yes: str, no: str, yes_label: Optional[str] = None) -> InlineKeyboardMarkup:
    return kb([[btn(yes_label or t("b_yes", lang), yes, DANGER), btn(t("b_cancel", lang), no)]])


# ------------------------------------------------------------------ منوی اصلی
def main_menu_kb(lang: str, admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [btn(t("b_hosts", lang), "menu:hosts", PRIMARY), btn(t("b_add", lang), "hosts:add", SUCCESS)],
        [btn(t("b_snip", lang), "snip:list"), btn(t("b_groups", lang), "grp:list")],
        [btn(t("b_account", lang), "me:home"), btn(t("b_lang", lang), "lang:toggle")],
        [btn(t("b_support", lang), url=settings.support_url)],
    ]
    if admin:
        rows.append([btn(t("b_admin", lang), "adm:home", PRIMARY)])
    return kb(rows)


def gate_kb(lang: str, extra: Optional[list] = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append([btn(t("b_support", lang), url=settings.support_url), btn(t("b_lang", lang), "lang:toggle")])
    return kb(rows)


# ------------------------------------------------------------------ سرورها
def hosts_list_kb(lang: str, groups: list, hosts: list, gid: int, page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list] = []
    if gid == 0 and page == 0:
        gbtns = [btn(f"🗂 {g['name']} ({fmt_num(g['n'], lang)})", f"hl:{g['id']}:0") for g in groups[:12]]
        rows += [gbtns[i : i + 2] for i in range(0, len(gbtns), 2)]
    for h in hosts:
        icon = "⭐" if h["favorite"] else "🖥"
        rows.append([btn(f"{icon} {h['label']}", f"h:view:{h['id']}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"hl:{gid}:{page - 1}"))
        nav.append(btn(f"{fmt_num(page + 1, lang)}/{fmt_num(pages, lang)}", "noop"))
        if page < pages - 1:
            nav.append(btn("▶️", f"hl:{gid}:{page + 1}"))
        rows.append(nav)
    rows.append([btn(t("b_add", lang), "hosts:add", SUCCESS), btn(t("b_back", lang), "menu:home" if gid == 0 else "hl:0:0")])
    return kb(rows)


def host_detail_kb(lang: str, host: dict) -> InlineKeyboardMarkup:
    hid = host["id"]
    fav = t("b_unfav", lang) if host["favorite"] else t("b_fav", lang)
    back = f"hl:{host['group_id'] or 0}:0"
    return kb(
        [
            [btn(t("b_connect", lang), f"h:conn:{hid}", SUCCESS)],
            [btn(t("b_files", lang), f"h:sftp:{hid}", PRIMARY), btn(t("b_monitor", lang), f"h:mon:{hid}", PRIMARY)],
            [btn(t("b_test", lang), f"h:test:{hid}"), btn(fav, f"h:fav:{hid}")],
            [btn(t("b_move", lang), f"h:grp:{hid}"), btn(t("b_edit", lang), f"h:edit:{hid}")],
            [btn(t("b_delete", lang), f"h:del:{hid}", DANGER), btn(t("b_back", lang), back)],
        ]
    )


def edit_fields_kb(lang: str, host: dict) -> InlineKeyboardMarkup:
    hid = host["id"]
    rows = [
        [btn(t("f_label", lang), f"h:ef:{hid}:label"), btn(t("f_addr", lang), f"h:ef:{hid}:ip")],
        [btn(t("f_port", lang), f"h:ef:{hid}:port"), btn(t("f_user", lang), f"h:ef:{hid}:username")],
    ]
    if host["auth_type"] == "password":
        rows.append([btn(t("f_password", lang), f"h:ef:{hid}:secret")])
    rows.append([btn(t("b_back", lang), f"h:view:{hid}")])
    return kb(rows)


def group_pick_kb(lang: str, hid: int, groups: list) -> InlineKeyboardMarkup:
    rows = [[btn(f"🗂 {g['name']}", f"h:setgrp:{hid}:{g['id']}")] for g in groups]
    rows.append([btn(t("b_nogroup", lang), f"h:setgrp:{hid}:0")])
    rows.append([btn(t("b_back", lang), f"h:view:{hid}")])
    return kb(rows)


def trust_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[btn(t("b_trust", lang), "trust:yes", SUCCESS), btn(t("b_cancel", lang), "trust:no", DANGER)]])


def connecting_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[btn(t("b_cancel", lang), "conn:cancel", DANGER)]])


# ------------------------------------------------------------------ ویزارد افزودن سرور
def wizard_kb(lang: str, step: str, home: str) -> InlineKeyboardMarkup:
    rows: list[list] = []
    if step == "port":
        rows.append([btn(t("b_port22", lang), "wz:port22", PRIMARY)])
    elif step == "user":
        rows.append([btn(t("b_user_root", lang), "wz:userroot", PRIMARY)])
    elif step == "auth":
        rows.append([btn(t("b_auth_pw", lang), "wz:auth:password", PRIMARY), btn(t("b_auth_key", lang), "wz:auth:key", PRIMARY)])
    elif step == "confirm":
        rows.append([btn(t("b_save", lang), "wz:save", SUCCESS), btn(t("b_test", lang), "wz:test")])
    rows.append([btn(t("b_back", lang), "wz:back"), btn(t("b_cancel", lang), home, DANGER)])
    return kb(rows)


# ------------------------------------------------------------------ ترمینال
def terminal_kb(lang: str) -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("Ctrl+C", "term:ctrlc", DANGER), btn("Ctrl+D", "term:ctrld"), btn("Tab", "term:tab"), btn("Esc", "term:esc")],
            [btn("◀️", "term:left"), btn("🔼", "term:up"), btn("🔽", "term:down"), btn("▶️", "term:right")],
            [btn(t("b_enter", lang), "term:enter", SUCCESS)],
            [btn(t("b_clear", lang), "term:clear"), btn(t("b_log", lang), "term:log"), btn(t("b_snip", lang), "term:snip")],
            [btn(t("b_exit", lang), "term:exit", DANGER)],
        ]
    )


# ------------------------------------------------------------------ SFTP
def sftp_kb(lang: str, entries: list, page: int, pages: int, per_page: int, at_root: bool, mode: str) -> InlineKeyboardMarkup:
    rows: list[list] = []
    start = page * per_page
    for i, e in enumerate(entries[start : start + per_page], start=start):
        if e["is_dir"]:
            label = f"🗑 📁 {e['name']}" if mode == "delete" else f"📁 {e['name']}"
        else:
            label = f"🗑 📄 {e['name']}" if mode == "delete" else f"📄 {e['name']}"
        rows.append([btn(label[:60], f"sf:{'del' if mode == 'delete' else ('cd' if e['is_dir'] else 'dl')}:{i}", DANGER if mode == "delete" else None)])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"sf:pg:{page - 1}"))
        nav.append(btn(f"{fmt_num(page + 1, lang)}/{fmt_num(pages, lang)}", "noop"))
        if page < pages - 1:
            nav.append(btn("▶️", f"sf:pg:{page + 1}"))
        rows.append(nav)
    tools = []
    if not at_root:
        tools.append(btn(t("b_up", lang), "sf:up", PRIMARY))
    tools += [btn(t("b_refresh", lang), "sf:rf"), btn(t("b_upload", lang), "sf:uph", SUCCESS)]
    rows.append(tools)
    rows.append(
        [
            btn(t("b_mkdir", lang), "sf:md"),
            btn(t("b_delmode_off", lang) if mode == "delete" else t("b_delmode", lang), "sf:dm", DANGER if mode != "delete" else None),
        ]
    )
    rows.append([btn(t("b_exit", lang), "sf:exit", DANGER)])
    return kb(rows)
