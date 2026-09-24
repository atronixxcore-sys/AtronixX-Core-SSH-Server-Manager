"""
security.py - امنیت

- ادمین فقط با آیدی عددی داخل .env
- بلاک مقصدهای داخلی/خصوصی/لوکال/متادیتای ابری و آیپی خود سرور (SSRF) پیش از هر اتصال
- Rate limit اتصال
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from collections import deque

from config import settings
from core.errors import SSHConnectError, TargetBlocked

_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def is_admin(uid: int | None) -> bool:
    return uid is not None and uid == settings.admin_id


def valid_address(text: str) -> bool:
    """آیپی معتبر یا نام دامنه (با حداقل یک نقطه)."""
    text = text.strip()
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return bool(_HOST_RE.match(text)) and "." in text


def _check_ip(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or str(ip) in settings.blocked_ips:
        raise TargetBlocked("blocked target")
    return ip


async def resolve_public(target: str) -> str:
    """مقصد را به آیپی تبدیل و بررسی می‌کند؛ فقط آیپی عمومی مجاز است.
    اتصال حتماً با همین آیپی انجام می‌شود (جلوگیری از DNS rebinding)."""
    try:
        return str(_check_ip(ipaddress.ip_address(target.strip())))
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(target, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SSHConnectError(f"DNS: {exc}") from exc
    addrs = [_check_ip(ipaddress.ip_address(info[4][0].split("%")[0])) for info in infos]
    if not addrs:
        raise SSHConnectError("DNS: no address")
    addrs.sort(key=lambda a: a.version)  # اول IPv4
    return str(addrs[0])


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[int, deque] = {}

    def allow(self, key: int) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def retry_after(self, key: int) -> int:
        q = self._hits.get(key)
        if not q:
            return 0
        return max(1, int(self.window - (time.monotonic() - q[0])))


connect_limiter = RateLimiter(6, 60)   # حداکثر ۶ تلاش اتصال در دقیقه برای هر کاربر
