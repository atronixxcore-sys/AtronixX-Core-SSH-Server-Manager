"""main.py - نقطه‌ی ورود AtronixX-Core"""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    BaseUpdateProcessor,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from config import settings
from core import housekeeping, registry
from core.crypto import vault
from database.db import db
from handlers import admin, gate, hosts, router, sftp, start, terminal, tools, ui
from locales.strings import t

logger = logging.getLogger("atronixx")


class OrderedProcessor(BaseUpdateProcessor):
    """آپدیت‌های کاربران مختلف هم‌زمان، ولی آپدیت‌های هر چت به‌ترتیب پردازش می‌شوند
    (ترتیب دستورهایی که داخل ترمینال تایپ می‌شود مهم است)."""

    def __init__(self, max_concurrent: int = 32):
        super().__init__(max_concurrent)
        self._locks: dict[int, asyncio.Lock] = {}
        self._refs: dict[int, int] = {}

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_process_update(self, update, coroutine) -> None:
        chat = getattr(update, "effective_chat", None)
        key = chat.id if chat else 0
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._refs[key] = self._refs.get(key, 0) + 1
        try:
            async with lock:
                await coroutine
        finally:
            self._refs[key] -= 1
            if self._refs[key] <= 0:
                self._refs.pop(key, None)
                self._locks.pop(key, None)


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(TypeHandler(Update, gate.pre_check), group=-1)

    for name, fn in (("start", start.cmd_start), ("menu", start.cmd_start), ("help", start.cmd_help), ("cancel", start.cmd_cancel)):
        app.add_handler(CommandHandler(name, fn, filters=private))

    def cb(fn, pattern: str) -> None:
        app.add_handler(CallbackQueryHandler(fn, pattern=pattern))

    # عمومی
    cb(start.cb_menu_home, r"^menu:home$")
    cb(start.cb_noop, r"^noop$")
    cb(start.cb_lang, r"^lang:toggle$")
    cb(start.cb_terms, r"^terms:accept$")
    cb(start.cb_join_check, r"^join:check$")
    cb(start.cb_account, r"^me:home$")
    cb(start.cb_account_delete, r"^me:del$")
    cb(start.cb_account_delete_yes, r"^me:delyes$")
    # سرورها
    cb(hosts.cb_list, r"^(menu:hosts|hl:\d+:\d+)$")
    cb(hosts.cb_view, r"^h:view:\d+$")
    cb(hosts.cb_fav, r"^h:fav:\d+$")
    cb(hosts.cb_group_pick, r"^h:grp:\d+$")
    cb(hosts.cb_set_group, r"^h:setgrp:\d+:\d+$")
    cb(hosts.cb_test, r"^h:test:\d+$")
    cb(hosts.cb_delete, r"^h:del:\d+$")
    cb(hosts.cb_delete_yes, r"^h:delyes:\d+$")
    cb(hosts.cb_edit, r"^h:edit:\d+$")
    cb(hosts.cb_edit_field, r"^h:ef:\d+:\w+$")
    cb(hosts.cb_add, r"^hosts:add$")
    cb(hosts.cb_wizard, r"^wz:")
    # اتصال / ترمینال / SFTP
    cb(terminal.cb_connect, r"^h:(conn|connf|sftp|mon):\d+$")
    cb(terminal.cb_conn_cancel, r"^conn:cancel$")
    cb(terminal.cb_trust_yes, r"^trust:yes$")
    cb(terminal.cb_trust_no, r"^trust:no$")
    cb(terminal.cb_control_pad, r"^term:")
    cb(sftp.cb_nav, r"^sf:")
    # ابزارها
    cb(tools.cb_snip_list, r"^snip:list$")
    cb(tools.cb_snip_view, r"^snip:view:\d+$")
    cb(tools.cb_snip_del, r"^snip:del(yes)?:\d+$")
    cb(tools.cb_snip_add, r"^snip:add$")
    cb(tools.cb_snip_run, r"^snip:run:\d+$")
    cb(tools.cb_snip_close, r"^snip:close$")
    cb(tools.cb_grp_list, r"^grp:list$")
    cb(tools.cb_grp_view, r"^grp:view:\d+$")
    cb(tools.cb_grp_del, r"^grp:del(yes)?:\d+$")
    cb(tools.cb_grp_add, r"^grp:add$")
    # ادمین
    cb(admin.cb_home, r"^adm:home$")
    cb(admin.cb_stats, r"^adm:st$")
    cb(admin.cb_users, r"^adm:users:(all|sub|exp|banned):\d+$")
    cb(admin.cb_user, r"^adm:u:\d+$")
    cb(admin.cb_ban, r"^adm:(ban|unban):\d+$")
    cb(admin.cb_revoke, r"^adm:revoke(yes)?:\d+$")
    cb(admin.cb_delete_user, r"^adm:del(yes)?:\d+$")
    cb(admin.cb_grant_new, r"^adm:grantnew$")
    cb(admin.cb_grant, r"^adm:grant:\d+$")
    cb(admin.cb_find, r"^adm:find$")
    cb(admin.cb_message_user, r"^adm:msg:\d+$")
    cb(admin.cb_mode, r"^(adm:mode|adm:modeyes:(free|paid))$")
    cb(admin.cb_broadcast, r"^(adm:bc|adm:bcaud:(all|subs))$")
    cb(admin.cb_bc_go, r"^af:bcgo$")
    cb(admin.cb_channels, r"^(adm:ch|adm:chdel:\d+)$")
    cb(admin.cb_channel_add, r"^adm:chadd$")
    cb(admin.cb_backup, r"^(adm:bk|adm:bknow)$")
    cb(admin.cb_restore, r"^adm:rs$")
    cb(admin.cb_restore_go, r"^af:rsgo$")
    cb(admin.cb_sessions, r"^(adm:ss|adm:kill:\d+)$")
    cb(admin.cb_settings, r"^(adm:set|adm:live:-?\d+)$")
    cb(admin.cb_log, r"^adm:log$")
    # پیام‌های آزاد
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, router.on_text))
    app.add_handler(MessageHandler(private & filters.Document.ALL, router.on_document))
    app.add_handler(MessageHandler(private & filters.PHOTO, router.on_photo))
    app.add_error_handler(router.on_error)


async def post_init(app: Application) -> None:
    await db.open()
    registry.actions.update(terminal=terminal.launch_terminal, sftp=sftp.launch_sftp, monitor=tools.launch_monitor)
    en = [BotCommand("start", "Main menu"), BotCommand("cancel", "Cancel current operation"), BotCommand("help", "Help")]
    fa = [BotCommand("start", "منوی اصلی"), BotCommand("cancel", "لغو عملیات فعلی"), BotCommand("help", "راهنما")]
    await app.bot.set_my_commands(en)
    await app.bot.set_my_commands(fa, language_code="fa")
    housekeeping.start(app.bot)
    me = await app.bot.get_me()
    logger.info("AtronixX-Core started as @%s", me.username)
    admin_row = await db.get_user(settings.admin_id)
    lang = admin_row["language"] if admin_row else "fa"
    mode = await db.get_mode()
    await ui.send(app.bot, settings.admin_id, t("boot_notice", lang, mode=t("mode_paid" if mode == "paid" else "mode_free", lang)))


async def post_shutdown(app: Application) -> None:
    await housekeeping.stop()
    await registry.close_all()
    await db.close()


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx در سطح INFO آدرس درخواست‌ها (شامل توکن بات) را لاگ می‌کند؛ ساکتش می‌کنیم
    for noisy in ("httpx", "httpcore", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    vault.load()
    app = (
        Application.builder()
        .token(settings.bot_token)
        .concurrent_updates(OrderedProcessor(32))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    register(app)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
