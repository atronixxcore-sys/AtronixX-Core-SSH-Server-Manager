"""
backup.py - بکاپ و ریستور رمزگذاری‌شده

فایل بکاپ (.atxbak) = AES-256-GCM( zip{ atronixx.db, secret.key, meta.json } ) با کلیدِ مشتق‌شده از رمز (scrypt).
کلید اصلی داخل بکاپ است تا ریستور روی سرور جدید فقط با «یک فایل + رمز» کار کند.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import time
import zipfile

from config import settings
from core.crypto import CryptoError, decrypt_backup, encrypt_backup, vault
from database.db import db

FORMAT = 1
REQUIRED_TABLES = {"users", "hosts", "groups", "snippets", "settings", "channels"}


async def build_backup(passphrase: str | None = None) -> bytes:
    passphrase = passphrase or settings.backup_passphrase
    snap = await db.snapshot_bytes()
    st = await db.stats()
    meta = {
        "format": FORMAT,
        "created": int(time.time()),
        "users": st["users"],
        "hosts": st["hosts"],
        "brand": settings.brand,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("atronixx.db", snap)
        z.writestr("secret.key", vault.raw_key())
        z.writestr("meta.json", json.dumps(meta))
    return await asyncio.to_thread(encrypt_backup, buf.getvalue(), passphrase)


def _validate_db(data: bytes) -> None:
    os.makedirs(settings.tmp_dir, exist_ok=True)
    path = os.path.join(settings.tmp_dir, f"validate_{time.time_ns()}.db")
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise CryptoError("database in backup is corrupted")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        if not REQUIRED_TABLES <= tables:
            raise CryptoError("backup does not look like an AtronixX-Core database")
    except sqlite3.DatabaseError as exc:
        raise CryptoError("database in backup is invalid") from exc
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass


def _open_payload(blob: bytes, passphrase: str) -> tuple[bytes, bytes, dict]:
    raw = decrypt_backup(blob, passphrase)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            db_bytes = z.read("atronixx.db")
            key = z.read("secret.key")
            meta = json.loads(z.read("meta.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise CryptoError("backup content is corrupted") from exc
    if len(key) != 32:
        raise CryptoError("backup key is invalid")
    _validate_db(db_bytes)
    return db_bytes, key, meta


async def inspect_backup(blob: bytes, passphrase: str) -> dict:
    """فقط بررسی و خواندن اطلاعات (بدون تغییر). CryptoError اگر رمز/فایل اشتباه باشد."""
    _, _, meta = await asyncio.to_thread(_open_payload, blob, passphrase)
    return meta


async def restore_backup(blob: bytes, passphrase: str) -> dict:
    """جایگزینی کامل دیتابیس و کلید. اگر چیزی خراب شد به وضعیت قبلی برمی‌گردد."""
    db_bytes, key, meta = await asyncio.to_thread(_open_payload, blob, passphrase)
    old_db = await db.snapshot_bytes()
    old_key = vault.raw_key()
    try:
        vault.install_raw_key(key)
        await db.replace_file(db_bytes)
    except Exception:
        vault.install_raw_key(old_key)
        await db.replace_file(old_db)
        raise
    return meta
