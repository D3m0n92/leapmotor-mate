"""Database queries — charges domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ, _iso_to_utc, _billed_kwh
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import i18n
import time



# Labels are intentionally language-neutral (international loanwords + universal
# electrical acronyms) so they never need translating across UI languages.
CHARGE_TYPES = {
    "HOME": {"label": "Home", "icon": "🏠", "color": "#22c55e"},
    "AC":   {"label": "AC",   "icon": "🔌", "color": "#60a5fa"},
    "FAST": {"label": "DC",   "icon": "⚡", "color": "#fb923c"},
    "HPC":  {"label": "HPC",  "icon": "🚀", "color": "#e879f9"},
    "FREE": {"label": "FREE", "icon": "🆓", "color": "#a3e635"},
    "MANUAL": {"label": "Manual", "icon": "✎", "color": "#94a3b8"},
}

# ── 📍 charging-station labels (resolved by web/charger_locator.py) ───────────
# A candidate is a closed public charge with a GPS fix and no label yet. Home charges
# are excluded twice over — by the HOME type and by any wallbox session evidence — so a
# pure-home install never triggers a single network lookup.
_LOCATION_CANDIDATES_WHERE = (
    "ended_at IS NOT NULL AND location_name IS NULL "
    "AND latitude IS NOT NULL AND longitude IS NOT NULL "
    "AND latitude <> 0 AND longitude <> 0 "
    "AND COALESCE(location_type, '') <> 'HOME' "
    "AND wallbox_energy_start_kwh IS NULL AND COALESCE(ac_energy_kwh, 0) <= 0.05"
)

_SCAN_MAX_KW = 250.0  # implied charge rate above this → spurious-SoC glitch, not a real charge



def update_charge_type(charge_id: int, location_type: str,
                       manual_cost: Optional[float] = None) -> dict:
    """Set location_type and (re)compute the cost from the pricing config in effect now (flat or
    time-of-use). Frozen afterwards (the 'new charges only' rule). HOME charges are billed on the
    wallbox energy the POLLER measured at charge start/stop (charges.ac_energy_kwh = the counter
    delta — exact, not estimated) when available; otherwise, and for every other type, on the
    battery (DC/SoC) energy.

    `MANUAL` is the user-entered total actually paid (the public-charging jungle — subscriptions,
    session/idle fees, pay-method rates — can't be modelled by a per-kWh tariff). It OVERRIDES the
    automatic cost: `manual_cost` is stored verbatim and the automatic costers (auto-confirm and the
    one-time repairs) leave a MANUAL charge's cost alone. It still feeds the WAC like any priced
    charge (rate = cost ÷ billed DC energy)."""
    from db.costs import compute_cost
    db = _conn_rw()
    row = db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()
    if not row:
        return {}

    charge = dict(row)
    charge["location_type"] = location_type
    if location_type == "MANUAL":
        # Keep the existing cost if no amount was supplied (e.g. re-tagging without re-typing it).
        cost = round(manual_cost, 2) if manual_cost is not None else charge.get("cost")
    else:
        meter = charge.get("ac_energy_kwh")
        billed = meter if (location_type == "HOME" and meter and meter > 0) else None
        cost = compute_cost(charge, ac_kwh=billed)

    db.execute(
        "UPDATE charges SET location_type=?, cost=? WHERE id=?",
        (location_type, cost, charge_id)
    )
    db.commit()
    return dict(db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone())



def auto_confirm_home_charges() -> int:
    """Auto-assign HOME to closed, still-untyped charges where the wallbox measured real AC
    energy (opt-in `wallbox_auto_home` setting; idea credit: @hubcasale, PR #47): if YOUR
    wallbox saw energy flow during the session, the charge happened at home. DC/public
    charges and reconstructed ones carry no wallbox session energy, so they stay manual.
    Each hit goes through update_charge_type — the SAME path as a manual badge confirm —
    so the cost honours the pricing config (flat or TOU bands) and the AC-energy billing;
    the type stays user-editable afterwards. The 0.05 kWh floor mirrors the phantom-charge
    threshold (meter jitter must not tag a charge). Runs on page renders (a settings probe
    + one SELECT, normally 0 rows) and when the toggle is switched on; returns # confirmed."""
    from db.settings import get_setting
    try:
        if get_setting("wallbox_auto_home", "0") != "1":
            return 0
        rows = _get().execute(
            "SELECT id FROM charges WHERE location_type IS NULL AND ended_at IS NOT NULL "
            "AND COALESCE(reconstructed, 0) = 0 AND COALESCE(ac_energy_kwh, 0) > 0.05"
        ).fetchall()
    except sqlite3.Error:   # fresh install — settings/charges tables not created yet
        return 0
    for r in rows:
        update_charge_type(r["id"], "HOME")
    return len(rows)



def has_location_lookup_candidates() -> bool:
    try:
        return _get().execute(
            f"SELECT 1 FROM charges WHERE {_LOCATION_CANDIDATES_WHERE} LIMIT 1"
        ).fetchone() is not None
    except sqlite3.Error:  # fresh install — column not migrated by the poller yet
        return False



def get_location_lookup_candidates(limit: int = 40) -> list[dict]:
    try:
        rows = _get().execute(
            f"SELECT id, latitude, longitude FROM charges WHERE {_LOCATION_CANDIDATES_WHERE} "
            "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]



def get_labelled_locations() -> list[tuple]:
    """(lat, lon, label) of every already-resolved charge — '' sentinels included — so
    a charge at an already-known spot reuses the answer instead of re-asking Overpass."""
    try:
        rows = _get().execute(
            "SELECT latitude, longitude, location_name FROM charges "
            "WHERE location_name IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [(r["latitude"], r["longitude"], r["location_name"]) for r in rows]



def set_charge_location_name(charge_id: int, name: str) -> None:
    db = _conn_rw()
    db.execute("UPDATE charges SET location_name=? WHERE id=?", (name, charge_id))
    db.commit()



def save_charge_note(charge_id: int, note: str) -> None:
    """#107: persist the optional free-text user note on a charge (empty string clears it)."""
    note = (note or "").strip()[:1000]
    db = _conn_rw()
    db.execute("UPDATE charges SET note=? WHERE id=?", (note or None, charge_id))
    db.commit()



def add_manual_charge(started_at: str, energy_kwh: float, cost: Optional[float] = None,
                      charge_type: str = "AC", ended_at: Optional[str] = None) -> int:
    """Insert a user-entered historical charge — e.g. sessions from before Mate was installed —
    so the lifetime totals / monthly report reflect them (#87). Manual charges carry only the
    essentials (date, energy, cost, AC/DC) and deliberately have NO telemetry: start/end SoC are
    left NULL, so they're excluded from the SoH estimate and have no power curve, and
    location_type='MANUAL' keeps the automatic costers from overwriting the cost the user typed."""
    db = _conn_rw()
    try:
        vrow = db.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vehicle_id = vrow["id"] if vrow else None
        ct = "DC" if str(charge_type).upper() in ("DC", "FAST", "HPC") else "AC"
        cur = db.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
            "charge_type, location_type, cost, reconstructed) "
            "VALUES (?, ?, ?, ?, ?, 'MANUAL', ?, 0)",
            (vehicle_id, started_at, ended_at or started_at, energy_kwh, ct, cost))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()



def delete_charge(charge_id: int) -> bool:
    """Permanently remove a charge session. Returns True if one was deleted. Day/month/lifetime
    charge totals recompute from the DB automatically. The shared per-poll positions log is untouched."""
    db = _conn_rw()
    cur = db.execute("DELETE FROM charges WHERE id=?", (charge_id,))
    db.commit()
    return cur.rowcount > 0



def get_charges(limit: int = 50) -> list[dict]:
    db = _get()
    rows = db.execute(
        "SELECT * FROM charges WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["started_at"] = _local_iso(d.get("started_at"))
        d["ended_at"] = _local_iso(d.get("ended_at"))
        out.append(d)
    return out



def get_last_charge_end() -> Optional[datetime]:
    """End time of the most recently COMPLETED charge (local-tz aware), or None if no
    charge has ever finished. Used to bound the "since last charge" getEC window."""
    db = _get()
    row = db.execute(
        "SELECT ended_at FROM charges WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1"
    ).fetchone()
    return _local_dt(row["ended_at"]) if row else None



def get_charge_power_curve(charge_id: int) -> dict:
    """Per-sample charging power for one session, for the expandable power chart.
    Power = |pack_voltage(1177) x pack_current(1178)| / 1000 — the same value as the
    HA `sensor.leapmotor_charging_power`. NOT rounded to 1 decimal (that flattens the
    curve); kept at 3 decimals so the real variation shows. Samples come from the
    general `positions` log (may be pruned over time → empty for very old sessions)."""
    from db.costs import _power_window_bounds
    db = _get()
    ch = db.execute("SELECT started_at, ended_at FROM charges WHERE id = ?", (charge_id,)).fetchone()
    if not ch:
        return {"labels": [], "power": [], "soc": []}
    start, end = ch["started_at"], ch["ended_at"]
    if end:
        # Cap the upper bound at the next charge's start so an orphan/overlapping charge
        # (whose ended_at bled past a later charge — see close_orphan_charges) cannot absorb
        # the next charge's power samples into its curve. That leak would inflate BOTH the
        # AC-vs-DC wallbox comparison AND the HOME cost (which bills the AC energy derived from
        # this curve) — GitHub #24. Mirrors _charge_active_window / compute_cost. For a normal
        # charge the next charge starts after ended_at → no cap, identical behaviour.
        lo, hi, excl = _power_window_bounds(db, start, end)
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a, soc FROM positions "
            "WHERE charging = 1 AND recorded_at >= ? AND recorded_at " + ("<" if excl else "<=")
            + " ? ORDER BY recorded_at",
            (lo, hi),
        ).fetchall()
    else:  # charge still in progress — open upper bound
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a, soc FROM positions "
            "WHERE charging = 1 AND recorded_at >= ? ORDER BY recorded_at",
            (start,),
        ).fetchall()
    labels, power, soc, times = [], [], [], []
    for r in rows:
        v = r["charge_voltage_v"] or 0
        a = r["charge_current_a"] or 0
        labels.append((_local_iso(r["recorded_at"]) or "")[11:16])  # HH:MM local
        power.append(round(abs(v * a) / 1000.0, 3))
        soc.append(r["soc"])
        times.append(r["recorded_at"])  # raw UTC ISO — used to align external (wallbox) history
    return {"labels": labels, "power": power, "soc": soc, "times": times}



def latest_charge_id_with_power() -> int | None:
    """Most recent charge that still has per-sample data (for the Wallbox page chart)."""
    db = _get()
    row = db.execute(
        "SELECT c.id FROM charges c WHERE EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None



def charges_with_power(limit: int = 30) -> list[dict]:
    """Recent HOME charges (= the wallbox) that still have a power curve — raw
    {id, started_at, energy_added_kwh}. Only HOME charges are relevant to the
    wallbox comparison: public/away charges (and unconfirmed NULL ones) are excluded,
    which also avoids attributing another car's wallbox session to this car."""
    db = _get()
    rows = db.execute(
        "SELECT c.id, c.started_at, c.energy_added_kwh FROM charges c "
        "WHERE c.location_type = 'HOME' AND EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]



def is_home_charge(charge_id: int) -> bool:
    """True only when the charge is tagged HOME (= the wallbox)."""
    db = _get()
    row = db.execute("SELECT location_type FROM charges WHERE id = ?", (charge_id,)).fetchone()
    return bool(row) and row["location_type"] == "HOME"



def unconfirmed_charges_count() -> int:
    """How many FINISHED charges still have no type set (location_type NULL) → need
    confirming. In-progress charges (ended_at NULL) are excluded: they can't be
    confirmed until they end, otherwise the banner would never clear while charging."""
    db = _get()
    row = db.execute(
        "SELECT COUNT(*) n FROM charges WHERE location_type IS NULL AND ended_at IS NOT NULL"
    ).fetchone()
    return row["n"] if row else 0



def latest_home_charge_cost():
    """Cost of the most recent home charge (= the wallbox) — from Mate's own charge
    records, so the Wallbox page reuses it instead of a separate HA cost sensor."""
    db = _get()
    row = db.execute(
        "SELECT cost FROM charges WHERE location_type = 'HOME' AND cost IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row["cost"] if row else None



def _charge_active_window(db, started_at, ended_at):
    """First & last sample with REAL charging power (positions.charging=1, which is set only when power
    flows — NOT on plug-in) inside the session window. Returns (start_utc_iso, end_utc_iso), or
    (None, None) when there are no power samples (e.g. pruned/old charges). Bounds are normalized to UTC
    because positions.recorded_at is UTC while the charge timestamps may arrive localized."""
    from db.costs import _power_window_bounds
    if not started_at:
        return None, None
    # Cap at the next charge's start so an orphan/overlapping charge (whose ended_at can
    # bleed past a later charge — see the poller's close_orphan_charges) cannot inherit the
    # next charge's last power sample as its own window end.
    lo, hi, excl = _power_window_bounds(db, started_at, ended_at)
    row = db.execute(
        "SELECT MIN(recorded_at) AS s, MAX(recorded_at) AS e FROM positions "
        "WHERE charging = 1 AND recorded_at >= ? AND recorded_at " + ("<" if excl else "<=") + " ?",
        (lo, hi),
    ).fetchone()
    return (row["s"], row["e"]) if (row and row["s"]) else (None, None)



def _charge_window_display(db, raw_start, raw_end) -> dict:
    """For the charges list: surface the REAL charging window (first→last power) only when it differs
    from the plug-in→unplug session window by more than a threshold — i.e. a delayed/scheduled charge
    or a long idle tail. For a normal charge the two coincide → {differs: False} (no extra clutter).
    Returns {differs: False} or {differs: True, real_start, real_end} (HH:MM, local)."""
    rs, re = _charge_active_window(db, raw_start, raw_end)
    if not rs:
        return {"differs": False}
    import datetime

    def _p(x):
        try:
            return datetime.datetime.fromisoformat(x)
        except Exception:
            return None

    s0, e0, rs0, re0 = _p(raw_start), _p(raw_end), _p(rs), _p(re)
    THRESH = 300  # seconds — below this the windows are "the same" (just poll granularity)
    differs = bool((s0 and rs0 and (rs0 - s0).total_seconds() > THRESH)
                   or (e0 and re0 and (e0 - re0).total_seconds() > THRESH))
    if not differs:
        return {"differs": False}
    return {"differs": True,
            "real_start": (_local_iso(rs) or "")[11:16],
            "real_end": (_local_iso(re) or "")[11:16]}



def get_charges_grouped() -> list[dict]:
    """Return charges nested as year → month → day."""
    from db.settings import get_language
    charges = get_charges()
    from collections import OrderedDict
    db = _get()

    def _node(label):
        return {"label": label, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False, "months": OrderedDict()}

    def _day_node(label):
        return {"label": label, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False, "charges": []}

    lang = get_language()
    years: dict = OrderedDict()
    for c in charges:
        if not c.get("started_at"):
            continue
        dt = _local_dt(c["started_at"])
        if dt is None:
            continue
        # Real charging window (first→last power) vs the plug-in→unplug session — compute on the RAW
        # UTC timestamps BEFORE we localize them below.
        c["active_window"] = _charge_window_display(db, c.get("started_at"), c.get("ended_at"))
        c["started_at"] = dt.isoformat()
        c["ended_at"] = _local_iso(c.get("ended_at"))

        yr  = dt.strftime("%Y")
        mo  = i18n.fmt_month_year(lang, dt)
        day = i18n.fmt_day_month_year(lang, dt)

        years.setdefault(yr, _node(yr))
        years[yr]["months"].setdefault(mo, {**_node(mo), "days": OrderedDict()})
        years[yr]["months"][mo]["days"].setdefault(day, _day_node(day))

        years[yr]["months"][mo]["days"][day]["charges"].append(c)

        kwh  = _billed_kwh(c)   # wallbox AC for HOME (billed); DC otherwise — matches the card
        cost = c.get("cost") or 0
        for node in [years[yr], years[yr]["months"][mo], years[yr]["months"][mo]["days"][day]]:
            node["kwh"]   = round(node["kwh"] + kwh, 2)
            node["count"] += 1
            if c.get("cost") is not None:
                node["cost"]     = round(node["cost"] + cost, 2)
                node["has_cost"] = True

    return list(years.values())



def scan_missed_charges(threshold: float = 2.0, apply: bool = False) -> list[dict]:
    """Find charges that happened while the car was asleep/offline BEFORE live
    reconstruction existed (or while the poller was down) and were never logged — a
    SoC that ROSE while parked, not covered by any existing charge (GitHub #35, from
    the #29 follow-up). Returns candidate dicts; with apply=True also inserts them as
    reconstructed charges (charge_type 'AC', cost NULL until the user confirms the type,
    exactly like the live reconstruction path).

    Idempotent: an applied candidate's window is then covered by its own charge row, so
    a re-run's overlap check skips it — running it twice creates no duplicates.

    Guards against false positives (which a one-shot silent migration could not afford,
    hence this is preview-then-confirm): parked at both ends (charging=0, speed<=1), the
    odometer UNCHANGED across the whole run (so regen while driving offline can't look
    like a charge), and no overlap with any existing charge window."""
    from db.health import get_battery_capacity_kwh
    db = _conn_rw() if apply else _get()
    v = db.execute("SELECT id FROM vehicles LIMIT 1").fetchone()
    if not v:
        return []
    vehicle_id = v["id"]
    rows = db.execute(
        "SELECT recorded_at, soc, charging, speed_kmh, odometer_km, latitude, longitude "
        "FROM positions WHERE vehicle_id=? AND soc IS NOT NULL ORDER BY recorded_at, id",
        (vehicle_id,)).fetchall()
    charges = db.execute(
        "SELECT started_at, ended_at FROM charges WHERE vehicle_id=?", (vehicle_id,)).fetchall()
    cap = get_battery_capacity_kwh()

    def _parked(r):
        return (r["charging"] or 0) == 0 and (r["speed_kmh"] or 0) <= 1

    def _odo_same(a, b):
        oa, ob = a["odometer_km"], b["odometer_km"]
        return oa is None or ob is None or abs(ob - oa) < 0.5

    def _overlaps(start, end):
        for c in charges:
            cs, ce = c["started_at"], (c["ended_at"] or "9999")   # NULL end = open-ended
            if start <= ce and cs <= end:                          # inclusive interval overlap
                return True
        return False

    candidates, i, n = [], 0, len(rows)
    while i < n - 1:
        a, b = rows[i], rows[i + 1]
        if not (b["soc"] - a["soc"] > 0 and _parked(a) and _parked(b) and _odo_same(a, b)):
            i += 1
            continue
        # Extend the run while SoC keeps rising, parked, and the odometer never moves —
        # so one charge seen across several stale polls becomes ONE candidate, not many.
        run_start, run_end, j = a, b, i + 1
        while j < n - 1:
            c, d = rows[j], rows[j + 1]
            if d["soc"] - c["soc"] > 0 and _parked(c) and _parked(d) and _odo_same(run_start, d):
                run_end, j = d, j + 1
            else:
                break
        rise = run_end["soc"] - run_start["soc"]
        if rise >= threshold and run_start["soc"] >= 1.0 and not _overlaps(run_start["recorded_at"], run_end["recorded_at"]):
            try:
                dur = round((datetime.fromisoformat(run_end["recorded_at"])
                             - datetime.fromisoformat(run_start["recorded_at"])).total_seconds() / 60, 1)
            except (TypeError, ValueError):
                dur = None
            # Plausibility: a spurious SoC=0/low reading makes a "charge" of impossible power (a full
            # pack in seconds). Skip runs whose implied rate exceeds any real charger; keep when the
            # duration is unknown (start_soc>=1 already filters the zero-start glitch).
            implied_kw = (rise / 100.0 * cap) / (dur / 60.0) if dur and dur > 0 else None
            if implied_kw is not None and implied_kw > _SCAN_MAX_KW:
                i = j + 1
                continue
            candidates.append({
                "started_at": run_start["recorded_at"], "ended_at": run_end["recorded_at"],
                "start_soc": run_start["soc"], "end_soc": run_end["soc"],
                "energy_kwh": round(max(rise / 100.0 * cap, 0), 3), "duration_min": dur,
                "latitude": run_end["latitude"], "longitude": run_end["longitude"],
            })
        i = j + 1

    if apply and candidates:
        for c in candidates:
            db.execute(
                """INSERT INTO charges
                   (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,
                    duration_min, latitude, longitude, charge_type, reconstructed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (vehicle_id, c["started_at"], c["ended_at"], c["start_soc"], c["end_soc"],
                 c["energy_kwh"], c["duration_min"], c["latitude"], c["longitude"], "AC"))
        db.commit()
    return candidates



def _integrate_charge_energy_kwh(db, start: str, end: str | None) -> float:
    """Real DC energy delivered into the pack during a charge = ∫|V·I|dt over the
    logged samples (trapezoidal). V/I come from signals 1177/1178 in `positions`, the
    same source as the power-curve chart and the Wallbox DC comparison. This is a
    MEASURED energy, independent of SoC — so dividing it by the SoC delta gives an
    estimate of usable pack capacity that actually tracks battery ageing (unlike the
    stored energy_added_kwh, which is SoC × nominal capacity and would be circular)."""
    from db.costs import _power_window_bounds
    if end:
        # Cap at the next charge's start (same leak guard as get_charge_power_curve / compute_cost)
        # so an overlapping orphan charge can't inflate the integrated DC energy / SoH estimate.
        lo, hi, excl = _power_window_bounds(db, start, end)
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
            "WHERE charging = 1 AND recorded_at >= ? AND recorded_at " + ("<" if excl else "<=")
            + " ? ORDER BY recorded_at",
            (lo, hi),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
            "WHERE charging = 1 AND recorded_at >= ? ORDER BY recorded_at",
            (start,),
        ).fetchall()
    energy = 0.0
    prev_t = None
    prev_p = 0.0
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r["recorded_at"]).replace(" ", "T").rstrip("Z"))
        except Exception:
            continue
        p = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
        if prev_t is not None:
            dt_h = (t - prev_t).total_seconds() / 3600.0
            # Guard against gaps (deep-sleep / pruning): ignore intervals over 15 min.
            if 0 < dt_h <= 0.25:
                energy += (p + prev_p) / 2.0 * dt_h
        prev_t, prev_p = t, p
    return energy



def _charge_has_soc_jump(db, start: str, end: str | None,
                         max_rate_per_min: float = 0.8) -> bool:
    """True if any two consecutive charging samples in the session show a SoC rise rate
    faster than max_rate_per_min %/min — a BMS recalibration snap, not real energy.
    At AC rates (≤ 22 kW on 67 kWh), the physical max is ~0.55%/min; a threshold of 0.8
    leaves margin for fast 3-phase AC while still catching BMS jumps (e.g. +2.5%/min).
    Only call this for AC charge types — DC fast-charging can legitimately reach 3-4%/min."""
    clause = "recorded_at >= ? AND recorded_at <= ?" if end else "recorded_at >= ?"
    params = (start, end) if end else (start,)
    rows = db.execute(
        f"SELECT recorded_at, soc FROM positions WHERE {clause} AND charging = 1 "
        "AND soc IS NOT NULL ORDER BY recorded_at",
        params,
    ).fetchall()
    prev_soc, prev_t = None, None
    for r in rows:
        soc = r["soc"]
        try:
            t = datetime.fromisoformat(str(r["recorded_at"]).replace(" ", "T").rstrip("Z"))
        except Exception:
            prev_soc, prev_t = soc, None
            continue
        if prev_soc is not None and prev_t is not None:
            dt_min = (t - prev_t).total_seconds() / 60.0
            if 0 < dt_min <= 15.0 and (soc - prev_soc) / dt_min > max_rate_per_min:
                return True
        prev_soc, prev_t = soc, t
    return False



def _charge_has_active_use(db, start: str, end: str | None) -> bool:
    """True if any position sample during the charge window had cabin HVAC running
    (climate_cooling=1 or climate_heating=1 — not just climate_on, which also fires during
    battery thermal management and is too broad). A running cabin compressor/heater is a
    reliable proxy for 'user was in the car consuming power', which distorts the energy/SoC
    ratio used for the SoH estimate."""
    clause = "recorded_at >= ? AND recorded_at <= ?" if end else "recorded_at >= ?"
    params = (start, end) if end else (start,)
    row = db.execute(
        f"SELECT 1 FROM positions WHERE {clause} "
        "AND (climate_cooling = 1 OR climate_heating = 1) LIMIT 1",
        params,
    ).fetchone()
    return row is not None



def _charge_temp_odo(db, start: str, end: str | None):
    """Coldest battery temperature (°C) and the odometer (km) seen WHILE CHARGING in a session,
    from the positions log. The min temp is the conservative basis for the cold-charge gate; the
    odometer gives the per-distance (cycle-ageing) axis of the SoH trend."""
    if end:
        rows = db.execute(
            "SELECT battery_min_temp, odometer_km FROM positions WHERE charging = 1 "
            "AND recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at", (start, end)).fetchall()
    else:
        rows = db.execute(
            "SELECT battery_min_temp, odometer_km FROM positions WHERE charging = 1 "
            "AND recorded_at >= ? ORDER BY recorded_at", (start,)).fetchall()
    temps = [r["battery_min_temp"] for r in rows if r["battery_min_temp"] is not None]
    odos = [r["odometer_km"] for r in rows if r["odometer_km"] is not None]
    return (min(temps) if temps else None), (max(odos) if odos else None)
