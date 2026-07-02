"""Database queries — health domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import re
import time



_AC_CHARGE_TYPES = ('AC', 'HOME', 'FREE')   # types where DC fast-rate is impossible

# SoC arrives as preciseSoc (signal 100003) with 0.1% resolution, and a ±0.1% parked BMS
# jitter is real (both up- and down-ticks observed while parked, odometer flat). Worst case
# each window endpoint is one quantum off, so a window's drop carries up to ±0.2% of pure
# measurement error — which the %/day extrapolation multiplies by 24/hours (#41).
SOC_QUANTUM = 0.1

_DROP_ERR = 2 * SOC_QUANTUM

# The intrinsic noise floor: a parked drop below 2 sensor quanta is jitter, not drain. The user's
# `vampire_min_drop_pct` is a DISPLAY threshold layered on top — raising it thins the charted bars,
# but it must never make a car that DOES lose charge look like it has no parked data at all (#63).
# So we always collect windows down to this floor and tag which ones clear the user's threshold,
# letting the page tell "no parked data yet" apart from "data exists, just below your threshold".
_VAMPIRE_NOISE_FLOOR = 0.2

_VAMPIRE_ACTIVE_USE_RATE = 15.0  # %/day above this is active use (A/C, meeting, etc.), not standby



# ── Battery health (SoH) ───────────────────────────────────────────────────────


def get_battery_capacity_kwh() -> float:
    """Configured (nominal) usable battery capacity, set per-model at first run and
    overridable in Settings. Used as the 100%-SoC reference for the health estimate."""
    from db.settings import get_setting
    try:
        return float(get_setting("battery_capacity_kwh", "65.0"))
    except (TypeError, ValueError):
        return 65.0



def get_battery_health(min_soc_delta: float = 12.0, temp_min_c: float | None = None) -> dict:
    """Estimate usable battery capacity / state-of-health over time from charge sessions. For
    each charge with a meaningful SoC rise we integrate the measured DC energy and divide by the
    SoC delta → estimated full-pack capacity.

    Two LFP-specific refinements keep the trend honest:
    - **Cold charges are shown but excluded** from the headline/trend. A cold LFP pack delivers
      less and its BMS SoC drifts, so a winter session reads low — that's temperature, not ageing.
      Charges whose min battery temp is below `temp_min_c` (Settings `soh_temp_min_c`, default 15°C)
      get `excluded: True` and don't feed the figure, but stay in `points` for the chart.
    - **Charges ending near 100% weigh most** in the headline: the BMS recalibrates SoC near full,
      so their SoC delta — and therefore the estimate — is the most trustworthy.

    Single sessions are noisy, so the headline is a weighted mean over the most recent valid ones.
    Charges with no stored telemetry (pruned) are skipped entirely."""
    from db.charges import _charge_has_active_use, _charge_has_soc_jump, _charge_temp_odo, _integrate_charge_energy_kwh
    from db.settings import get_setting
    db = _get()
    # SoH is measured-vs-as-new, so the denominator is the ORIGINAL spec capacity, not
    # the energy-calc capacity the user may have overridden — otherwise adopting a
    # measured (already-aged) value would reset SoH to ~100% and hide the ageing.
    # battery_capacity_nominal_kwh is snapshotted the first time the user overrides.
    try:
        nominal = float(get_setting("battery_capacity_nominal_kwh", "") or get_battery_capacity_kwh())
    except (TypeError, ValueError):
        nominal = get_battery_capacity_kwh()
    if temp_min_c is None:
        try:
            temp_min_c = float(get_setting("soh_temp_min_c", "15") or 15)
        except (TypeError, ValueError):
            temp_min_c = 15.0
    rows = db.execute(
        "SELECT id, started_at, ended_at, start_soc, end_soc, charge_type "
        "FROM charges WHERE ended_at IS NOT NULL AND start_soc IS NOT NULL "
        "AND end_soc IS NOT NULL ORDER BY started_at",
    ).fetchall()
    points = []
    for r in rows:
        delta = (r["end_soc"] or 0) - (r["start_soc"] or 0)
        if delta < min_soc_delta:                      # tiny top-ups → huge relative error
            continue
        energy = _integrate_charge_energy_kwh(db, r["started_at"], r["ended_at"])
        if energy <= 0.1:                              # no usable telemetry (pruned / AC-only meter)
            continue
        est = energy / (delta / 100.0)
        # Drop physically implausible estimates (sampling gaps, bad V/I spikes).
        if not (nominal * 0.5 <= est <= nominal * 1.15):
            continue
        temp, odo = _charge_temp_odo(db, r["started_at"], r["ended_at"])
        cold = temp is not None and temp < temp_min_c
        soc_jump = (not cold and r["charge_type"] in _AC_CHARGE_TYPES
                    and _charge_has_soc_jump(db, r["started_at"], r["ended_at"]))
        active_use = (not cold and not soc_jump
                      and _charge_has_active_use(db, r["started_at"], r["ended_at"]))
        excluded = cold or soc_jump or active_use
        exclude_reason = ("cold" if cold else ("soc_jump" if soc_jump else "active_use")) if excluded else None
        dt = _local_dt(r["started_at"])
        points.append({
            "charge_id": r["id"],
            "date": dt.strftime("%Y-%m-%d") if dt else (r["started_at"] or "")[:10],
            "ts": dt.isoformat() if dt else r["started_at"],
            "capacity_kwh": round(est, 1),
            "soh_pct": round(est / nominal * 100, 1) if nominal else None,
            "soc_delta": round(delta, 1),
            "end_soc": round(r["end_soc"], 1) if r["end_soc"] is not None else None,
            "energy_kwh": round(energy, 2),
            "temp_c": round(temp, 1) if temp is not None else None,
            "odometer_km": round(odo) if odo is not None else None,
            "charge_type": r["charge_type"],
            "excluded": excluded,
            "exclude_reason": exclude_reason,
        })
    valid = [p for p in points if not p["excluded"]]

    # Weight a session by how close it ended to a full (BMS-recalibrated) 100% — that's where the
    # LFP SoC is trustworthy, so its SoC delta (and the estimate) carries the least error.
    def _w(p):
        es = p.get("end_soc")
        return 1.0 if es is None else max(0.25, min(1.0, (es - 50.0) / 50.0))

    tail = valid[-5:]                                  # weighted mean of the recent valid estimates
    if tail:
        wsum = sum(_w(p) for p in tail)
        latest_cap = round(sum(p["capacity_kwh"] * _w(p) for p in tail) / wsum, 1)
        latest_soh = round(latest_cap / nominal * 100, 1) if nominal else None
    else:
        latest_cap = latest_soh = None
    return {
        "nominal_kwh": round(nominal, 1),
        "points": points,
        "sample_count": len(valid),
        "excluded_count": len(points) - len(valid),
        "cold_count": sum(1 for p in points if p.get("exclude_reason") == "cold"),
        "active_use_count": sum(1 for p in points if p.get("exclude_reason") == "active_use"),
        "soc_jump_count": sum(1 for p in points if p.get("exclude_reason") == "soc_jump"),
        "temp_min_c": round(temp_min_c, 1),
        "latest_capacity_kwh": latest_cap,
        "latest_soh_pct": latest_soh,
    }



def get_vampire_drain(min_hours: float = 1.0, min_drop_pct: float = 0.2,
                      lookback_days: int = 90, limit: int = 60) -> dict:
    """Vampire drain = SoC lost while the car is OFF (Ready/ON3 = 0) and NOT charging — measured
    exactly from power-OFF to the next power-ON (precise, via positions.ready; falls back to the old
    speed<1 "parked" test only for trips logged before the ready signal existed). This INCLUDES
    off-state remote heating/cooling (it ran while the car was off) and EXCLUDES on-state idle
    (Ready+P with climate, which belongs to the driving session). Scans the per-poll
    `positions` log, groups consecutive OFF samples (charging=0, not moving) into windows
    bounded by any charging or driving — driving is detected by speed OR a rise in odometer between
    idle samples, so a drive that happened during a reporting gap can't be mistaken for drain. Each
    kept window reports its SoC drop, a normalised %/day rate, the rate's quantization error band
    (`rate_err`) and whether the rate is trustworthy (`reliable`: a drop of at least 4 quanta AND
    an error band within ±1 %/day — short windows extrapolate a single sensor step into several
    %/day, see #41). Windows shorter than `min_hours` or with a drop below `min_drop_pct` (sensor
    jitter) are not charted, but every park >= `min_hours` — zero-drop ones included — feeds the
    time-weighted `typical_pct_per_day` headline. Pure read over data Mate already records every
    poll — no extra polling, no user input."""
    db = _get()
    # Collect down to the intrinsic noise floor regardless of the user's display threshold, so a
    # raised `min_drop_pct` thins the chart without hiding that drain exists at all (#63).
    floor = min(min_drop_pct, _VAMPIRE_NOISE_FLOOR)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = db.execute(
        "SELECT recorded_at, soc, charging, speed_kmh, odometer_km, ac_port_mode, ready FROM positions "
        "WHERE soc IS NOT NULL AND recorded_at >= ? ORDER BY recorded_at",
        (cutoff,),
    ).fetchall()

    windows = []
    agg = {"drop": 0.0, "hours": 0.0}

    def _flush(w, ongoing=False, close=None):
        if not w:
            return
        soc_end, t_end = w["soc_last"], w["t_last"]
        # The park ended at a wake into driving/charging: the first fresh reading reveals the SoC
        # that actually drained DURING deep sleep — while asleep the car stops reporting and the
        # cloud serves a FROZEN SoC, so the parked samples sit flat and a slow loss is invisible
        # until wake (and is otherwise lost if the car is driven right away: the parked window
        # closes at the frozen value and the drop falls in the gap before the trip's start SoC).
        # Close the window at that fresh value + time so the drain is captured — but only when it's
        # a DROP (a rise = BMS recalibration / charge → keep the parked value, never invent drain).
        if close is not None and close["soc"] is not None and close["soc"] < (soc_end or 0):
            soc_end, t_end = close["soc"], close["recorded_at"]
        t0, t1 = _local_dt(w["t0"]), _local_dt(t_end)
        if t0 is None or t1 is None:
            return
        hours = (t1 - t0).total_seconds() / 3600.0
        drop = (w["soc0"] or 0) - (soc_end or 0)
        pct_per_day = drop / hours * 24 if hours else 0
        # OFF-state high-rate windows are flagged (amber) as likely remote heating/cooling, but — unlike
        # the old speed-based logic — they are NOT excluded: drain while the car is OFF is OFF drain by
        # the Ready-OFF→Ready-ON definition (the in-card note says off-climate is included).
        active_use = pct_per_day > _VAMPIRE_ACTIVE_USE_RATE
        if hours >= min_hours:
            # Headline aggregate: every OFF stretch long enough to measure counts, including zero-drop
            # ones (a "drain happened"-only sample reads high — selection bias). SoC up-ticks are BMS
            # jitter → clamp to 0.
            agg["hours"] += hours
            agg["drop"] += max(drop, 0.0)
        # Compare the rounded drop: raw float drops sit a hair off the threshold
        # (56.8 − 56.4 = 0.3999…), so identical physical drops would randomly pass/fail.
        drop_r = round(drop, 1)
        if hours >= min_hours and drop_r >= floor - 1e-9:
            err = _DROP_ERR / hours * 24
            windows.append({
                "start": t0.isoformat(), "end": t1.isoformat(),
                "hours": round(hours, 1),
                "soc_start": round(w["soc0"], 1), "soc_end": round(soc_end, 1),
                "drop_pct": drop_r,
                "pct_per_day": round(pct_per_day, 1),
                "rate_err": round(err, 1),
                "reliable": drop_r >= 2 * _DROP_ERR - 1e-9 and err <= 1.0,
                "ongoing": ongoing,
                "active_use": active_use,
                # Clears the user's display threshold → charted as a bar; otherwise it's a real
                # parked window kept only to power the "below your threshold" hint + headline.
                "_charted": drop_r >= min_drop_pct - 1e-9,
            })

    cur = None
    for r in rows:
        # A V2L / bidirectional-discharge sample (ac_port_mode==2) is NOT standby: the car is parked
        # but actively powering an external load, so that SoC loss is V2L output, not vampire drain.
        # Treat it like charging — it BOUNDS the parked window and its drop is never read as drain.
        v2l = r["ac_port_mode"] == 2
        # OFF window = car powered down (Ready/ON3 = 0), not charging, not V2L. Falls back to the old
        # speed<1 test only when the ready signal is absent (trips before it was logged). The drain now
        # spans exactly Ready-OFF → next Ready-ON: on-state idle (Ready+P with climate) is NOT counted,
        # while OFF-state remote heating/cooling IS (per the in-card note).
        rd = r["ready"]
        idle = (not r["charging"]) and (not v2l) and (rd == 0 if rd is not None else (r["speed_kmh"] or 0) < 1)
        odo = r["odometer_km"]
        # a rise in odometer since the window's last idle sample → a drive happened (even if its
        # samples were missed) → the park ended there.
        if (cur is not None and odo is not None and cur["odo_last"] is not None
                and odo - cur["odo_last"] > 0.5):
            _flush(cur)
            cur = None
        if not idle:                        # driving / charging / V2L now → park ended
            # Close at the wake's fresh SoC only on a DRIVING transition (the odometer-rise guard
            # above already split off any drive that happened in a gap, so a same-odometer drive
            # sample here is a genuine wake-after-park → its SoC is real standby drain). A CHARGING
            # or V2L transition is left as-is: the pre-charge gap is ambiguous (could be a drive to
            # the charger), and a V2L drop is bidirectional-discharge output (not standby) — so we
            # never infer drain from either.
            _flush(cur, close=(None if (r["charging"] or v2l) else r))
            cur = None
            continue
        if cur is None:                     # start a new parked window
            cur = {"t0": r["recorded_at"], "soc0": r["soc"],
                   "t_last": r["recorded_at"], "soc_last": r["soc"], "odo_last": odo}
        else:                               # extend the current parked window
            cur["t_last"] = r["recorded_at"]
            cur["soc_last"] = r["soc"]
            if odo is not None:
                cur["odo_last"] = odo
    _flush(cur, ongoing=True)               # the trailing park is still open

    windows = windows[-limit:]
    # Split the kept (>= noise floor) windows into the ones charted at the user's display
    # threshold and the rest. `measurable` = real parked drain that exists regardless of the
    # slider; `below_threshold` powers the "data exists, just below your X% threshold" hint so a
    # raised slider never reads as "no parked data at all" (#63).
    charted = [w for w in windows if w.pop("_charted")]
    measurable = len(windows)
    active_use_count = sum(1 for w in charted if w.get("active_use"))
    # Time-weighted typical (total SoC lost / total parked time): quantization noise cancels
    # across windows instead of every short park voting like a long one, and slow drain below
    # the per-window display threshold still surfaces. Gated on `measurable` (not the charted
    # count) so the headline survives a raised display threshold; None while nothing clears the
    # noise floor, so young installs keep the no-data state.
    typical = round(agg["drop"] / agg["hours"] * 24, 1) if measurable and agg["hours"] else None
    return {"windows": charted, "count": len(charted),
            "measurable_count": measurable, "below_threshold": measurable - len(charted),
            "active_use_count": active_use_count,
            "min_drop_pct": round(min_drop_pct, 1),
            "typical_pct_per_day": typical, "lookback_days": lookback_days}
