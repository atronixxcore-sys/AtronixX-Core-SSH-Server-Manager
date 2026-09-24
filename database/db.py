"""
db.py - دیتابیس SQLite (WAL) با یک اتصال سریالایز‌شده (بدون وابستگی aiosqlite)

- پسورد/کلید/passphrase سرورها فقط به‌صورت AES-256-GCM ذخیره می‌شوند (core.crypto).
- عملیات blocking در thread اجرا می‌شود تا حلقه‌ی asyncio بلاک نشود.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from typing import Any, Callable, Optional

from config import settings
from core.crypto import vault

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT, first_name TEXT,
  language TEXT NOT NULL DEFAULT 'fa',
  terms_accepted INTEGER NOT NULL DEFAULT 0,
  is_banned INTEGER NOT NULL DEFAULT 0, ban_reason TEXT,
  created_at INTEGER NOT NULL, last_seen INTEGER,
  sub_expires_at INTEGER, sub_max_hosts INTEGER NOT NULL DEFAULT 0,
  n3 INTEGER NOT NULL DEFAULT 0, n1 INTEGER NOT NULL DEFAULT 0, nexp INTEGER NOT NULL DEFAULT 0,
  notify_pending INTEGER NOT NULL DEFAULT 0,
  request_notified INTEGER NOT NULL DEFAULT 0,
  started INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
  label TEXT NOT NULL, ip TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 22,
  username TEXT NOT NULL, auth_type TEXT NOT NULL,
  secret TEXT NOT NULL, passphrase TEXT, host_fingerprint TEXT,
  group_id INTEGER, favorite INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hosts_owner ON hosts(owner_id);
CREATE TABLE IF NOT EXISTS snippets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, name TEXT NOT NULL, command TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL UNIQUE,
  title TEXT, username TEXT, link TEXT
);
CREATE TABLE IF NOT EXISTS admin_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, action TEXT NOT NULL, target TEXT, detail TEXT
);
"""

_HOST_UPDATABLE = {"label", "ip", "port", "username", "auth_type", "host_fingerprint"}


def _aad(owner: int, field: str) -> str:
    return f"host:{owner}:{field}"


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- زیرساخت
    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return conn

    async def open(self) -> None:
        async with self._lock:
            self._conn = await asyncio.to_thread(self._connect)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    async def _run(self, fn: Callable[[], Any]) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        def f():
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

        return await self._run(f)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        def f():
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

        return await self._run(f)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        def f():
            return self._conn.execute(sql, params).lastrowid

        return await self._run(f)

    async def txn(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        def f():
            c = self._conn
            c.execute("BEGIN")
            try:
                result = fn(c)
                c.execute("COMMIT")
                return result
            except Exception:
                c.execute("ROLLBACK")
                raise

        return await self._run(f)

    # ------------------------------------------------------------- کاربران
    async def get_user(self, uid: int) -> Optional[dict]:
        return await self.fetchone("SELECT * FROM users WHERE id=?", (uid,))

    async def ensure_user(self, uid: int, username: Optional[str], first_name: Optional[str]) -> tuple[dict, bool]:
        """کاربر را می‌سازد/به‌روز می‌کند. خروجی: (ردیف کاربر, تازه ساخته شد؟)"""
        now = int(time.time())
        user = await self.get_user(uid)
        if user is None:
            await self.execute(
                "INSERT INTO users(id,username,first_name,created_at,last_seen,started) VALUES(?,?,?,?,?,1)",
                (uid, username, first_name, now, now),
            )
            return await self.get_user(uid), True
        if (
            user["username"] != username
            or user["first_name"] != first_name
            or not user["started"]
            or now - (user["last_seen"] or 0) > 60
        ):
            await self.execute(
                "UPDATE users SET username=?, first_name=?, started=1, last_seen=? WHERE id=?",
                (username, first_name, now, uid),
            )
            user = await self.get_user(uid)
        return user, False

    async def ensure_placeholder(self, uid: int) -> None:
        """ردیف کاربری که هنوز ربات را استارت نزده (برای بن/اشتراک با آیدی عددی)."""
        if await self.get_user(uid) is None:
            await self.execute("INSERT INTO users(id,created_at,started) VALUES(?,?,0)", (uid, int(time.time())))

    async def set_language(self, uid: int, lang: str) -> None:
        await self.execute("UPDATE users SET language=? WHERE id=?", (lang, uid))

    async def accept_terms(self, uid: int) -> None:
        await self.execute("UPDATE users SET terms_accepted=1 WHERE id=?", (uid,))

    async def set_banned(self, uid: int, banned: bool, reason: Optional[str] = None) -> None:
        await self.execute("UPDATE users SET is_banned=?, ban_reason=? WHERE id=?", (1 if banned else 0, reason, uid))

    async def mark_request_notified(self, uid: int) -> None:
        await self.execute("UPDATE users SET request_notified=1 WHERE id=?", (uid,))

    async def clear_notify_pending(self, uid: int) -> None:
        await self.execute("UPDATE users SET notify_pending=0 WHERE id=?", (uid,))

    async def mark_notified(self, uid: int, col: str) -> None:
        if col not in ("n3", "n1", "nexp"):
            raise ValueError(col)
        await self.execute(f"UPDATE users SET {col}=1 WHERE id=?", (uid,))

    async def delete_user(self, uid: int) -> None:
        def f(c: sqlite3.Connection):
            c.execute("DELETE FROM hosts WHERE owner_id=?", (uid,))
            c.execute("DELETE FROM groups WHERE owner_id=?", (uid,))
            c.execute("DELETE FROM snippets WHERE owner_id=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))

        await self.txn(f)

    # ------------------------------------------------------------- اشتراک
    async def grant(self, uid: int, days: int, max_hosts: int) -> dict:
        """اشتراک می‌دهد یا تمدید می‌کند (روزها به باقی‌مانده اضافه می‌شود)."""
        now = int(time.time())
        user = await self.get_user(uid)
        if user is None:
            await self.execute("INSERT INTO users(id,created_at,started) VALUES(?,?,0)", (uid, now))
            user = await self.get_user(uid)
        base = max(now, user["sub_expires_at"] or 0)
        await self.execute(
            "UPDATE users SET sub_expires_at=?, sub_max_hosts=?, n3=0, n1=0, nexp=0, notify_pending=1 WHERE id=?",
            (base + days * 86400, max_hosts, uid),
        )
        return await self.get_user(uid)

    async def revoke(self, uid: int) -> None:
        await self.execute("UPDATE users SET sub_expires_at=NULL, sub_max_hosts=0 WHERE id=?", (uid,))

    async def users_with_subscription(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM users WHERE sub_expires_at IS NOT NULL")

    # ------------------------------------------------------------- لیست/آمار (ادمین)
    @staticmethod
    def _filter_sql(flt: str) -> str:
        return {
            "all": "1=1",
            "sub": "u.sub_expires_at > :now",
            "exp": "u.sub_expires_at IS NOT NULL AND u.sub_expires_at <= :now",
            "banned": "u.is_banned=1",
        }.get(flt, "1=1")

    async def list_users(self, flt: str, offset: int, limit: int) -> tuple[list[dict], int]:
        now = int(time.time())
        where = self._filter_sql(flt)
        rows = await self.fetchall(
            f"SELECT u.*, (SELECT COUNT(*) FROM hosts h WHERE h.owner_id=u.id) AS host_count "
            f"FROM users u WHERE {where.replace(':now', str(now))} "
            f"ORDER BY u.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n FROM users u WHERE {where.replace(':now', str(now))}"
        )
        return rows, total["n"]

    async def user_with_hosts(self, uid: int) -> Optional[dict]:
        return await self.fetchone(
            "SELECT u.*, (SELECT COUNT(*) FROM hosts h WHERE h.owner_id=u.id) AS host_count FROM users u WHERE u.id=?",
            (uid,),
        )

    async def stats(self) -> dict:
        now = int(time.time())
        r = await self.fetchone(
            "SELECT COUNT(*) AS users,"
            " SUM(CASE WHEN sub_expires_at > ? THEN 1 ELSE 0 END) AS subs,"
            " SUM(is_banned) AS banned FROM users",
            (now,),
        )
        h = await self.fetchone("SELECT COUNT(*) AS n FROM hosts")
        return {"users": r["users"] or 0, "subs": r["subs"] or 0, "banned": r["banned"] or 0, "hosts": h["n"]}

    async def audience_ids(self, audience: str) -> list[int]:
        now = int(time.time())
        if audience == "subs":
            rows = await self.fetchall(
                "SELECT id FROM users WHERE started=1 AND is_banned=0 AND sub_expires_at > ?", (now,)
            )
        else:
            rows = await self.fetchall("SELECT id FROM users WHERE started=1 AND is_banned=0")
        return [r["id"] for r in rows]

    # ------------------------------------------------------------- تنظیمات
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    async def get_mode(self) -> str:
        return "paid" if await self.get_setting("mode", "free") == "paid" else "free"

    async def set_mode(self, mode: str) -> None:
        await self.set_setting("mode", "paid" if mode == "paid" else "free")

    async def get_max_live(self) -> int:
        try:
            return max(1, int(await self.get_setting("max_live", str(settings.default_max_live))))
        except ValueError:
            return settings.default_max_live

    # ------------------------------------------------------------- سرورها
    async def add_host(
        self,
        owner: int,
        label: str,
        ip: str,
        port: int,
        username: str,
        auth_type: str,
        secret: str,
        passphrase: Optional[str] = None,
        group_id: Optional[int] = None,
    ) -> int:
        return await self.execute(
            "INSERT INTO hosts(owner_id,label,ip,port,username,auth_type,secret,passphrase,group_id,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                owner,
                label,
                ip,
                port,
                username,
                auth_type,
                vault.encrypt(secret, _aad(owner, "secret")),
                vault.encrypt(passphrase, _aad(owner, "passphrase")) if passphrase else None,
                group_id,
                int(time.time()),
            ),
        )

    async def get_host(self, host_id: int, owner: int) -> Optional[dict]:
        """اطلاعات سرور بدون اسرار (رمزها برنمی‌گردند)."""
        row = await self.fetchone("SELECT * FROM hosts WHERE id=? AND owner_id=?", (host_id, owner))
        if row:
            row.pop("secret", None)
            row.pop("passphrase", None)
        return row

    async def get_host_decrypted(self, host_id: int, owner: int) -> Optional[dict]:
        row = await self.fetchone("SELECT * FROM hosts WHERE id=? AND owner_id=?", (host_id, owner))
        if not row:
            return None
        row["secret"] = vault.decrypt(row["secret"], _aad(owner, "secret"))
        row["passphrase"] = (
            vault.decrypt(row["passphrase"], _aad(owner, "passphrase")) if row["passphrase"] else None
        )
        return row

    async def list_hosts(self, owner: int, group_id: Optional[int] = None) -> list[dict]:
        """group_id=None → همه، 0 → بدون گروه، عدد → همان گروه. اول علاقه‌مندی‌ها."""
        cols = "id,owner_id,label,ip,port,username,auth_type,host_fingerprint,group_id,favorite,created_at"
        base = f"SELECT {cols} FROM hosts WHERE owner_id=?"
        params: tuple = (owner,)
        if group_id == 0:
            base += " AND group_id IS NULL"
        elif group_id:
            base += " AND group_id=?"
            params += (group_id,)
        return await self.fetchall(base + " ORDER BY favorite DESC, label COLLATE NOCASE", params)

    async def count_hosts(self, owner: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS n FROM hosts WHERE owner_id=?", (owner,))
        return row["n"]

    async def update_host(self, host_id: int, owner: int, **fields: Any) -> None:
        cols, vals = [], []
        for key, value in fields.items():
            if key in ("secret", "passphrase"):
                value = vault.encrypt(value, _aad(owner, key)) if value else None
            elif key not in _HOST_UPDATABLE:
                raise ValueError(key)
            cols.append(f"{key}=?")
            vals.append(value)
        if cols:
            await self.execute(
                f"UPDATE hosts SET {', '.join(cols)} WHERE id=? AND owner_id=?", tuple(vals) + (host_id, owner)
            )

    async def delete_host(self, host_id: int, owner: int) -> None:
        await self.execute("DELETE FROM hosts WHERE id=? AND owner_id=?", (host_id, owner))

    async def set_host_fingerprint(self, host_id: int, fingerprint: str) -> None:
        await self.execute("UPDATE hosts SET host_fingerprint=? WHERE id=?", (fingerprint, host_id))

    async def toggle_favorite(self, host_id: int, owner: int) -> None:
        await self.execute(
            "UPDATE hosts SET favorite=1-favorite WHERE id=? AND owner_id=?", (host_id, owner)
        )

    async def set_host_group(self, host_id: int, owner: int, group_id: Optional[int]) -> None:
        await self.execute(
            "UPDATE hosts SET group_id=? WHERE id=? AND owner_id=?", (group_id or None, host_id, owner)
        )

    # ------------------------------------------------------------- گروه‌ها
    async def list_groups(self, owner: int) -> list[dict]:
        return await self.fetchall(
            "SELECT g.*, (SELECT COUNT(*) FROM hosts h WHERE h.group_id=g.id) AS n "
            "FROM groups g WHERE owner_id=? ORDER BY name COLLATE NOCASE",
            (owner,),
        )

    async def get_group(self, gid: int, owner: int) -> Optional[dict]:
        return await self.fetchone("SELECT * FROM groups WHERE id=? AND owner_id=?", (gid, owner))

    async def add_group(self, owner: int, name: str) -> int:
        return await self.execute("INSERT INTO groups(owner_id,name) VALUES(?,?)", (owner, name))

    async def delete_group(self, gid: int, owner: int) -> None:
        def f(c: sqlite3.Connection):
            c.execute("UPDATE hosts SET group_id=NULL WHERE group_id=? AND owner_id=?", (gid, owner))
            c.execute("DELETE FROM groups WHERE id=? AND owner_id=?", (gid, owner))

        await self.txn(f)

    # ------------------------------------------------------------- اسنیپت‌ها
    async def list_snippets(self, owner: int) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM snippets WHERE owner_id=? ORDER BY name COLLATE NOCASE", (owner,)
        )

    async def get_snippet(self, sid: int, owner: int) -> Optional[dict]:
        return await self.fetchone("SELECT * FROM snippets WHERE id=? AND owner_id=?", (sid, owner))

    async def add_snippet(self, owner: int, name: str, command: str) -> int:
        return await self.execute(
            "INSERT INTO snippets(owner_id,name,command) VALUES(?,?,?)", (owner, name, command)
        )

    async def delete_snippet(self, sid: int, owner: int) -> None:
        await self.execute("DELETE FROM snippets WHERE id=? AND owner_id=?", (sid, owner))

    # ------------------------------------------------------------- کانال‌های جوین اجباری
    async def list_channels(self) -> list[dict]:
        return await self.fetchall("SELECT * FROM channels ORDER BY id")

    async def add_channel(self, chat_id: int, title: str, username: Optional[str], link: Optional[str]) -> None:
        await self.execute(
            "INSERT INTO channels(chat_id,title,username,link) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username, link=excluded.link",
            (chat_id, title, username, link),
        )

    async def remove_channel(self, cid: int) -> None:
        await self.execute("DELETE FROM channels WHERE id=?", (cid,))

    # ------------------------------------------------------------- لاگ ادمین
    async def log(self, action: str, target: Optional[str] = None, detail: Optional[str] = None) -> None:
        await self.execute(
            "INSERT INTO admin_log(ts,action,target,detail) VALUES(?,?,?,?)",
            (int(time.time()), action, target, detail),
        )

    async def recent_logs(self, n: int = 20) -> list[dict]:
        return await self.fetchall("SELECT * FROM admin_log ORDER BY id DESC LIMIT ?", (n,))

    # ------------------------------------------------------------- بکاپ / ریستور
    async def snapshot_bytes(self) -> bytes:
        """نسخه‌ی یکپارچه‌ی دیتابیس (بدون توقف بات) با VACUUM INTO."""
        os.makedirs(settings.tmp_dir, exist_ok=True)
        tmp = os.path.join(settings.tmp_dir, f"snap_{os.getpid()}_{time.time_ns()}.db")

        def f() -> bytes:
            self._conn.execute("VACUUM INTO ?", (tmp,))
            try:
                with open(tmp, "rb") as fh:
                    return fh.read()
            finally:
                os.remove(tmp)

        return await self._run(f)

    async def replace_file(self, data: bytes) -> None:
        """جایگزینی کامل فایل دیتابیس (ریستور) و باز کردن مجدد."""
        tmp = self.path + ".restore"

        def f():
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            with open(tmp, "wb") as fh:
                fh.write(data)
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except FileNotFoundError:
                    pass
            os.replace(tmp, self.path)
            self._conn = self._connect()

        await self._run(f)


db = Database(settings.db_path)
