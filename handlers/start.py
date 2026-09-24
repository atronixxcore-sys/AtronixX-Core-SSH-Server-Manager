"""start.py - /start، منوی اصلی، زبان، قوانین، جوین اجباری، حساب من"""

from __future__ import annotations

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from core import registry
from core.jdate import fmt_dt, fmt_num
from core.security import is_admin
from database.db import db
from handlers import gate, ui
from handlers import keyboards as K
from locales.strings import Raw, t


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("flow", None)
    await registry.reset_chat(update.effective_chat.id, keep_terminal=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reset(update, context)
    await ui.show_main(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = ui.lang_of(context)
    await ui.show(update, context, t("help", lang), K.back_kb(lang))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """همیشه کاربر را از هر حالت گیرکرده نجات می‌دهد (فلو، اتصال در حال انجام، SFTP)."""
    await _reset(update, context)
    lang = ui.lang_of(context)
    await context.bot.send_message(update.effective_chat.id, t("cancelled", lang), parse_mode=ui.PM)
    await ui.show_main(update, context)


async def cb_menu_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _reset(update, context)
    await ui.show_main(update, context)


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    user = await db.get_user(uid)
    await db.set_language(uid, "en" if user["language"] == "fa" else "fa")
    await gate.refresh(update, context)


async def cb_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await db.accept_terms(update.effective_user.id)
    await gate.refresh(update, context)


async def cb_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    gate.forget_membership(uid)
    user = await db.get_user(uid)
    reason, _ = await gate.evaluate(context.bot, user)
    if reason == "join":
        await update.callback_query.answer(t("join_still", user["language"]), show_alert=True)
        return
    await update.callback_query.answer()
    await gate.refresh(update, context)


# ------------------------------------------------------------------ حساب من
async def cb_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    lang = ui.lang_of(context)
    user = await db.get_user(uid)
    n = await db.count_hosts(uid)
    text = t(
        "account",
        lang,
        uid=str(uid),
        joined=fmt_dt(user["created_at"], lang, with_time=False),
        hosts=fmt_num(n, lang),
        status=Raw(await ui.status_line(user, lang, n)),
    )
    kb = K.kb([[K.btn(t("b_delete_account", lang), "me:del", K.DANGER)], [K.btn(t("b_back", lang), "menu:home")]])
    await ui.show(update, context, text, kb)


async def cb_account_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = ui.lang_of(context)
    if is_admin(update.effective_user.id):
        await update.callback_query.answer(t("admin_cannot_delete", lang), show_alert=True)
        return
    await update.callback_query.answer()
    await ui.show(update, context, t("account_delete_confirm", lang), K.danger_confirm_kb(lang, "me:delyes", "me:home", t("b_delete_yes", lang)))


async def cb_account_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    uid = update.effective_user.id
    lang = ui.lang_of(context)
    if is_admin(uid):
        return
    user = await db.get_user(uid)
    await registry.reset_chat(uid, keep_terminal=False)
    banned = user["is_banned"]
    ban_reason = user["ban_reason"]
    await db.delete_user(uid)
    if banned:  # حذف داده نباید بن را دور بزند
        await db.ensure_user(uid, update.effective_user.username, update.effective_user.full_name)
        await db.set_banned(uid, True, ban_reason)
    await ui.show(update, context, t("account_deleted", lang), None)
