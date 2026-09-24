"""core/errors.py - خطاهای مشترک بین ماژول‌ها (برای جلوگیری از import حلقه‌ای)"""

from __future__ import annotations


class SSHConnectError(RuntimeError):
    """خطای اتصال/احراز هویت SSH."""


class TargetBlocked(SSHConnectError):
    """مقصد داخلی/خصوصی یا ممنوع است (محافظت از سرور میزبان)."""


class HostKeyUntrusted(Exception):
    """کلید میزبان هنوز تایید نشده یا تغییر کرده است. اتصال باز باقی می‌ماند."""

    def __init__(self, connection, fingerprint: str, stored: str | None):
        super().__init__("host key not trusted")
        self.connection = connection
        self.fingerprint = fingerprint
        self.stored = stored
