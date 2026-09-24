"""
crypto.py - رمزنگاری

- فیلدهای حساس (پسورد/کلید SSH): AES-256-GCM با nonce تصادفی و AAD (اتصال داده به کاربر/فیلد).
- کلید اصلی: ۳۲ بایت تصادفی در فایل جدا (secret.key با دسترسی 600)، نه داخل دیتابیس.
- بکاپ: scrypt (N=2^15) برای مشتق‌گیری کلید از رمز + AES-256-GCM.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from config import settings

BACKUP_MAGIC = b"ATXBAK1"


class CryptoError(Exception):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


class Vault:
    def __init__(self, key_path: str):
        self.key_path = key_path
        self._aes: AESGCM | None = None

    def load(self) -> None:
        """کلید را می‌خواند؛ اگر وجود نداشت (نصب اول) می‌سازد."""
        os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
        if not os.path.exists(self.key_path):
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(os.urandom(32))
        with open(self.key_path, "rb") as fh:
            raw = fh.read()
        if len(raw) != 32:
            raise CryptoError("secret.key is corrupted")
        self._aes = AESGCM(
            HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"atronixx-core/fields/v1").derive(raw)
        )

    def raw_key(self) -> bytes:
        with open(self.key_path, "rb") as fh:
            return fh.read()

    def install_raw_key(self, raw: bytes) -> None:
        """جایگزینی کلید اصلی (ریستور بکاپ). اتمیک و با دسترسی 600."""
        if len(raw) != 32:
            raise CryptoError("invalid key size")
        tmp = self.key_path + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, self.key_path)
        self.load()

    def encrypt(self, plaintext: str, aad: str) -> str:
        if self._aes is None:
            raise CryptoError("vault not loaded")
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return "v1:" + _b64e(nonce + ct)

    def decrypt(self, token: str, aad: str) -> str:
        if self._aes is None:
            raise CryptoError("vault not loaded")
        if not token or not token.startswith("v1:"):
            raise CryptoError("unknown token format")
        raw = _b64d(token[3:])
        try:
            return self._aes.decrypt(raw[:12], raw[12:], aad.encode("utf-8")).decode("utf-8")
        except InvalidTag as exc:
            raise CryptoError("decryption failed (wrong key or tampered data)") from exc


vault = Vault(settings.key_path)


def _scrypt_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def encrypt_backup(payload: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_scrypt_key(passphrase, salt)).encrypt(nonce, payload, BACKUP_MAGIC)
    return BACKUP_MAGIC + salt + nonce + ct


def decrypt_backup(blob: bytes, passphrase: str) -> bytes:
    head = len(BACKUP_MAGIC)
    if len(blob) < head + 28 + 16 or not blob.startswith(BACKUP_MAGIC):
        raise CryptoError("not an AtronixX-Core backup file")
    salt, nonce, ct = blob[head : head + 16], blob[head + 16 : head + 28], blob[head + 28 :]
    try:
        return AESGCM(_scrypt_key(passphrase, salt)).decrypt(nonce, ct, BACKUP_MAGIC)
    except InvalidTag as exc:
        raise CryptoError("wrong password or corrupted file") from exc
