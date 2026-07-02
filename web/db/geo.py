"""Database queries — geo domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import time



# ── V2L (vehicle-to-load) discharge sessions ───────────────────────────────────
# Reconstructed ON-READ from the per-poll `positions` log (ac_port_mode + battery current/voltage)
# — same "pure read, no extra table" approach as get_vampire_drain. A session = a run of samples
# with ac_port_mode==2 (V2L mode active, signal 47). Reported power is NET of the idle baseline
# captured just before the session, so the car's own awake overhead (~300 W) is not attributed to
# the external load. Battery current (charge_current_a / signal 1178) is SIGNED: positive = discharge.


def get_v2l_sessions(lookback_days: int = 90, limit: int = 50, vehicle_id: int | None = None) -> dict:
    db = _get()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    if vehicle_id is not None:   # use idx_positions_vehicle(vehicle_id, recorded_at) → fast range scan
        rows = db.execute(
            "SELECT recorded_at, soc, charge_current_a, charge_voltage_v, ac_port_mode FROM positions "
            "WHERE vehicle_id = ? AND recorded_at >= ? ORDER BY recorded_at", (vehicle_id, cutoff),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT recorded_at, soc, charge_current_a, charge_voltage_v, ac_port_mode FROM positions "
            "WHERE recorded_at >= ? ORDER BY recorded_at", (cutoff,),
        ).fetchall()

    def _close(c, ongoing=False):
        s = c["samples"]
        # Integrate net power over time (left-rectangle per gap). Gaps outside (0, 1h] are skipped so
        # a sleep/offline hole between two V2L samples can never invent energy.
        energy_wh, peak_w = 0.0, 0.0
        for k in range(len(s)):
            peak_w = max(peak_w, s[k][1])
            if k:
                dt_h = (s[k][0] - s[k - 1][0]).total_seconds() / 3600.0
                if 0 < dt_h <= 1.0:
                    energy_wh += s[k - 1][1] * dt_h
        soc_used = round((c["soc0"] or 0) - (c["soc_last"] or 0), 1)
        return {
            "start": c["t0"].isoformat(), "end": c["t_last"].isoformat(),
            "duration_min": round((c["t_last"] - c["t0"]).total_seconds() / 60.0, 1),
            "energy_wh": round(energy_wh, 1),
            "peak_w": round(peak_w),
            "current_w": round(s[-1][1]) if s else 0,    # latest sample's net power (instantaneous)
            "baseline_w": round(c["i0"] * (c["v_ref"] or 0.0)),
            "soc_used_pct": soc_used if soc_used > 0 else 0.0,
            "ongoing": ongoing,
        }

    sessions, cur, baseline_a = [], None, 0.0   # baseline_a = last non-V2L (awake idle) discharge current
    for r in rows:
        mode = r["ac_port_mode"]
        if mode is None:
            continue   # web-side live writes can leave ac_port_mode NULL — skip so they neither SPLIT a
                       # session (NULL != 2 would close it) NOR corrupt baseline_a with their own current
        i = float(r["charge_current_a"] or 0.0)
        v = float(r["charge_voltage_v"] or 0.0)
        if mode != 2:                            # not in V2L → close any open session, refresh baseline
            if cur is not None:
                sessions.append(_close(cur)); cur = None
            if i > 0:                            # positive = discharge → the awake idle overhead (I0)
                baseline_a = i
            continue
        t = _local_dt(r["recorded_at"])
        if t is None:
            continue
        if cur is None:                          # V2L just started → open a session, freeze its baseline
            cur = {"t0": t, "t_last": t, "i0": max(0.0, baseline_a), "v_ref": v,
                   "soc0": r["soc"], "soc_last": r["soc"], "samples": []}
        cur["samples"].append((t, max(0.0, i - cur["i0"]) * v))    # NET power, clamped at 0
        cur["t_last"], cur["soc_last"] = t, r["soc"]

    if cur is not None:
        sessions.append(_close(cur, ongoing=True))

    sessions = sessions[-limit:]
    return {"sessions": sessions, "count": len(sessions),
            "total_energy_wh": round(sum(s["energy_wh"] for s in sessions), 1),
            "lookback_days": lookback_days}



def get_v2l_status(lookback_days: int = 7) -> dict:
    """Compact V2L summary for the Overview card — ALWAYS shown (we don't gate on model; the data
    decides). Idle until a V2L session appears, then live net power. `ever_used` separates
    idle-with-history from never-used; `power_max_w` (3500 W) scales the UI bar. Vehicle-scoped + a
    short lookback so the Overview's 10 s htmx auto-refresh stays a cheap indexed range scan."""
    from db.vehicle import get_vehicle
    try:
        veh, _ = get_vehicle()
        vehicle_id = veh.get("id") if veh else None
    except Exception:  # noqa: BLE001
        vehicle_id = None
    recent = get_v2l_sessions(lookback_days=lookback_days, limit=1, vehicle_id=vehicle_id)["sessions"]
    last = recent[-1] if recent else None
    active = bool(last and last.get("ongoing"))
    dur_min = int(round(last["duration_min"])) if last else 0
    return {
        "has_data": True,                          # always visible — never hide a feature on a guess
        "ever_used": last is not None,
        "active": active,
        "power_w": last["current_w"] if active else 0,
        "energy_wh": last["energy_wh"] if last else 0.0,
        "peak_w": last["peak_w"] if last else 0,
        "end": last["end"] if last else None,
        "duration": f"{dur_min // 60:02d}:{dur_min % 60:02d}",   # session length, hh:mm
        "power_max_w": 3500,
    }



def get_v2l_total_kwh() -> float:
    """All-time total energy DRAWN via V2L (sum of every reconstructed session), in kWh — for the
    Statistics 'total summary' card. Reconstructed from the positions log (no table), vehicle-scoped."""
    from db.vehicle import get_vehicle
    try:
        veh, _ = get_vehicle()
        vid = veh.get("id") if veh else None
    except Exception:  # noqa: BLE001
        vid = None
    wh = get_v2l_sessions(lookback_days=36500, limit=1_000_000, vehicle_id=vid)["total_energy_wh"]
    return round((wh or 0) / 1000.0, 2)



# ── Global map (all tracks + frequent places) ──────────────────────────────────


def _rows_to_segments(rows, max_points: int) -> list[list[list[float]]]:
    """Group ordered (trip_id, lat, lon) rows into one polyline per trip (never joined across
    trips), then proportionally downsample to ~max_points total while keeping each trip's real
    first/last point. Shared by the global map (get_all_track) and the report's month map."""
    segments: list[list[list[float]]] = []
    cur_id, cur = None, []
    for r in rows:
        if r["trip_id"] != cur_id:
            if len(cur) >= 2:
                segments.append(cur)
            cur, cur_id = [], r["trip_id"]
        cur.append([round(r["latitude"], 5), round(r["longitude"], 5)])
    if len(cur) >= 2:
        segments.append(cur)

    total = sum(len(s) for s in segments)
    if total <= max_points or total == 0:
        return segments
    # Proportional per-trip downsample, keeping each segment's real endpoints.
    step = total / max_points
    out = []
    for s in segments:
        keep = max(2, int(len(s) / step))
        if keep >= len(s):
            out.append(s)
            continue
        st = len(s) / keep
        ds = [s[int(i * st)] for i in range(keep)]
        ds[-1] = s[-1]
        out.append(ds)
    return out



def get_all_track(max_points: int = 12000) -> list[list[list[float]]]:
    """Every trip's GPS track as a list of polylines (one [lat, lon] list per trip),
    so the global map draws the actual driven roads as connected lines instead of
    loose dots. Points are NEVER joined across trips. Downsampled to roughly
    ``max_points`` total while always keeping each trip's first and last point, so the
    lines stay continuous even when zoomed in."""
    db = _get()
    rows = db.execute(
        "SELECT trip_id, latitude, longitude FROM trip_positions "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY trip_id, id"
    ).fetchall()
    return _rows_to_segments(rows, max_points)



def get_month_track(month: str, max_points: int = 8000) -> list[list[list[float]]]:
    """GPS polylines for every trip STARTED in the given local 'YYYY-MM' — the report's month
    map. Same shape/downsampling as get_all_track, scoped to one month's trips (parent and
    merged-child trips alike, so every road driven that month is drawn)."""
    if not month:
        return []
    db = _get()
    ids = []
    for r in db.execute("SELECT id, started_at FROM trips WHERE started_at IS NOT NULL").fetchall():
        dt = _local_dt(r["started_at"])
        if dt is not None and dt.strftime("%Y-%m") == month:
            ids.append(r["id"])
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT trip_id, latitude, longitude FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY trip_id, id", ids
    ).fetchall()
    return _rows_to_segments(rows, max_points)



def get_frequent_places(min_visits: int = 2, top_n: int = 15) -> list[dict]:
    """Cluster trip start/end points into recurring places (Home, Work, …) by snapping
    coordinates to a ~110 m grid (3 decimals) and counting visits. Returns the busiest
    clusters with an averaged centre and a visit count — no reverse geocoding, so it
    stays offline and cheap."""
    db = _get()
    rows = db.execute(
        "SELECT start_lat, start_lon, end_lat, end_lon FROM trips"
    ).fetchall()
    buckets: dict[tuple, dict] = {}
    for r in rows:
        for lat, lon in ((r["start_lat"], r["start_lon"]), (r["end_lat"], r["end_lon"])):
            if lat is None or lon is None:
                continue
            key = (round(lat, 3), round(lon, 3))
            b = buckets.setdefault(key, {"lat": 0.0, "lon": 0.0, "visits": 0})
            b["lat"] += lat
            b["lon"] += lon
            b["visits"] += 1
    places = [
        {"latitude": round(b["lat"] / b["visits"], 6),
         "longitude": round(b["lon"] / b["visits"], 6),
         "visits": b["visits"]}
        for b in buckets.values() if b["visits"] >= min_visits
    ]
    places.sort(key=lambda p: p["visits"], reverse=True)
    return places[:top_n]
