"""jdate.py - تاریخ شمسی (بدون وابستگی خارجی) و قالب‌بندی زمان بر اساس زبان"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(text: str) -> str:
    return text.translate(_FA)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def fmt_dt(ts: int | float | None, lang: str, with_time: bool = True) -> str:
    """فارسی: تاریخ شمسی با ارقام فارسی. انگلیسی: میلادی."""
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts, _tz())
    if lang == "fa":
        jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
        s = f"{jy:04d}/{jm:02d}/{jd:02d}"
        if with_time:
            s += f" · {dt:%H:%M}"
        return fa_digits(s)
    return f"{dt:%Y-%m-%d %H:%M}" if with_time else f"{dt:%Y-%m-%d}"


def fmt_num(n: int | str, lang: str) -> str:
    return fa_digits(str(n)) if lang == "fa" else str(n)
