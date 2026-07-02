"""Database queries — costs domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ, _iso_to_utc, _billed_kwh
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import re
import time



PRICE_KEYS = {
    "HOME": "price_home_kwh",
    "AC":   "price_ac_kwh",
    "FAST": "price_fast_kwh",
    "HPC":  "price_hpc_kwh",
}

# ── Charging-cost configuration (flat 24h vs time-of-use bands) ───────────────
# Stored in `settings`: cost_mode = 'flat'|'tou', tou_method = 'split'|'start',
# tou_bands = JSON list of {start, end, prices:{HOME,AC,FAST,HPC}}. The flat
# price_*_kwh values double as the "off-band" price in time-of-use mode.
_TOU_TYPES = ["HOME", "AC", "FAST", "HPC"]



def get_charge_prices() -> dict:
    db = _get()
    rows = db.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'price_%_kwh'"
    ).fetchall()
    return {r["key"]: float(r["value"]) for r in rows}



def _mode_allowed(ctype: str, mode: str) -> bool:
    """Dynamic (HA sensor) is HOME-ONLY (Silvio 02/07): no HA integration exposes a price for
    public AC/DC/HPC charging — those are operator-billed, not a home tariff — so 'dynamic' on
    an away type is never a valid choice, whatever wrote it (UI, a raw API call, or a value
    saved before this rule existed)."""
    return mode in ("flat", "tou") or (mode == "dynamic" and ctype == "HOME")



def get_cost_config() -> dict:
    """Pricing config for the Costs page: mode, calc method and the user bands.

    `modes` (#106 fix) = the pricing mode PER CHARGE TYPE {HOME,AC,FAST,HPC}, resolved from the
    `cost_modes` JSON setting; types not explicitly set — or set to a mode `_mode_allowed`
    rejects (dynamic on an away type) — default from the legacy global `cost_mode`, read-time
    resolution, no write migration. The legacy-'dynamic' default is CORRECTIVE, see
    `_default_mode_for`."""
    from db.settings import get_setting
    raw = get_setting("tou_bands", "")
    try:
        bands = json.loads(raw) if raw else []
        if not isinstance(bands, list):
            bands = []
    except (ValueError, TypeError):
        bands = []
    legacy = get_setting("cost_mode", "flat")
    try:
        m = json.loads(get_setting("cost_modes", "") or "{}")
        m = m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        m = {}
    modes = {t: (m.get(t) if m.get(t) in ("flat", "tou", "dynamic") and _mode_allowed(t, m.get(t))
                 else _default_mode_for(t, legacy))
             for t in _TOU_TYPES}
    return {
        "mode":   legacy,
        "modes":  modes,
        "method": get_setting("tou_method", "split"),
        "bands":  bands,
    }



def _default_mode_for(ctype: str, legacy: str) -> str:
    """Per-type default when `cost_modes` doesn't name a type. Legacy global 'dynamic' was a
    pricing BUG for away charges (the single home-tariff sensor priced public AC/DC/HPC too —
    spot prices can sit near zero → silently wrong costs, the #106 report): the fix's migration
    CORRECTS it rather than preserving it — dynamic carries over to HOME only, public types drop
    to their fixed base prices. flat/tou never had the bug → they apply to every type as
    before."""
    if legacy == "dynamic":
        return "dynamic" if ctype == "HOME" else "flat"
    return legacy



def save_cost_modes(modes: dict) -> None:
    """Persist the per-charge-type pricing modes (#106). Values are sanitised to the three known
    modes AND to `_mode_allowed` (dynamic is HOME-only — rejected here too, not just at read
    time, so a raw API call can't park an away type on 'dynamic' in storage); unknown/rejected/
    missing types fall back to the legacy global mode at read time. When all four types agree,
    the legacy `cost_mode` is aligned too, so single-mode users keep a coherent value
    everywhere."""
    from db.settings import set_setting
    clean = {t: v for t, v in (modes or {}).items()
             if t in _TOU_TYPES and v in ("flat", "tou", "dynamic") and _mode_allowed(t, v)}
    set_setting("cost_modes", json.dumps(clean))
    vals = set(clean.values())
    if len(clean) == len(_TOU_TYPES) and len(vals) == 1:
        set_setting("cost_mode", vals.pop())



def get_dynamic_price_entity() -> str:
    """Saved HA entity_id for the 'dynamic sensor' pricing mode, or '' if none chosen."""
    from db.settings import get_setting
    return get_setting("dynamic_price_entity_id", "")



def save_dynamic_price_entity(entity_id: str) -> None:
    from db.settings import set_setting
    set_setting("dynamic_price_entity_id", (entity_id or "").strip())



def get_dynamic_price_entity_for(ctype: str) -> str:
    """Dynamic-price sensor for ONE charge type (#106 fix): the per-type choice from the
    `dynamic_price_entities` JSON map. Only HOME falls back to the legacy single entity (that
    sensor IS the home tariff — a pre-fix dynamic setup keeps its home pricing with zero
    reconfiguration). Other types get NO silent fallback: an away type explicitly set to
    dynamic without its own sensor prices at its base — falling back to the home sensor would
    re-introduce the very bug this fixes."""
    from db.settings import get_setting
    try:
        raw = get_setting("dynamic_price_entities", "")
        m = json.loads(raw) if raw else {}
        e = (m.get(ctype) or "").strip() if isinstance(m, dict) else ""
    except Exception:  # noqa: BLE001 — settings table may not exist in minimal test DBs
        e = ""
    if e:
        return e
    return get_dynamic_price_entity() if ctype == "HOME" else ""



def save_dynamic_price_entity_for(ctype: str, entity_id: str) -> None:
    from db.settings import get_setting, set_setting
    if ctype not in _TOU_TYPES:
        return
    try:
        raw = get_setting("dynamic_price_entities", "")
        m = json.loads(raw) if raw else {}
        m = m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        m = {}
    m[ctype] = (entity_id or "").strip()
    set_setting("dynamic_price_entities", json.dumps(m))



def save_cost_config(mode: str, method: str, bands: list) -> None:
    """Persist the Costs-page config. Bands are sanitised to {start,end,prices}."""
    from db.settings import set_setting
    mode   = mode   if mode   in ("flat", "tou", "dynamic") else "flat"
    method = method if method in ("split", "start") else "split"
    clean = []
    for b in bands or []:
        if not isinstance(b, dict):
            continue
        start = str(b.get("start", "")).strip()
        end   = str(b.get("end", "")).strip()
        if not start or not end:
            continue
        prices, src = {}, (b.get("prices") or {})
        for t in _TOU_TYPES:
            try:
                prices[t] = round(float(src.get(t)), 4)
            except (TypeError, ValueError):
                prices[t] = None
        # Days of the week the band applies to (0=Mon … 6=Sun). Empty/invalid =
        # every day, so a band always applies somewhere.
        raw_days = b.get("days")
        days = sorted({int(d) for d in raw_days
                       if isinstance(d, (int, float)) and 0 <= int(d) <= 6}) \
            if isinstance(raw_days, list) else []
        if not days:
            days = list(range(7))
        clean.append({"start": start, "end": end, "days": days, "prices": prices})
    set_setting("cost_mode", mode)
    set_setting("tou_method", method)
    set_setting("tou_bands", json.dumps(clean))



def _parse_hhmm(s) -> Optional[int]:
    """'HH:MM' → minute-of-day (0–1440), or None if unparseable."""
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v <= 24 * 60 else None
    except (ValueError, AttributeError):
        return None



def _time_in_window(minute: int, start_min: int, end_min: int) -> bool:
    """Is minute-of-day inside [start, end)? Handles windows crossing midnight
    (start > end, e.g. 23:30→06:30). start == end means the whole day."""
    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= minute < end_min
    return minute >= start_min or minute < end_min



def _band_covers(b: dict, weekday: int, minute: int) -> bool:
    """Does this band cover (weekday, minute-of-day)? A band crossing midnight (start > end,
    e.g. 23:30→07:30) is anchored to the day it STARTS: its pre-midnight part [start,24:00)
    applies when that day is in `days`; its post-midnight part [00:00,end) belongs to the
    PREVIOUS day's membership — so a Saturday-only off-peak band also covers the early Sunday
    hours, but a Sunday-only band does not."""
    days = b.get("days")
    if not isinstance(days, list) or not days:
        days = list(range(7))
    s, e = _parse_hhmm(b.get("start")), _parse_hhmm(b.get("end"))
    if s is None or e is None:
        return False
    if s == e:                                        # whole-day band
        return weekday in days
    if s < e:                                         # same-day window
        return s <= minute < e and weekday in days
    if minute >= s and weekday in days:               # crosses midnight: pre-midnight → this day
        return True
    return minute < e and (weekday - 1) % 7 in days   # post-midnight → previous day



def _match_band(bands: list, weekday: int, minute: int):
    """First band that covers this (weekday, minute-of-day), regardless of charge type."""
    for b in bands:
        if _band_covers(b, weekday, minute):
            return b
    return None



def _resolve_band_price(bands: list, ctype: str, weekday: int, minute: int,
                        base: float, base_set: bool):
    """TYPE-AWARE band price for a moment (#106 fix): the first band covering this moment
    WITH a price set for this charge type wins — a blank cell means "this band is not for
    this type", so overlapping windows can serve different types (the home 23-07 off-peak and
    a public AC network's own 22-06 band coexist; each type reads its own). Previously the
    first time-matching band won for every type and a blank cell dropped straight to base,
    which silently killed any later overlapping band. No band prices this type at this
    moment → the type's base price (is_set=False when that base isn't configured either →
    not costed)."""
    for b in bands:
        if _band_covers(b, weekday, minute):
            bp = (b.get("prices") or {}).get(ctype)
            if bp is not None:
                return float(bp), True
    return base, base_set



def _next_charge_start_utc(db, started_at) -> Optional[str]:
    """UTC start of the first charge beginning strictly after `started_at` (a raw stored
    value), or None. Used to cap a charge's power-sample window: an orphan/overlapping
    charge whose ended_at bled past a later charge (see the poller's close_orphan_charges)
    must NOT absorb the next charge's power samples into its own window or cost."""
    try:
        row = db.execute(
            "SELECT MIN(started_at) AS s FROM charges WHERE started_at > ?", (started_at,)
        ).fetchone()
    except sqlite3.Error:
        return None   # no charges table (isolated unit tests) → no cap
    return _iso_to_utc(row["s"]) if (row and row["s"]) else None



def _power_window_bounds(db, started_at, ended_at):
    """(lower_utc, upper, upper_is_exclusive) for a charge's charging=1 samples, capping
    the upper bound at the next charge's start so a window/cost never leaks across charges.
    When capped, the upper bound is EXCLUSIVE (the next charge owns samples at its start)."""
    lo = _iso_to_utc(started_at) or started_at
    hi = _iso_to_utc(ended_at) or lo
    nxt = _next_charge_start_utc(db, started_at)
    if nxt and nxt <= hi:
        return lo, nxt, True
    return lo, hi, False



def _dynamic_sensor_cost(charge, energy: float, base: float, ctype: str = None) -> Optional[float]:
    """Cost from a live HA price-sensor history (Nordpool/Tibber/ENTSO-E-style dynamic
    tariffs): integrate the charge's real power curve same as TOU 'split', but price each
    interval by the sensor's value AT that instant (step-hold — these sensors update once
    an hour) instead of a static band. Falls back to the flat base price whenever the sensor
    isn't configured, HA is unreachable, or it has no history for the window (never leaves
    a charge silently uncosted just because one live lookup failed).
    `ctype` (#106): the charge type, to resolve its own per-type sensor; None = legacy single."""
    entity_id = get_dynamic_price_entity_for(ctype) if ctype else get_dynamic_price_entity()
    if not entity_id or not charge["ended_at"]:
        return round(energy * base, 2) if base else None

    import ha_client   # local: ha_client imports db_reader, so this avoids a circular import
    db = _get()
    lo, hi, excl = _power_window_bounds(db, charge["started_at"], charge["ended_at"])
    rows = db.execute(
        "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
        "WHERE charging = 1 AND recorded_at >= ? AND recorded_at " + ("<" if excl else "<=")
        + " ? ORDER BY recorded_at",
        (lo, hi),
    ).fetchall()
    samples = []
    for r in rows:
        dt = _local_dt(r["recorded_at"])
        if dt is not None:
            power = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
            samples.append((dt, power))
    if len(samples) < 2:
        return round(energy * base, 2) if base else None

    price_hist = ha_client.get_history(entity_id, lo, hi)
    if not price_hist:
        return round(energy * base, 2) if base else None

    idx, total_e, weighted = 0, 0.0, 0.0
    for (dt0, p0), (dt1, p1) in zip(samples, samples[1:]):
        hours = (dt1 - dt0).total_seconds() / 3600.0
        if hours <= 0 or hours > 0.25:   # mirrors compute_cost's TOU-split gap guard
            continue
        e = (p0 + p1) / 2.0 * hours
        if e <= 0:
            continue
        ts0 = dt0.timestamp()
        while idx + 1 < len(price_hist) and price_hist[idx + 1][0] <= ts0:
            idx += 1
        total_e += e
        weighted += e * price_hist[idx][1]

    if total_e <= 0:
        return round(energy * base, 2) if base else None
    # scale the time-weighted average price onto the authoritative (SOC) energy, same as
    # the TOU-split method, so the total stays consistent with the energy shown elsewhere.
    return round(energy * (weighted / total_e), 2)



def compute_cost(charge, config: Optional[dict] = None, ac_kwh: Optional[float] = None):
    """Cost for ONE charge using the pricing config in effect *now*. This is the
    single place a charge's cost is set, and it is frozen afterwards (no retroactive
    recompute when prices/bands change later). Returns a float (0.0 = free) or None
    when the type/price isn't known yet.
        flat        → energy × base price for the charge's type
        TOU 'start' → price of the band matching the start day+time (else base)
        TOU 'split' → energy split across bands by the real power curve, each
                      sample priced by the band matching its own day+time
        dynamic     → same power-curve split as TOU 'split', priced by a live HA
                      sensor's history instead of a static band (see _dynamic_sensor_cost)

    `ac_kwh`: for HOME charges on a configured wallbox, the caller passes the real AC energy the
    wallbox delivered (what you actually pay the utility, incl. AC→DC conversion losses). When given
    (>0) it replaces the DC SOC-energy as the billed amount; otherwise we bill the DC energy (the only
    figure we have for public/away charges). The band-weighting (timing) is unchanged — AC and DC flow
    at the same times — so only the total energy differs.
    """
    location_type = charge["location_type"]
    # `ac_kwh` (when given) is the wallbox energy the poller MEASURED for this charge — the counter
    # delta start→stop, an exact figure, not an estimate. HOME charges are billed on it; everything
    # else (and HOME without a wallbox) on the battery (DC/SoC) energy. The caller picks which.
    energy = ac_kwh if (ac_kwh and ac_kwh > 0) else (charge["energy_added_kwh"] or 0)
    if not location_type or energy <= 0:
        return None
    if location_type == "FREE":
        return 0.0

    if config is None:
        config = get_cost_config()
    prices = get_charge_prices()
    key = PRICE_KEYS.get(location_type, "")
    base_set = key in prices
    base = float(prices.get(key, 0.0) or 0.0)

    # Pricing mode PER CHARGE TYPE (#106): this charge's type picks its own mode; a config
    # without the per-type map (older caller / pre-#106 settings) falls back to the global one.
    mode = (config.get("modes") or {}).get(location_type) or config.get("mode", "flat")
    if mode == "dynamic" and not _mode_allowed(location_type, mode):
        mode = "flat"   # defense in depth — dynamic is HOME-only, whatever handed us this config

    if mode == "dynamic":
        return _dynamic_sensor_cost(charge, energy, base, ctype=location_type)

    bands = config.get("bands") or []
    if mode != "tou" or not bands:
        return round(energy * base, 2) if base else None

    def _start_band_cost():
        dt = _local_dt(charge["started_at"])
        if dt is None:
            return round(energy * base, 2) if base else None
        price, is_set = _resolve_band_price(bands, location_type,
                                            dt.weekday(), dt.hour * 60 + dt.minute,
                                            base, base_set)
        if not is_set and price == 0:
            return None
        return round(energy * price, 2)

    if config.get("method") == "start":
        return _start_band_cost()

    # An in-progress charge (no ended_at) has no integrable curve yet → price by start band.
    if not charge["ended_at"]:
        return _start_band_cost()

    # method 'split': integrate the power curve, price each interval by its band. The window
    # is capped at the next charge's start so an orphan/overlapping charge can't integrate a
    # later charge's power (which would also distort the band weighting).
    db = _get()
    lo, hi, excl = _power_window_bounds(db, charge["started_at"], charge["ended_at"])
    rows = db.execute(
        "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
        "WHERE charging = 1 AND recorded_at >= ? AND recorded_at " + ("<" if excl else "<=")
        + " ? ORDER BY recorded_at",
        (lo, hi),
    ).fetchall()
    samples = []
    for r in rows:
        dt = _local_dt(r["recorded_at"])
        if dt is not None:
            power = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
            samples.append((dt, power))

    total_e, weighted, any_set = 0.0, 0.0, False
    for (dt0, p0), (dt1, p1) in zip(samples, samples[1:]):
        hours = (dt1 - dt0).total_seconds() / 3600.0
        if hours <= 0 or hours > 0.25:   # skip non-positive AND multi-hour gaps (charger
            continue                     # paused / poll miss): never price a phantom interval
                                         # across the gap (mirrors _integrate_charge_energy_kwh)
        e = (p0 + p1) / 2.0 * hours
        if e <= 0:
            continue
        price, is_set = _resolve_band_price(bands, location_type,
                                            dt0.weekday(), dt0.hour * 60 + dt0.minute,
                                            base, base_set)
        any_set = any_set or is_set
        total_e += e
        weighted += e * price

    if total_e <= 0:               # no usable curve → fall back to the start band
        return _start_band_cost()
    if not any_set and weighted == 0:
        return None
    # scale the time-weighted average price onto the authoritative (SOC) energy,
    # so the total stays consistent with the energy shown elsewhere.
    return round(energy * (weighted / total_e), 2)



def update_charge_price(key: str, value: float) -> None:
    """Persist a base €/kWh price. Per the 'new charges only' rule, this does NOT
    retroactively recompute already-recorded charges: a charge's cost is frozen
    when its type is confirmed, and only charges confirmed from here on use the
    new price. Same goes for time-of-use band/mode edits."""
    from db.settings import set_setting
    set_setting(key, str(value))



def _wac_blend(charges) -> Optional[float]:
    """Weighted-average-cost blended €/kWh of the battery after a chronological list of PRICED
    charges (GitHub #53). Pure (no DB) so it's simulation/unit-testable: each item is a dict with
    start_soc, end_soc, cost, ac_energy_kwh, location_type, energy_added_kwh.

    Model: the battery is ONE reservoir at a blended price; only a charge moves the price,
    consumption never does → replay the charges, anchoring the mix on each charge's SoC. Capacity
    CANCELS out (SoC ratios), so this is capacity-free and robust to SoH error. Update per charge:

        p' = (start_soc·p + (end_soc − start_soc)·rate) / end_soc

    where rate = charge cost ÷ its billed energy (_billed_kwh: wallbox AC for HOME, else battery DC —
    same basis as the per-charge € and the #51 trip-rate fix). Bootstrap: the first priced charge
    sets p to its own rate (the pre-existing energy is valued at the first thing we can measure).
    Unconfirmed charges (cost=NULL) are simply ABSENT from this list → carry-forward, i.e. the blend
    is unchanged across them — Mate's framework rule "no cost until confirmed, HOME excluded"."""
    p = None
    for c in charges:
        ss, es = c.get("start_soc"), c.get("end_soc")
        if ss is None or es is None or es <= 0 or es <= ss:
            continue                         # need a real SoC rise to weight the mix
        basis = _billed_kwh(c)
        cost = c.get("cost")
        if cost is None or not basis or basis <= 0:
            continue                         # unpriced → must not move the blend
        rate = cost / basis
        if rate <= 0:
            continue
        p = rate if p is None else (ss * p + (es - ss) * rate) / es
    return p



def blended_price_at(vehicle_id: int, ts: str) -> Optional[float]:
    """Blended €/kWh of the battery (WAC, #53) for `vehicle_id` at instant `ts` — the price in
    effect for a trip starting then, set by every PRICED charge that ended at/before `ts`. None until
    the first priced charge (early trips stay uncosted, as today). Recomputed from history each call
    (no stored state) → self-corrects the moment a charge's cost is assigned/edited."""
    db = _get()
    rows = db.execute(
        "SELECT start_soc, end_soc, cost, ac_energy_kwh, location_type, energy_added_kwh "
        "FROM charges WHERE vehicle_id = ? AND ended_at IS NOT NULL AND ended_at <= ? "
        "  AND cost IS NOT NULL AND energy_added_kwh > 0 ORDER BY ended_at",
        (vehicle_id, ts),
    ).fetchall()
    return _wac_blend([dict(r) for r in rows])
