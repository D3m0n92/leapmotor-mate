"""Database queries — vehicle domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import capability_profile
import re
import time



_opt_overrides: dict = {}
_opt_expiry: float = 0.0
_OPT_TTL = 30

# #107: driving-mode tag values Mate accepts (manual — the cloud doesn't expose drive mode).
DRIVE_MODES = ("comfort", "normal", "sport")



def upsert_vehicle(vin: str, car_type: str) -> None:
    """Pre-populate vehicles table from setup wizard (before first poller run)."""
    db = _conn_rw()
    db.execute(
        "INSERT OR IGNORE INTO vehicles (vin, car_type) VALUES (?,?)",
        (vin, car_type),
    )
    db.execute("UPDATE vehicles SET car_type=? WHERE vin=?", (car_type, vin))
    db.commit()



def get_vehicle():
    db = _get()
    v = db.execute("SELECT * FROM vehicles LIMIT 1").fetchone()
    s = {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings").fetchall()}
    return dict(v) if v else None, s



def clear_optimistic_status() -> None:
    """Remove the in-memory optimistic overlay (called when API does not confirm the command)."""
    global _opt_overrides, _opt_expiry
    _opt_overrides = {}
    _opt_expiry = 0.0



def extend_optimistic_status() -> None:
    """Re-arm the optimistic overlay's TTL while a command is still being verified.
    The post-command verification can poll the cloud for up to ~30s waiting for the
    car's state to propagate; without this the overlay would expire mid-wait and the
    UI would briefly flash the stale pre-command state (GitHub #34)."""
    global _opt_expiry
    if _opt_overrides:
        _opt_expiry = time.time() + _OPT_TTL



def write_optimistic_status(overrides: dict) -> None:
    """Copy the latest position row, apply field overrides, insert as new row.
       Also caches overrides in memory so get_latest_status() can re-apply them
       even if the poller overwrites the DB row before the UI refresh fires.
    """
    global _opt_overrides, _opt_expiry
    db = _conn_rw()
    row = db.execute("SELECT * FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return
    d = dict(row)
    d.pop("id")
    d["recorded_at"] = datetime.now(timezone.utc).isoformat()
    d.update(overrides)
    cols = ", ".join(d.keys())
    placeholders = ", ".join("?" for _ in d)
    db.execute(f"INSERT INTO positions ({cols}) VALUES ({placeholders})", list(d.values()))
    db.commit()
    _opt_overrides = dict(overrides)
    _opt_expiry = time.time() + _OPT_TTL



def save_fresh_signals(signals: dict) -> None:
    """Write a fresh position row from raw API signals (called after a command)."""
    db = _conn_rw()
    v = db.execute("SELECT id FROM vehicles LIMIT 1").fetchone()
    if not v:
        return
    vehicle_id = v["id"]

    def sig(key, default=0):  return int(signals.get(key) or default)
    def sigf(key, default=0.0): return float(signals.get(key) or default)

    def _is_charging() -> bool:
        """Charging only happens while PARKED, so the car must be stationary (gear P,
        speed ~0); plus the cable plugged in (1149) AND a real charge current (1178). The
        motion gate is essential: during regen the pack current is strongly negative (same
        sign as charging) and 1149 reads 1 spuriously, so without it driving is mistaken
        for charging. Signal 1939 (AC fan mode) is not used."""
        if int(signals.get("1010") or 0) != 0:   # gear R/N/D → moving
            return False
        try:
            if float(signals.get("1319") or 0) > 2.0:   # speed > 2 km/h → moving
                return False
        except (TypeError, ValueError):
            pass
        if int(signals.get("1149") or 0) == 0:
            return False
        cur = signals.get("1178"); volt = signals.get("1177"); rem = signals.get("1200")
        try:    cur = float(cur) if cur is not None else None
        except (TypeError, ValueError): cur = None
        try:    volt = float(volt) if volt is not None else None
        except (TypeError, ValueError): volt = None
        power = abs(cur * volt) / 1000.0 if (cur is not None and volt is not None and abs(cur) >= 3.0) else None
        if cur is not None:
            if abs(cur) < 3.0:
                return False
            return rem is not None or (power is not None and power >= 1.0)
        if power is not None:
            return power >= 1.0 and rem is not None
        return int(signals.get("1149") or 0) == 2

    gear_map = {0: "P", 1: "R", 2: "N", 3: "D"}
    # Windows: flag OR position % (the T03 reports only the %, the B10 only the flag) — same shared
    # logic as the Vehicle page so the Overview tile / Commands grid agree with it (#62). use_pct is
    # gated by the capability profile, exactly as _parse_vehicle_status does.
    _wvin = (get_vehicle()[0] or {}).get("vin")
    _wstates = capability_profile.window_open_states(
        signals, bool(_wvin) and capability_profile.is_shown(_wvin, "windows_pct"))
    windows_open = int(any(_wstates))
    windows_open_count = sum(1 for w in _wstates if w)

    # Plug from signal 1149 (charge connection status), gated by motion. Signal 47
    # (acInputSlowCharge) latches at 1 for ~5 min after an AC charge on the B10 and does
    # NOT clear on unplug, so it cannot drive session-close; 1149 drops to 0 immediately.
    # 1149 reads 1 spuriously during regen at speed → suppress while moving (mirrors
    # _is_charging). 47 is only a fallback when 1149 is absent. See poller/client._is_plugged_in.
    def _is_plugged() -> bool:
        if int(signals.get("1010") or 0) != 0:          # gear R/N/D → moving
            return False
        try:
            if float(signals.get("1319") or 0) > 2.0:   # speed > 2 km/h → moving
                return False
        except (TypeError, ValueError):
            pass
        conn = signals.get("1149")
        if conn is None:
            return int(signals.get("47") or 0) == 1     # legacy fallback when 1149 absent
        try:
            return int(conn) in (1, 2)
        except (TypeError, ValueError):
            return False
    plug_connected = _is_plugged()

    db.execute(
        """INSERT INTO positions (
            vehicle_id, recorded_at,
            latitude, longitude, speed_kmh, odometer_km,
            soc, range_km, gear, charging,
            battery_min_temp, climate_target_temp, inside_temp,
            is_locked, climate_on, plug_connected,
            climate_cooling, climate_heating, climate_defrost,
            trunk_open, windows_open, sunshade_open,
            remaining_charge_min, charge_voltage_v, charge_current_a, charge_completed, security_active,
            windows_open_count,
            door_driver_open, door_passenger_open, door_rear_left_open, door_rear_right_open,
            window_fl_open, window_rl_open, ac_port_mode,
            fan_level, recirculation, climate_mode
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            vehicle_id,
            datetime.now(timezone.utc).isoformat(),
            sigf("3725") or sigf("2190"),
            sigf("3724") or sigf("2191"),
            sigf("1319"), sigf("1318"),
            sigf("100003") or sigf("1204"),
            sigf("3260"),
            gear_map.get(sig("1010"), "P"),
            int(_is_charging()),
            sigf("1182"), sigf("2183"), sigf("1349"),
            sig("1298"), sig("1938"), int(plug_connected),
            int(sig("2669") == 2), int(sig("2681") == 2), int(sig("1945") == 2),
            sig("1281"), windows_open, sig("1724"),
            sig("1200") or None,
            sigf("1177") or None,
            sigf("1178") or None,
            int(int(signals.get("3736") or 0) != 0),
            int(int(signals.get("1255") or 0) != 0),
            windows_open_count,
            1 if sig("1277") else 0, 1 if sig("1278") else 0,
            1 if sig("1279") else 0, 1 if sig("1280") else 0,
            1 if _wstates[0] else 0, 1 if _wstates[2] else 0,
            int(signals.get("47") or 0),     # ac_port_mode — same as the poller; without it this
                                             # web-side write left NULL, fragmenting V2L sessions (#)
            sig("1941") or None,             # fan_level (1941 acAirVolume 1-7; 0 → NULL = no data)
            int(sig("1943") == 1),           # recirculation (1=recirc/in, 0=fresh/out)
            int(signals["3713"]) if signals.get("3713") is not None else None,  # climate_mode (3713)
        ),
    )
    db.commit()



def get_latest_status() -> Optional[dict]:
    db = _get()
    row = db.execute(
        "SELECT * FROM positions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    # Apply in-memory optimistic overrides if still within TTL
    if time.time() < _opt_expiry and _opt_overrides:
        d.update(_opt_overrides)
    # GPS fallback: a poll can come back with no fix → (0,0). Don't let that blank the Overview
    # map (or reset Navigation's start point) — fall back to the last position that had a real
    # fix and flag it stale, so the last known location keeps showing. Only a true (0,0)/null is
    # treated as "no fix" (a car genuinely on the prime meridian at lon 0 is kept).
    _lat, _lon = d.get("latitude"), d.get("longitude")
    if _lat is None or _lon is None or (abs(_lat) < 1e-6 and abs(_lon) < 1e-6):
        last = db.execute(
            "SELECT latitude, longitude FROM positions "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND NOT (ABS(latitude) < 1e-6 AND ABS(longitude) < 1e-6) "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            d["latitude"], d["longitude"] = last["latitude"], last["longitude"]
            d["position_stale"] = True
    # Charge power: positions stores current/voltage, not a power column. Compute it
    # (|I×V|), only when the charge current is meaningful (>=3A). Signal 49 is NOT a
    # power (it's the left-mirror-heating flag) and must never be used here.
    cur_a = d.get("charge_current_a")
    volt_v = d.get("charge_voltage_v")
    if cur_a is not None and volt_v is not None and abs(cur_a) >= 3.0:
        d["charge_power_kw"] = round(abs(cur_a * volt_v) / 1000.0, 2)
    else:
        d["charge_power_kw"] = 0.0
    # "Ventilating" = the REAL vent mode (signal 3713 climate_mode == 4), gated on A/C being on
    # (modes persist when off). The old derive-by-absence wrongly lit up for plain A/C-on / AUTO
    # (mode 0 = A/C on but not yet cooling) — confirmed on-car 2026-06-21.
    d["climate_venting"] = bool(d.get("climate_on")) and d.get("climate_mode") == 4
    # How long ago
    try:
        ts = datetime.fromisoformat(d["recorded_at"])
        now = datetime.now(timezone.utc)
        delta = int((now - ts).total_seconds())
        if delta < 60:
            d["last_seen"] = f"{delta}s ago"
        elif delta < 3600:
            d["last_seen"] = f"{delta // 60}m ago"
        else:
            d["last_seen"] = f"{delta // 3600}h ago"
    except Exception:
        d["last_seen"] = "unknown"
    # OTA / software-update status (the poller scans the account message inbox for an update notice).
    d["ota"] = get_ota_status()
    return d



def get_ota_status() -> dict:
    """OTA / software-update status the poller stored (from scanning the account inbox). Returns
    {available:bool, title:str|None, time:str|None (localized "dd/mm HH:MM")}. False until the
    poller has run a check; only ever True when an update notice is actually present."""
    from db.settings import get_setting
    available = get_setting("ota_available", "") == "1"
    title = get_setting("ota_title", "") or None
    when = None
    raw = get_setting("ota_time", "")
    if raw:
        try:
            dt = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
            when = (_local_dt(dt.isoformat()) or dt).strftime("%d/%m %H:%M")
        except (TypeError, ValueError, OSError):
            when = None
    return {"available": available, "title": title, "time": when}
