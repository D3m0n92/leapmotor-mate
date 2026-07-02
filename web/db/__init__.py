"""Database sub-package — shared connection helpers.

All db sub-modules import _get(), _conn_rw(), and common utilities from here.
The parent db_reader.py re-exports everything so existing consumers are unaffected.
"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _tz = os.environ.get("TZ")
    _LOCAL_TZ = ZoneInfo(_tz) if _tz else None
except Exception:
    _LOCAL_TZ = None

DB_PATH = os.environ.get("DB_PATH", "leapmotor_mate.db")


def _local_dt(s) -> Optional[datetime]:
    """Parse an ISO-8601 UTC string and convert to local time."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_LOCAL_TZ)
    except Exception:
        return None


def _local_iso(s):
    """Parse an ISO-8601 UTC string and return a local ISO-8601 string."""
    dt = _local_dt(s)
    return dt.isoformat(timespec="seconds") if dt else s


def _conn(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get():
    """Get a read-only connection to the main DB."""
    return _conn(DB_PATH)


def _conn_rw() -> sqlite3.Connection:
    """Open a read-write SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _iso_to_utc(x):
    """Normalize any ISO timestamp to a UTC (+00:00) string so it compares correctly against
    positions.recorded_at (stored in UTC)."""
    if not x:
        return x
    try:
        dt = datetime.fromisoformat(x)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return x


def _billed_kwh(c) -> float:
    """The energy figure SHOWN (and billed) for a charge: the wallbox-measured AC kWh for
    HOME charges that have a wallbox reading, else the battery DC (SoC) energy."""
    ac = c.get("ac_energy_kwh")
    if c.get("location_type") == "HOME" and ac and ac > 0:
        return ac
    return c.get("energy_added_kwh") or 0

