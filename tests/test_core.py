"""تست منطق داخلی بدون تلگرام/SSH: رمزنگاری، دیتابیس، اشتراک، بکاپ، تاریخ شمسی، امنیت مقصد"""
import asyncio, os, sys, tempfile, time

tmp = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123456789:AAH" + "x" * 32, ADMIN_ID="1000", DATA_DIR=tmp,
                  BACKUP_PASSPHRASE="test-passphrase-123", BLOCKED_IPS="8.8.4.4")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from core.crypto import vault, encrypt_backup, decrypt_backup, CryptoError
from core.jdate import gregorian_to_jalali, fmt_dt
from core.security import resolve_public, valid_address, RateLimiter
from core.errors import TargetBlocked
from database.db import db

vault.load()

def test_crypto():
    tok = vault.encrypt("s3cret", "a")
    assert tok.startswith("v1:") and "s3cret" not in tok
    assert vault.decrypt(tok, "a") == "s3cret"
    try: vault.decrypt(tok, "b"); raise SystemExit("AAD not enforced")
    except CryptoError: pass
    blob = encrypt_backup(b"hello", "pw-12345678")
    assert decrypt_backup(blob, "pw-12345678") == b"hello"
    try: decrypt_backup(blob, "wrong-pass"); raise SystemExit("wrong pw accepted")
    except CryptoError: pass
    bad = bytearray(blob); bad[-1] ^= 1
    try: decrypt_backup(bytes(bad), "pw-12345678"); raise SystemExit("tamper accepted")
    except CryptoError: pass
    assert oct(os.stat(settings.key_path).st_mode & 0o777) == "0o600"
    print("crypto ok")

def test_jdate():
    assert gregorian_to_jalali(2026, 9, 24) == (1405, 7, 2), gregorian_to_jalali(2026, 9, 24)
    assert gregorian_to_jalali(2025, 3, 21) == (1404, 1, 1)
    assert gregorian_to_jalali(2024, 3, 20) == (1403, 1, 1)
    assert fmt_dt(1790000000, "en").count("-") == 2
    print("jdate ok", fmt_dt(time.time(), "fa"))

async def test_security():
    for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.17.0.1", "169.254.169.254", "100.64.0.1", "::1", "8.8.4.4", "::ffff:10.0.0.1"):
        try: await resolve_public(bad); raise SystemExit(f"{bad} not blocked")
        except TargetBlocked: pass
    assert await resolve_public("8.8.8.8") == "8.8.8.8"
    assert valid_address("example.com") and valid_address("1.2.3.4") and not valid_address("bad host") and not valid_address("localhost")
    rl = RateLimiter(2, 60); assert rl.allow(1) and rl.allow(1) and not rl.allow(1) and rl.allow(2)
    print("security ok")

async def test_db():
    await db.open()
    u, created = await db.ensure_user(5, "bob", "Bob"); assert created and u["language"] == "fa"
    u, created = await db.ensure_user(5, "bob", "Bob"); assert not created
    hid = await db.add_host(5, "srv", "1.2.3.4", 22, "root", "password", "pw!", None)
    raw = await db.fetchone("SELECT secret FROM hosts WHERE id=?", (hid,))
    assert "pw!" not in raw["secret"]
    h = await db.get_host_decrypted(hid, 5); assert h["secret"] == "pw!"
    assert await db.get_host(hid, 5) and "secret" not in await db.get_host(hid, 5)
    assert await db.get_host_decrypted(hid, 6) is None  # مالکیت
    await db.update_host(hid, 5, label="new", secret="pw2"); assert (await db.get_host_decrypted(hid, 5))["secret"] == "pw2"
    await db.toggle_favorite(hid, 5); assert (await db.list_hosts(5))[0]["favorite"] == 1
    gid = await db.add_group(5, "prod"); await db.set_host_group(hid, 5, gid)
    assert len(await db.list_hosts(5, 0)) == 0 and len(await db.list_hosts(5, gid)) == 1
    await db.delete_group(gid, 5); assert len(await db.list_hosts(5, 0)) == 1
    # اشتراک
    g = await db.grant(77, 30, 5); assert g["sub_max_hosts"] == 5 and g["notify_pending"] == 1 and not g["started"]
    first = g["sub_expires_at"]; g2 = await db.grant(77, 30, 5)
    assert 29 * 86400 < g2["sub_expires_at"] - first <= 30 * 86400 + 5  # تمدید = اضافه به باقی‌مانده
    st = await db.stats(); assert st["users"] == 2 and st["subs"] == 1
    rows, total = await db.list_users("sub", 0, 10); assert total == 1 and rows[0]["id"] == 77
    assert await db.audience_ids("subs") == []  # 77 هنوز start نزده
    await db.set_banned(5, True); assert (await db.audience_ids("all")) == []
    await db.set_mode("paid"); assert await db.get_mode() == "paid"
    await db.add_channel(-100123, "Ch", "chan", "https://t.me/chan"); assert len(await db.list_channels()) == 1
    # بکاپ/ریستور
    from core.backup import build_backup, restore_backup
    blob = await build_backup()
    await db.delete_user(5); assert await db.get_user(5) is None
    info = await restore_backup(blob, settings.backup_passphrase)
    assert info["users"] == 2 and (await db.get_user(5)) is not None
    assert (await db.get_host_decrypted(hid, 5))["secret"] == "pw2"
    try: await restore_backup(blob, "wrong-wrong-1"); raise SystemExit("restore accepted wrong pw")
    except CryptoError: pass
    await db.delete_user(5)
    await db.close()
    print("db+backup ok")

test_crypto(); test_jdate()
asyncio.run(test_security()); asyncio.run(test_db())
print("ALL CORE TESTS PASSED")
