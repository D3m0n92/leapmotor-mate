"""Database queries — misc domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional



def auto_location_type(max_power_kw: float) -> str:
    p = max_power_kw or 0
    if p <= 8:   return "HOME"
    if p <= 22:  return "AC"
    if p <= 80:  return "FAST"
    return "HPC"



# ── Research / BetaTester mode (MateBetaTesterOnly build) ──────────────────────

def add_logbook_note(note: str) -> None:
    """Append a timestamped tester note (e.g. 'engine started to charge while driving')."""
    import time
    note = (note or "").strip()
    if not note:
        return
    db = _conn_rw()
    db.execute("INSERT INTO research_logbook (ts, note) VALUES (?, ?)",
               (int(time.time() * 1000), note[:2000]))
    db.commit()



def get_logbook(limit: int = 200):
    """Recent logbook notes, newest first → [{ts, note}]. Empty if the table isn't there yet."""
    try:
        rows = _get().execute(
            "SELECT ts, note FROM research_logbook ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r["ts"], "note": r["note"]} for r in rows]
    except Exception:  # noqa: BLE001
        return []



def count_raw_signals() -> int:
    """How many raw-signal rows have been captured (shown in the beta UI)."""
    try:
        return _get().execute("SELECT COUNT(*) c FROM raw_signals_log").fetchone()["c"]
    except Exception:  # noqa: BLE001
        return 0



def get_raw_signal_rows():
    """All captured raw-signal rows (ts, sig_key, value), oldest first — for the export."""
    try:
        rows = _get().execute(
            "SELECT ts, sig_key, value FROM raw_signals_log ORDER BY ts ASC").fetchall()
        return [(r["ts"], r["sig_key"], r["value"]) for r in rows]
    except Exception:  # noqa: BLE001
        return []



def get_db_size_bytes() -> int:
    """Total on-disk size of the SQLite DB (main file + WAL/SHM sidecars)."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(DB_PATH + suffix)
        except OSError:
            pass
    return total



def checkpoint() -> None:
    """Flush the WAL into the main DB file so a file copy/download is consistent."""
    c = _conn_rw()
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.commit()
    finally:
        c.close()



# ── Command responsiveness log (car↔cloud reachability proxy) ────────────────
# A remote command is the ONLY moment Mate talks to the car in real time — polls just read
# the cloud's CACHED state, so they succeed even when the car has weak coverage. Logging each
# command's outcome therefore measures how responsive the car itself is (a proxy for the
# cellular coverage where it's parked) — which is exactly what a "cloud OK but car didn't
# confirm" timeout is telling us. This is why one user can see timeouts while everyone else is fine.

def _ensure_command_log(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS command_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER)")



def log_command(action: str, outcome: str, latency_ms: Optional[int] = None) -> None:
    """Record one remote-command outcome (confirmed|timeout_car|cloud_unreachable|rejected).
    Best-effort: never raises into the command path. Keeps ~90 days."""
    try:
        db = _conn_rw()
        _ensure_command_log(db)
        db.execute("INSERT INTO command_log (ts, action, outcome, latency_ms) VALUES (?,?,?,?)",
                   (datetime.now(timezone.utc).isoformat(), action, outcome, latency_ms))
        db.execute("DELETE FROM command_log WHERE ts < ?",
                   ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))
        db.commit()
    except Exception:
        pass



def command_responsiveness(last_n: int = 24, min_samples: int = 3) -> dict:
    """How reliably the car answers commands — a proxy for its cellular coverage. Window is by
    COUNT (the LAST `last_n` commands), NOT by time: it stays visible between command sessions
    and recovers to green within ~last_n good commands (old timeouts scroll out). Only
    'confirmed' vs 'timeout_car' count (a cloud/network or auth failure isn't the car's fault).
    ALWAYS returns a dict so the badge stays visible — state='unknown' until min_samples commands."""
    rows = []
    try:
        db = _conn_rw()
        _ensure_command_log(db)
        rows = db.execute(
            "SELECT outcome, latency_ms FROM command_log "
            "WHERE outcome IN ('confirmed','timeout_car') ORDER BY id DESC LIMIT ?",
            (last_n,)).fetchall()
    except Exception:
        rows = []
    total = len(rows)
    if total < min_samples:
        return {"state": "unknown", "confirmed": 0, "timeouts": 0, "total": total,
                "rate": None, "last_n": last_n, "avg_latency_ms": None}
    confirmed = sum(1 for r in rows if r["outcome"] == "confirmed")
    lat = [r["latency_ms"] for r in rows
           if r["outcome"] == "confirmed" and r["latency_ms"] is not None]
    rate = confirmed / total
    state = ("responsive" if rate >= 0.8 else
             "intermittent" if rate >= 0.4 else "unresponsive")
    return {"state": state, "confirmed": confirmed, "timeouts": total - confirmed,
            "total": total, "rate": round(rate, 2), "last_n": last_n,
            "avg_latency_ms": int(sum(lat) / len(lat)) if lat else None}
