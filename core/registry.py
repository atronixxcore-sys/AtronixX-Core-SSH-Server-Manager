"""registry.py - وضعیت لحظه‌ای (حافظه): نشست‌های ترمینال، SFTP، تایید اثرانگشت، تسک‌ها"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger("atronixx.registry")


@dataclass
class PendingTrust:
    host_id: int
    connection: Any
    fingerprint: str
    host: dict
    action: str = "terminal"
    mid: int | None = None


# chat_id -> ...
active_sessions: dict[int, Any] = {}
live_message: dict[int, int] = {}
render_tasks: dict[int, asyncio.Task] = {}
pending_trust: dict[int, PendingTrust] = {}
sftp_state: dict[int, dict] = {}
connecting: dict[int, asyncio.Task] = {}

# action name -> async fn(bot, chat_id, mid, uid, lang, host, conn)
actions: dict[str, Callable[..., Awaitable[None]]] = {}


async def _close_conn(conn: Any) -> None:
    try:
        conn.close()
        await asyncio.wait_for(conn.wait_closed(), 5)
    except Exception:  # noqa: BLE001
        pass


async def close_session(chat_id: int) -> None:
    session = active_sessions.pop(chat_id, None)
    task = render_tasks.pop(chat_id, None)
    live_message.pop(chat_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    if session is not None:
        try:
            await session.close()
        except Exception:  # noqa: BLE001
            logger.debug("session close error", exc_info=True)


async def discard_pending_trust(chat_id: int) -> None:
    pending = pending_trust.pop(chat_id, None)
    if pending is not None:
        await _close_conn(pending.connection)


async def close_sftp(chat_id: int) -> None:
    state = sftp_state.pop(chat_id, None)
    if state and state.get("browser") is not None:
        try:
            await state["browser"].close()
        except Exception:  # noqa: BLE001
            logger.debug("sftp close error", exc_info=True)


def cancel_connecting(chat_id: int) -> None:
    task = connecting.pop(chat_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()


async def reset_chat(chat_id: int, keep_terminal: bool = True) -> None:
    """بستن هر چیزِ نیمه‌کاره‌ی چت (به‌جز ترمینال زنده در حالت پیش‌فرض)."""
    cancel_connecting(chat_id)
    await discard_pending_trust(chat_id)
    await close_sftp(chat_id)
    if not keep_terminal:
        await close_session(chat_id)


async def close_all() -> None:
    for chat_id in list(active_sessions):
        await close_session(chat_id)
    for chat_id in list(sftp_state):
        await close_sftp(chat_id)
    for chat_id in list(pending_trust):
        await discard_pending_trust(chat_id)
    for chat_id in list(connecting):
        cancel_connecting(chat_id)
