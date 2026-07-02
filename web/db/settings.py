"""Database queries — settings domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import crypto



# ── Currency ──────────────────────────────────────────────────────────────────
# Monetary amounts are formatted via the Jinja `money` filter using this table.
# Stored setting `currency` holds the ISO 4217 code; default EUR keeps the old
# behaviour. `pos` = symbol placement, `dec` = decimal digits. Names stay in
# English (international convention) so they need no translation.
CURRENCIES = {
    "EUR": {"name": "Euro",            "symbol": "€",   "pos": "after",  "dec": 2},
    "USD": {"name": "US Dollar",       "symbol": "$",   "pos": "before", "dec": 2},
    "GBP": {"name": "British Pound",   "symbol": "£",   "pos": "before", "dec": 2},
    "CHF": {"name": "Swiss Franc",     "symbol": "CHF", "pos": "before", "dec": 2},
    "SEK": {"name": "Swedish Krona",   "symbol": "kr",  "pos": "after",  "dec": 2},
    "NOK": {"name": "Norwegian Krone", "symbol": "kr",  "pos": "after",  "dec": 2},
    "DKK": {"name": "Danish Krone",    "symbol": "kr",  "pos": "after",  "dec": 2},
    "PLN": {"name": "Polish Złoty",    "symbol": "zł",  "pos": "after",  "dec": 2},
    "CZK": {"name": "Czech Koruna",    "symbol": "Kč",  "pos": "after",  "dec": 2},
    "HUF": {"name": "Hungarian Forint","symbol": "Ft",  "pos": "after",  "dec": 0},
    "RON": {"name": "Romanian Leu",    "symbol": "lei", "pos": "after",  "dec": 2},
    "BGN": {"name": "Bulgarian Lev",   "symbol": "лв",  "pos": "after",  "dec": 2},
    "HRK": {"name": "Croatian Kuna",   "symbol": "kn",  "pos": "after",  "dec": 2},
    "TRY": {"name": "Turkish Lira",    "symbol": "₺",   "pos": "before", "dec": 2},
    "CAD": {"name": "Canadian Dollar", "symbol": "$",   "pos": "before", "dec": 2},
    "AUD": {"name": "Australian Dollar","symbol": "$",  "pos": "before", "dec": 2},
    "NZD": {"name": "New Zealand Dollar","symbol": "$", "pos": "before", "dec": 2},
    "JPY": {"name": "Japanese Yen",    "symbol": "¥",   "pos": "before", "dec": 0},
    "CNY": {"name": "Chinese Yuan",    "symbol": "¥",   "pos": "before", "dec": 2},
    "INR": {"name": "Indian Rupee",    "symbol": "₹",   "pos": "before", "dec": 2},
    "BRL": {"name": "Brazilian Real",  "symbol": "R$",  "pos": "before", "dec": 2},
    "MXN": {"name": "Mexican Peso",    "symbol": "$",   "pos": "before", "dec": 2},
    "ZAR": {"name": "South African Rand","symbol": "R", "pos": "before", "dec": 2},
    "RUB": {"name": "Russian Ruble",   "symbol": "₽",   "pos": "after",  "dec": 2},
    "UAH": {"name": "Ukrainian Hryvnia","symbol": "₴",  "pos": "after",  "dec": 2},
    "ILS": {"name": "Israeli Shekel",  "symbol": "₪",   "pos": "before", "dec": 2},
    "KRW": {"name": "South Korean Won","symbol": "₩",   "pos": "before", "dec": 0},
    "SGD": {"name": "Singapore Dollar","symbol": "$",   "pos": "before", "dec": 2},
    "HKD": {"name": "Hong Kong Dollar","symbol": "$",   "pos": "before", "dec": 2},
    "THB": {"name": "Thai Baht",       "symbol": "฿",   "pos": "before", "dec": 2},
    "MYR": {"name": "Malaysian Ringgit","symbol": "RM", "pos": "before", "dec": 2},
}

_DEFAULT_CURRENCY = "EUR"

_READY_PRESETS    = {"cool", "heat", "vent", "defrost", "none"}
_READY_SEAT_MODES = {"off", "heat", "vent"}



def get_setting(key: str, default: str = "") -> str:
    db = _get()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default



def set_setting(key: str, value: str) -> None:
    db = _conn_rw()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    db.commit()



def get_secret(key: str, default: str = "") -> str:
    """Read a secret setting, decrypting transparently (plaintext passes through)."""
    return crypto.decrypt(get_setting(key, default))



def set_secret(key: str, value: str) -> None:
    """Write a secret setting encrypted at rest (matches the poller's crypto/key)."""
    set_setting(key, crypto.encrypt(value or ""))



def get_or_create_device_id() -> str:
    """One stable device_id for this Mate install, shared by poller and web.
    Must match the poller's value so the whole app is a single Leapmotor device on
    the shared app cert (a random per-login device_id kept evicting other clients).
    INSERT OR IGNORE so poller and web converge on the same value."""
    import uuid
    did = get_setting("mate_device_id")
    if not did:
        db = _conn_rw()
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
            ("mate_device_id", uuid.uuid4().hex),
        )
        db.commit()
        did = get_setting("mate_device_id")
    return did



def is_setup_complete() -> bool:
    return get_setting("setup_complete") == "1"



def get_language() -> str:
    return get_setting("language", "en")



def get_currency_code() -> str:
    code = get_setting("currency", _DEFAULT_CURRENCY)
    return code if code in CURRENCIES else _DEFAULT_CURRENCY



def get_currency() -> dict:
    """Full metadata dict for the configured currency (always valid)."""
    return CURRENCIES[get_currency_code()]



def set_currency(code: str) -> None:
    if code in CURRENCIES:
        set_setting("currency", code)



def get_ready_automation_config() -> dict:
    """Sanitised config for the Prepara Veicolo page's automation section."""
    try:
        raw = json.loads(get_setting("ready_automation", "") or "{}")
        if not isinstance(raw, dict):
            raw = {}
    except (ValueError, TypeError):
        raw = {}
    ac_preset = raw.get("ac_preset")
    if ac_preset not in _READY_PRESETS:
        ac_preset = None
    try:
        ac_temperature = int(float(raw.get("ac_temperature")))
    except (TypeError, ValueError):
        ac_temperature = 22
    windows_pct = raw.get("windows_pct")
    try:
        windows_pct = None if windows_pct is None else max(0, min(int(windows_pct), 100))
    except (TypeError, ValueError):
        windows_pct = None

    def _seat(key):
        v = raw.get(key)
        return v if v in _READY_SEAT_MODES else "off"

    try:
        temp_value = float(raw.get("temp_value"))
    except (TypeError, ValueError):
        temp_value = 25.0
    return {
        "enabled":         bool(raw.get("enabled")),
        "temp_enabled":    bool(raw.get("temp_enabled")),
        "temp_comparator": raw.get("temp_comparator") if raw.get("temp_comparator") in (">", "<") else ">",
        "temp_value":      temp_value,
        "ac_preset":       ac_preset or "off",   # "off" is a real <select> option, ac_preset=None isn't
        "ac_temperature":  ac_temperature,
        "windows_pct":     windows_pct,
        "seat_driver":     _seat("seat_driver"),
        "seat_copilot":    _seat("seat_copilot"),
        "steering":        bool(raw.get("steering")),
        "mirror":          bool(raw.get("mirror")),
    }



def save_ready_automation_config(form) -> None:
    """Parse + sanitise the automation form (Werkzeug/Starlette FormData) and persist it as one
    JSON setting. Mirrors _parse_prepare_form's field names (ac_mode/ac_temperature/seat_driver/
    seat_copilot/steering/mirror — the shared bundle_fields() macro) plus the automation-only
    fields (enabled/temp_*/windows_*)."""
    ac_mode = (form.get("ac_mode") or "off").strip()
    ac_preset = ac_mode if ac_mode in _READY_PRESETS else None
    try:
        ac_temperature = int(float(form.get("ac_temperature") or 22))
    except (TypeError, ValueError):
        ac_temperature = 22
    windows_enabled = (form.get("windows_enabled") or "") in ("1", "on", "true", "True")
    windows_pct = None
    if windows_enabled:
        try:
            windows_pct = max(0, min(int(float(form.get("windows_pct") or 0)), 100))
        except (TypeError, ValueError):
            windows_pct = 0

    def _seat(name):
        v = form.get("seat_" + name) or "off"
        return v if v in _READY_SEAT_MODES else "off"

    try:
        temp_value = float(form.get("temp_value") or 25)
    except (TypeError, ValueError):
        temp_value = 25.0
    cfg = {
        "enabled":         (form.get("ready_enabled") or "") in ("1", "on", "true", "True"),
        "temp_enabled":    (form.get("temp_enabled") or "") in ("1", "on", "true", "True"),
        "temp_comparator": form.get("temp_comparator") if form.get("temp_comparator") in (">", "<") else ">",
        "temp_value":      round(temp_value, 1),
        "ac_preset":       ac_preset,
        "ac_temperature":  ac_temperature,
        "windows_pct":     windows_pct,
        "seat_driver":     _seat("driver"),
        "seat_copilot":    _seat("copilot"),
        "steering":        (form.get("steering") or "") in ("1", "on", "true", "True"),
        "mirror":          (form.get("mirror") or "") in ("1", "on", "true", "True"),
    }
    set_setting("ready_automation", json.dumps(cfg))
