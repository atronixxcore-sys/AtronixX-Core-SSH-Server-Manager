"""strings.py - ترجمه‌ها (فارسی پیش‌فرض / English) و تابع t()

t(key, lang, **kw): مقدارهای kw به‌صورت خودکار HTML-escape می‌شوند مگر اینکه Raw(...) باشند.
"""

from __future__ import annotations

import html
import logging

from locales._admin import ADMIN
from locales._user import USER

logger = logging.getLogger("atronixx.i18n")

S: dict[str, tuple[str, str]] = {**USER, **ADMIN}


class Raw(str):
    """متن HTML آماده که نباید دوباره escape شود."""


def t(key: str, lang: str = "fa", **kw) -> str:
    entry = S.get(key)
    if entry is None:
        logger.error("missing string key: %s", key)
        return key
    text = entry[0] if lang == "fa" else entry[1]
    if not kw:
        return text
    safe = {k: (v if isinstance(v, Raw) else html.escape(str(v), quote=False)) for k, v in kw.items()}
    try:
        return text.format(**safe)
    except (KeyError, IndexError, ValueError):
        logger.exception("bad placeholders for %s", key)
        return text
