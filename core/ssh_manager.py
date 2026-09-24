"""
ssh_manager.py - موتور اتصال SSH/PTY و SFTP (شبیه‌سازی Termius)

- fingerprint_of(): اثرانگشت SHA-256 کلید میزبان
- open_trusted(): اتصال + تطبیق اثرانگشت ذخیره‌شده (TOFU)؛ اگر تایید نشده باشد HostKeyUntrusted
- TerminalSession: نشست PTY زنده + صفحه‌ی مجازی pyte (خروجی ANSI → متن قابل نمایش در تلگرام)
- SFTPBrowser: مرور/آپلود/دانلود/حذف روی یک اتصال که باز نگه داشته می‌شود

امنیت: قبل از هر اتصال، مقصد resolve و فقط آیپی عمومی پذیرفته می‌شود
(core.security.resolve_public) و اتصال با همان آیپی انجام می‌شود.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import stat
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import asyncssh
import pyte

from core.errors import HostKeyUntrusted, SSHConnectError, TargetBlocked  # noqa: F401  (re-export)
from core.security import resolve_public

logger = logging.getLogger("atronixx.ssh")

TERM_COLS = 56
TERM_ROWS = 26
SCROLLBACK_LINES = 1500   # سبک‌تر برای سرور با رم کم
CONNECT_TIMEOUT = 12


def fingerprint_of(key: asyncssh.SSHKey) -> str:
    """اثرانگشت SHA-256 به فرمت استاندارد (مثل خروجی ssh-keygen -lf)."""
    if hasattr(key, "public_data") and key.public_data:
        der = key.public_data
    else:
        der = key.export_public_key(format_name="der")
    digest = hashlib.sha256(der).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def key_needs_passphrase(text: str) -> tuple[bool, bool]:
    """(کلید معتبر است؟, نیاز به passphrase دارد؟)"""
    try:
        asyncssh.import_private_key(text)
        return True, False
    except asyncssh.KeyImportError as exc:
        if "passphrase" in str(exc).lower():
            return True, True
        return False, False
    except Exception:  # noqa: BLE001
        return False, False


async def _open_connection(host: dict, connect_timeout: int = CONNECT_TIMEOUT) -> asyncssh.SSHClientConnection:
    ip = await resolve_public(host["ip"])  # TargetBlocked / SSHConnectError
    kwargs: dict = dict(
        host=ip,
        port=host["port"],
        username=host["username"],
        known_hosts=None,          # اعتماد به کلید میزبان را خودمان (TOFU) مدیریت می‌کنیم
        connect_timeout=connect_timeout,
        keepalive_interval=30,
        keepalive_count_max=3,
        agent_path=None,
    )
    if host["auth_type"] == "password":
        kwargs["password"] = host["secret"]
        kwargs["client_keys"] = None
    else:
        try:
            key = asyncssh.import_private_key(host["secret"], passphrase=host.get("passphrase") or None)
        except (ValueError, asyncssh.KeyImportError) as exc:
            raise SSHConnectError(f"invalid key: {exc}") from exc
        kwargs["client_keys"] = [key]
        kwargs["password"] = None
    try:
        return await asyncssh.connect(**kwargs)
    except asyncssh.Error as exc:
        raise SSHConnectError(str(exc)) from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise SSHConnectError(f"network: {exc}") from exc


async def open_trusted(host: dict) -> asyncssh.SSHClientConnection:
    """اتصال؛ اگر اثرانگشت ذخیره‌شده با کلید فعلی برابر نبود HostKeyUntrusted می‌دهد
    (اتصال داخل استثنا باز می‌ماند تا بعد از تایید کاربر ادامه پیدا کند)."""
    conn = await _open_connection(host)
    key = conn.get_server_host_key()
    fp = fingerprint_of(key) if key else "unknown"
    stored = host.get("host_fingerprint")
    if stored and stored == fp:
        return conn
    raise HostKeyUntrusted(conn, fp, stored)


async def test_connection(host: dict) -> tuple[bool, float | None, str | None]:
    """تست سریع اتصال: (موفق؟, latency ms, پیام خطا)"""
    start = time.monotonic()
    try:
        conn = await _open_connection(host, connect_timeout=8)
    except SSHConnectError as exc:
        return False, None, str(exc)
    elapsed = (time.monotonic() - start) * 1000
    conn.close()
    try:
        await asyncio.wait_for(conn.wait_closed(), 5)
    except Exception:  # noqa: BLE001
        pass
    return True, round(elapsed, 1), None


# تشخیص ورود/خروج برنامه‌های تمام‌صفحه (htop, nano, vim, less) از روی escape sequence
_ALT_RE = re.compile(rb"\x1b\[\?(?:1049|1047|47)([hl])")
# اگر انتهای یک chunk وسط یک escape sequence قطع شده باشد، تا chunk بعدی نگهش می‌داریم
_PARTIAL_RE = re.compile(rb"\x1b(?:\[(?:\?\d{0,4})?)?$")


class ScrollScreen(pyte.Screen):
    """صفحه‌ی مجازی pyte به‌همراه تاریخچه (Scrollback)."""

    def __init__(self, columns: int, lines: int, history: int = SCROLLBACK_LINES):
        self.scrollback: deque[str] = deque(maxlen=history)
        super().__init__(columns, lines)

    def _row_text(self, row) -> str:
        return "".join(row[x].data for x in range(self.columns)).rstrip()

    def index(self):  # noqa: D401
        top, bottom = self.margins or (0, self.lines - 1)
        if self.cursor.y == bottom:
            self.scrollback.append(self._row_text(self.buffer[top]))
        super().index()

    def erase_in_display(self, how=0, *args, **kwargs):
        if how in (2, 3):
            self.scrollback.clear()
        super().erase_in_display(how, *args, **kwargs)


@dataclass
class TerminalSession:
    host: dict
    connection: asyncssh.SSHClientConnection
    process: asyncssh.SSHClientProcess
    screen: ScrollScreen = field(init=False)
    stream: pyte.ByteStream = field(init=False)
    alt_screen: bool = field(default=False, init=False)
    started_at: float = field(default_factory=time.monotonic, init=False)
    last_input: float = field(default_factory=time.monotonic, init=False)
    last_output: float = field(default_factory=time.monotonic, init=False)
    idle_warned: bool = field(default=False, init=False)
    _carry: bytes = field(default=b"", init=False)
    _reader_task: Optional[asyncio.Task] = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)
    _dirty: bool = field(default=True, init=False)

    def __post_init__(self):
        self.screen = ScrollScreen(TERM_COLS, TERM_ROWS)
        # ByteStream (نه Stream): خروجی SSH بایت خام است و باید UTF-8 دیکد شود
        self.stream = pyte.ByteStream(self.screen)
        self._reader_task = asyncio.create_task(self._read_loop())

    # ------------------------------------------------------------ خواندن خروجی
    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break
                self._feed(chunk)
                self._dirty = True
                self.last_output = time.monotonic()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("PTY read loop ended", exc_info=True)
        finally:
            self._closed = True
            self._dirty = True

    def _feed(self, chunk: bytes) -> None:
        data = self._carry + chunk
        self._carry = b""
        partial = _PARTIAL_RE.search(data)
        if partial:
            self._carry = data[partial.start():]
            data = data[: partial.start()]

        pos = 0
        for match in _ALT_RE.finditer(data):
            self.stream.feed(data[pos : match.start()])
            self._on_alt_screen(entering=(match.group(1) == b"h"))
            pos = match.end()
        self.stream.feed(data[pos:])

    def _on_alt_screen(self, entering: bool) -> None:
        """pyte از alternate screen پشتیبانی نمی‌کند؛ شبیه‌سازی:
        ورود → محتوای فعلی به تاریخچه و صفحه پاک؛ خروج → صفحه پاک و تاریخچه دوباره دیده می‌شود."""
        if entering and not self.alt_screen:
            for line in self._screen_lines():
                self.screen.scrollback.append(line)
            self.screen.reset()
            self.alt_screen = True
        elif not entering and self.alt_screen:
            self.screen.reset()
            self.alt_screen = False

    def _screen_lines(self) -> list[str]:
        """خطوط قابل‌مشاهده‌ی صفحه تا آخرین خط دارای متن یا مکان‌نما."""
        display = [ln.rstrip() for ln in self.screen.display]
        last = self.screen.cursor.y
        for i in range(len(display) - 1, -1, -1):
            if display[i]:
                last = max(last, i)
                break
        return display[: last + 1]

    # ---------------------------------------------------------------- رندر
    def render(self) -> str:
        """رندر فعلی ترمینال به متن ساده (آخرین TERM_ROWS خط)، با مکان‌نمای █"""
        screen = self.screen
        lines = self._screen_lines()
        offset = 0
        if not self.alt_screen:
            need = TERM_ROWS - len(lines)
            if need > 0 and screen.scrollback:
                prefix = list(screen.scrollback)[-need:]
                offset = len(prefix)
                lines = prefix + lines
            idx = offset + screen.cursor.y
            if 0 <= idx < len(lines):
                ln = lines[idx].ljust(screen.cursor.x + 1)
                lines[idx] = ln[: screen.cursor.x] + "█" + ln[screen.cursor.x + 1 :]
        lines = lines[-TERM_ROWS:]
        return "\n".join(lines) if lines else " "

    def full_log(self) -> str:
        """کل تاریخچه + صفحه‌ی فعلی (برای خروجی گرفتن به‌صورت فایل)."""
        return "\n".join(list(self.screen.scrollback) + self._screen_lines())

    def consume_dirty(self) -> bool:
        was_dirty = self._dirty
        self._dirty = False
        return was_dirty

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    @classmethod
    async def open(cls, host: dict, connection: asyncssh.SSHClientConnection) -> "TerminalSession":
        process = await connection.create_process(
            term_type="xterm-256color",
            term_size=(TERM_COLS, TERM_ROWS),
            encoding=None,
        )
        return cls(host=host, connection=connection, process=process)

    # ---------------------------------------------------------------- ورودی
    def _touch(self) -> None:
        self.last_input = time.monotonic()
        self.idle_warned = False

    async def send_text(self, text: str) -> None:
        """متن + Enter. ترمینال‌ها Enter را با CR (\\r) می‌فهمند نه LF."""
        if self._closed:
            return
        self._touch()
        self.process.stdin.write((text + "\r").encode("utf-8"))

    async def send_raw(self, data: bytes) -> None:
        if self._closed:
            return
        self._touch()
        self.process.stdin.write(data)

    async def send_ctrl_c(self):
        await self.send_raw(b"\x03")

    async def send_ctrl_d(self):
        await self.send_raw(b"\x04")

    async def send_ctrl_z(self):
        await self.send_raw(b"\x1a")

    async def send_tab(self):
        await self.send_raw(b"\t")

    async def send_escape(self):
        await self.send_raw(b"\x1b")

    async def send_arrow(self, direction: str):
        codes = {"up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D"}
        await self.send_raw(codes[direction])

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        try:
            self.process.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.connection.close()
            await asyncio.wait_for(self.connection.wait_closed(), 5)
        except Exception:  # noqa: BLE001
            pass


class SFTPBrowser:
    """مرور/آپلود/دانلود/حذف SFTP روی یک اتصال که باز نگه داشته می‌شود."""

    MAX_ENTRIES = 1000

    def __init__(self, conn: asyncssh.SSHClientConnection):
        self._conn = conn
        self._sftp: Optional[asyncssh.SFTPClient] = None

    async def start(self) -> "SFTPBrowser":
        self._sftp = await self._conn.start_sftp_client()
        return self

    async def close(self) -> None:
        try:
            if self._sftp:
                self._sftp.exit()
            self._conn.close()
            await asyncio.wait_for(self._conn.wait_closed(), 5)
        except Exception:  # noqa: BLE001
            pass

    async def realpath(self, path: str) -> str:
        return await self._sftp.realpath(path)

    async def listdir(self, path: str) -> list[dict]:
        entries: list[dict] = []
        for entry in await self._sftp.readdir(path):
            name = entry.filename
            if name in (".", ".."):
                continue
            perm = entry.attrs.permissions or 0
            entries.append(
                {
                    "name": name,
                    "is_dir": stat.S_ISDIR(perm) or stat.S_ISLNK(perm),
                    "is_link": stat.S_ISLNK(perm),
                    "size": entry.attrs.size or 0,
                }
            )
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return entries[: self.MAX_ENTRIES]

    async def download(self, remote_path: str, local_path: str) -> None:
        await self._sftp.get(remote_path, local_path)

    async def upload(self, local_path: str, remote_path: str) -> None:
        await self._sftp.put(local_path, remote_path)

    async def mkdir(self, path: str) -> None:
        await self._sftp.mkdir(path)

    async def remove(self, path: str, is_dir: bool) -> None:
        if is_dir:
            await self._sftp.rmtree(path)
        else:
            await self._sftp.remove(path)
