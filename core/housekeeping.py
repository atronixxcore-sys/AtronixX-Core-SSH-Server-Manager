"""
housekeeping.py - کارهای پس‌زمینه (هر ۳۰ ثانیه یک تیک)

- ضربان (heartbeat) برای healthcheck داکر
- اجرای دسترسی: بستن نشست‌های کاربری که بن شده/اشتراکش تمام شده/مود عوض شده
- بیکاری: هشدار در دقیقه‌ی (idle-2) و بستن بعد از idle دقیقه؛ سقف عمر نشست
- بستن اتصال‌های SFTP بیکار (۵ دقیقه)
- هشدار انقضای اشتراک (۳ روز و ۱ روز مانده) و پیام انقضا (فقط مود اشتراکی)
- بکاپ خودکار (هر BACKUP_INTERVAL_HOURS ساعت) برای ادمین
- پاک‌سازی فایل‌های موقت
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from config import settings
from core import registry
from database.db import db
from handlers import gate, terminal, ui
from handlers import keyboards as K
from locales.strings import t

logger = logging.getLogger("atronixx.housekeeping")

TICK = 30
SFTP_IDLE = 300
_task: asyncio.Task | None = None


def _heartbeat() -> None:
    try:
        with open(settings.heartbeat_path, "w") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


async def _lang(uid: int) -> str:
    u = await db.get_user(uid)
    return u["language"] if u else "fa"


async def enforce_access(bot) -> None:
    mode = await db.get_mode()
    for chat_id in list(registry.active_sessions):
        if chat_id == settings.admin_id:
            continue
        u = await db.get_user(chat_id)
        if not u or not gate.has_access(u, mode):
            await terminal.close_with_notice(bot, chat_id, u["language"] if u else "fa", "access_lost")
    for chat_id in list(registry.sftp_state):
        if chat_id == settings.admin_id:
            continue
        u = await db.get_user(chat_id)
        if not u or not gate.has_access(u, mode):
            await registry.close_sftp(chat_id)
            await ui.send(bot, chat_id, t("access_lost", u["language"] if u else "fa"))


async def _idle_check(bot) -> None:
    now = time.monotonic()
    idle_limit = settings.idle_timeout_min * 60
    warn_at = max(60, idle_limit - 120)
    life_limit = settings.max_session_hours * 3600
    for chat_id, s in list(registry.active_sessions.items()):
        lang = await _lang(chat_id)
        idle = now - max(s.last_input, s.last_output)
        if now - s.started_at > life_limit:
            await terminal.close_with_notice(bot, chat_id, lang, "session_max_life")
        elif idle > idle_limit:
            await terminal.close_with_notice(bot, chat_id, lang, "session_idle")
        elif idle > warn_at and not s.idle_warned:
            s.idle_warned = True
            await ui.send(bot, chat_id, t("idle_warning", lang, minutes=str(max(1, round((idle_limit - idle) / 60)))))
    for chat_id, state in list(registry.sftp_state.items()):
        if now - state.get("last", now) > SFTP_IDLE:
            await registry.close_sftp(chat_id)
            await ui.edit(bot, chat_id, state["mid"], t("sftp_expired", await _lang(chat_id)), K.back_kb(await _lang(chat_id)))


async def _subscription_notices(bot) -> None:
    if await db.get_mode() != "paid":
        return
    now = time.time()
    for u in await db.users_with_subscription():
        if not u["started"] or u["is_banned"] or u["id"] == settings.admin_id:
            continue
        left, lang, uid = u["sub_expires_at"] - now, u["language"], u["id"]
        support_kb = K.gate_kb(lang)
        if left <= 0:
            if not u["nexp"]:
                await db.mark_notified(uid, "nexp")
                await ui.send(bot, uid, t("sub_expired_notice", lang, support=settings.support_username), support_kb)
                await ui.send(bot, settings.admin_id, t("adm_sub_expired", "fa", uid=str(uid), name=(u["first_name"] or "—")),
                              K.kb([[K.btn(t("b_grant", "fa"), f"adm:grant:{uid}", K.SUCCESS)]]))
        elif left <= 86400:
            if not u["n1"]:
                await db.mark_notified(uid, "n1")
                await db.mark_notified(uid, "n3")
                await ui.send(bot, uid, t("sub_warn_1", lang, support=settings.support_username), support_kb)
        elif left <= 3 * 86400:
            if not u["n3"]:
                await db.mark_notified(uid, "n3")
                await ui.send(bot, uid, t("sub_warn_3", lang, support=settings.support_username), support_kb)


async def _backup_due(bot) -> None:
    from handlers import admin   # import تنبل برای جلوگیری از وابستگی حلقه‌ای

    now = int(time.time())
    last = await db.get_setting("last_backup")
    if last is None:
        await db.set_setting("last_backup", str(now))   # اولین بکاپ خودکار: ۲۴ ساعت بعد از نصب
        return
    if now - int(last) < settings.backup_interval_hours * 3600:
        return
    attempt = int(await db.get_setting("last_backup_attempt", "0"))
    if now - attempt < 3600:
        return
    await db.set_setting("last_backup_attempt", str(now))
    await admin.send_backup(bot, settings.admin_id, "fa", manual=False)


def _cleanup_tmp() -> None:
    cutoff = time.time() - 3600
    try:
        for name in os.listdir(settings.tmp_dir):
            path = os.path.join(settings.tmp_dir, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except FileNotFoundError:
        pass


async def _loop(bot) -> None:
    tick = 0
    _heartbeat()
    while True:
        try:
            await asyncio.sleep(TICK)
            tick += 1
            _heartbeat()
            await enforce_access(bot)
            await _idle_check(bot)
            if tick % 10 == 0:   # هر ~۵ دقیقه
                await _subscription_notices(bot)
                await _backup_due(bot)
                _cleanup_tmp()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("housekeeping tick failed")


def start(bot) -> None:
    global _task
    os.makedirs(settings.tmp_dir, exist_ok=True)
    _task = asyncio.create_task(_loop(bot))


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
