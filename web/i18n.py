"""LeapMotor Mate — i18n (internationalisation).

Translations live in ``web/locales/{lang}.json``. This module loads them
once at import time and exposes the same API the rest of the codebase
already uses: ``get_t(lang)``, ``fmt_month_year()``, ``fmt_day_month_year()``.
"""

import json
import os
from typing import Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCALES_DIR = os.path.join(_HERE, "locales")

# ── Load translations from JSON files ────────────────────────────────────────

_T: dict[str, dict[str, str]] = {}
_MONTHS: dict[str, dict[str, list[str]]] = {}

for _fname in os.listdir(_LOCALES_DIR):
    if not _fname.endswith(".json"):
        continue
    _lang = _fname[:-5]  # "en.json" → "en"
    with open(os.path.join(_LOCALES_DIR, _fname), encoding="utf-8") as _fh:
        _data = json.load(_fh)
    _T[_lang] = _data.get("translations", {})
    if "months" in _data:
        _MONTHS[_lang] = _data["months"]

# ── Public API (unchanged from the original monolithic version) ──────────────

def get_t(lang: str) -> Callable[[str], str]:
    """Return a translator function ``t(key) -> str`` for *lang*.

    Falls back to English for missing keys, then to the raw key itself.
    """
    strings = _T.get(lang, _T.get("en", {}))
    fallback = _T.get("en", {})

    def t(key: str) -> str:
        return strings.get(key, fallback.get(key, key))

    return t


def fmt_month_year(lang: str, dt) -> str:
    """Localized "%B %Y" → e.g. "Giugno 2026"."""
    months = _MONTHS.get(lang, _MONTHS.get("en", {}))
    return f"{months['full'][dt.month - 1]} {dt.year}"


def fmt_day_month_year(lang: str, dt) -> str:
    """Localized "%d %b %Y" → e.g. "02 giu 2026"."""
    months = _MONTHS.get(lang, _MONTHS.get("en", {}))
    return f"{dt.day:02d} {months['abbr'][dt.month - 1]} {dt.year}"
