"""Database queries — trips domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import i18n
import re
import time



_READY_DEBOUNCE_S = 90        # ignore ready=0 dips shorter than this — signal blips seen in the log

_READY_LOOKBACK_S = 6 * 3600  # how far around the trip to scan positions for the session bounds

# ── Manual trip merge (reversible) ──────────────────────────────────────────────
# A merged trip is a parent + child trips (merged_into_id = parent.id), joined by the user when
# a journey was split by a SHORT, NON-charging stop. Nothing is deleted or overwritten — the group
# stats are computed on the fly, so "unmerge" restores the originals exactly.
TRIP_MERGE_GAP_DEFAULT = 5    # minutes — a stop under this is plausibly ONE continuous drive split by

                              # a brief pause (lights/gate/quick drop-off). A 15-30 min stop is a real
                              # destination = two separate trips → never auto-suggested for merge. The
                              # merge UI slider still opens up to TRIP_MERGE_GAP_MAX for manual merges.
TRIP_MERGE_GAP_MIN = 5

TRIP_MERGE_GAP_MAX = 90



def get_trip_track(trip_id: int) -> list[dict]:
    """Full ordered GPS track for one trip (for GPX export — not downsampled). Group-aware: a
    merged trip returns the union of all its segments' tracks, in chronological order."""
    db = _get()
    ids = _segment_ids(db, trip_id)
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT recorded_at, latitude, longitude, speed_kmh, soc FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]



def save_trip_note(trip_id: int, note: str,
                   drive_mode: Optional[str] = None,
                   one_pedal: Optional[int] = None) -> None:
    """#107: persist the trip user note + manual driving tags. drive_mode is one of DRIVE_MODES
    (anything else clears it); one_pedal is 1/0/None (None = not set). Empty note clears it.
    Writes to the trip id as given — the detail page already resolves a merged child to its parent."""
    from db.vehicle import DRIVE_MODES
    note = (note or "").strip()[:1000]
    dm = drive_mode if drive_mode in DRIVE_MODES else None
    op = one_pedal if one_pedal in (0, 1) else None
    db = _conn_rw()
    db.execute("UPDATE trips SET note=?, drive_mode=?, one_pedal=? WHERE id=?",
               (note or None, dm, op, trip_id))
    db.commit()



def delete_trip(trip_id: int) -> bool:
    """Permanently remove a trip and its GPS track. Returns True if a trip was deleted.
    Day/month/lifetime trip totals recompute from the DB, so they update automatically."""
    db = _conn_rw()
    # Deleting a merged trip removes the whole group (the parent + every child) and their tracks.
    ids = [trip_id] + [r["id"] for r in db.execute(
        "SELECT id FROM trips WHERE merged_into_id=?", (trip_id,)).fetchall()]
    ph = ",".join("?" * len(ids))
    cur = db.execute(f"DELETE FROM trips WHERE id IN ({ph})", ids)
    db.execute(f"DELETE FROM trip_positions WHERE trip_id IN ({ph})", ids)
    db.commit()
    return cur.rowcount > 0



# ── Phase 2: per-trip EC (driving) energy enrichment ─────────────────────────
# The cloud getEC endpoint gives the official DRIVING-energy split (Guida/AC/Altro) for a trip's
# exact window. We enrich NEW trips (after the feature's cutoff) and, when enabled, make EC the
# trip's energy — backing up the SoC value so it's fully reversible. Old trips stay SoC.

def _trip_epoch(s):
    """A stored trip timestamp (UTC ISO, possibly naive) → epoch seconds, or None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return None



def trip_epoch_window(trip: dict):
    """(begin_ts, end_ts) for a trip dict."""
    return _trip_epoch(trip.get("started_at")), _trip_epoch(trip.get("ended_at"))



def trip_ec_window(trip: dict, pad_s: int = 120):
    """Window for the getEC QUERY.

    getEC stamps a driving session's whole energy at ONE instant — the cloud anchor ≈ the real
    Ready-on (power-on). A query [begin, end] returns that energy only when begin ≤ anchor ≤ end. So:
      START = on_lo, the LAST ready=0 sample before the session (the car was provably OFF there →
              guaranteed ≤ the anchor, at ANY poll cadence). NOT sess["on"] (the first ready=1 poll):
              that can sit up to a poll interval (~30 s cold) AFTER the anchor → getEC None and the
              trip wrongly drops to SoC (#117 — verified same trip: one account caught it at
              on=08:33:09, another missed at 08:33:13, a 4 s knife-edge; on_lo=08:32:59 catches both).
              No magic pad. FALLBACK (no ready data): T0 − pad_s clamped to the previous trip midpoint.
      END   = T1, with NO padding — the energy is at the START anchor, so any end past it works; T1 is
              always past the anchor, and T1 + pad would risk the FUTURE (None) / the next trip.

    CAVEAT — the cloud's SESSION ≠ Mate's TRIP. The session runs from READY (power-on) until the car
    is switched OFF, so it can span SEVERAL Mate trips + long idle in Park (verified 22/06: trips
    133+134, the car never powered off between them → ONE session anchored at the first start; the
    second trip's window, being AFTER that anchor, returns None). Consequences: the FIRST drive after
    Ready catches the anchor and gets the WHOLE session (which may include pre-drive climate / idle /
    later drives → can over-read); a LATER drive in the same no-power-off run sits past the anchor →
    getEC returns None → the trip stays on the SoC estimate. The bigger the Ready→D gap (sitting in
    Ready with climate before shifting to D), the more likely a later trip misses it. Upstream (cloud
    session definition, not fixable here); ec_enrich._ec_implausible catches the absurd over-reads.
    Returns (begin_ts, end_ts) or (None, None)."""
    b, e = trip_epoch_window(trip)
    if not b or not e:
        return (None, None)
    # PRIMARY: begin = on_lo (last ready=0 before the session) — provably ≤ the cloud anchor at any
    # cadence, so getEC always catches it. NOT sess["on"] (first ready=1 poll), which can land a poll
    # interval AFTER the anchor → None (#117). end stays T1 (always past the start anchor).
    sess = ready_session(trip)
    if sess and sess.get("on_lo") is not None:
        return (int(sess["on_lo"]), int(e))
    # FALLBACK (no ready data, or no off-sample before the session): T0 − pad, clamped to prev midpoint.
    db = _get()
    begin = b - pad_s
    prev = db.execute(
        "SELECT MAX(ended_at) AS m FROM trips WHERE merged_into_id IS NULL "
        "AND ended_at IS NOT NULL AND ended_at < ?", (trip.get("started_at"),)).fetchone()
    if prev and prev["m"]:
        pe = _trip_epoch(prev["m"])
        if pe:
            begin = max(begin, (pe + b) // 2)
    return (int(begin), int(e))



def ready_session(trip: dict):
    """Reconstruct the car's power-on session (READY/ON3, PID 1258) that brackets this trip, from the
    per-poll `positions.ready` log. The cloud's getEC session runs from Ready-ON to power-OFF and can
    span SEVERAL Mate trips + idle (verified 22/06: trips 133+134 = one session) → this is the REAL
    getEC window AND tells us whether a trip shares its session with others.

    Returns {on, off, n_trips, trip_ids} (epoch seconds) or None when no ready data covers the trip
    (old trips before the signal existed → caller falls back to the T0−2min window). Brief ready=0
    dips shorter than _READY_DEBOUNCE_S are treated as still-on (blips)."""
    t0, t1 = _trip_epoch(trip.get("started_at")), _trip_epoch(trip.get("ended_at"))
    if not t0 or not t1:
        return None
    db = _get()
    lo = datetime.fromtimestamp(t0 - _READY_LOOKBACK_S, timezone.utc).isoformat()
    hi = datetime.fromtimestamp(t1 + _READY_LOOKBACK_S, timezone.utc).isoformat()
    rows = db.execute(
        "SELECT recorded_at, ready FROM positions WHERE recorded_at >= ? AND recorded_at <= ? "
        "ORDER BY recorded_at", (lo, hi)).fetchall()
    samples, last = [], None
    for r in rows:
        e = _trip_epoch(r["recorded_at"])
        if e is None:
            continue
        rd = r["ready"]
        rd = (last if last is not None else 0) if rd is None else rd  # carry-forward unknown
        last = rd
        samples.append((e, rd))
    if not any(rd for _, rd in samples):
        return None                          # no ready=1 anywhere → no session info
    # Build ready=1 runs, then merge runs separated by a ready=0 gap shorter than the debounce.
    runs, cur = [], None
    for e, rd in samples:
        if rd == 1:
            cur = [e, e] if cur is None else [cur[0], e]
        elif cur is not None:
            runs.append(cur); cur = None
    if cur is not None:
        runs.append(cur)
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < _READY_DEBOUNCE_S:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    # The session = the run that brackets the trip (small slack: the gear-P trip-end lags ready-off
    # by ~1 min, and ready-on can sit a poll after T0).
    sess = next(((s, e) for s, e in merged
                 if s - _READY_DEBOUNCE_S <= t0 and t1 <= e + _READY_DEBOUNCE_S), None)
    if sess is None:                         # fallback: any run overlapping the trip
        sess = next(((s, e) for s, e in merged if not (e < t0 or s > t1)), None)
    if sess is None:
        return None
    on, off = sess
    # on_lo = last ready=0 sample BEFORE the run = lower bracket of the real Ready-on. The true
    # power-on (= getEC anchor) sits between on_lo and `on` (≤ one poll interval), so on_lo is
    # provably ≤ the anchor → the safe getEC begin (see trip_ec_window). None only if the run starts
    # at the scan edge with no preceding off-sample (caller then uses its fallback).
    on_lo = max((ts for ts, rd in samples if ts < on and rd == 0), default=None)
    # Count finalized, non-merged trips whose span falls inside the session.
    olo = datetime.fromtimestamp(on - _READY_DEBOUNCE_S, timezone.utc).isoformat()
    ohi = datetime.fromtimestamp(off + _READY_DEBOUNCE_S, timezone.utc).isoformat()
    trs = db.execute(
        "SELECT id, started_at, ended_at FROM trips WHERE merged_into_id IS NULL "
        "AND ended_at IS NOT NULL AND ended_at >= ? AND started_at <= ? ORDER BY started_at",
        (olo, ohi)).fetchall()
    ids = []
    for tr in trs:
        ts0, ts1 = _trip_epoch(tr["started_at"]), _trip_epoch(tr["ended_at"])
        if ts0 and ts1 and ts0 >= on - _READY_DEBOUNCE_S and ts1 <= off + _READY_DEBOUNCE_S:
            ids.append(tr["id"])
    return {"on": int(on), "off": int(off),
            "on_lo": int(on_lo) if on_lo is not None else None,
            "n_trips": len(ids), "trip_ids": ids}



def get_trips_needing_ec(cutoff_iso: str, limit: int = 5, min_age_s: int = 600,
                         giveup_age_s: int = 6 * 3600) -> list[dict]:
    """Finalized, non-merged trips started on/after `cutoff_iso` whose cloud EC isn't STABLE yet,
    within the re-fetchable window: ended between `giveup_age_s` and `min_age_s` ago. The cloud
    aggregates a fresh trip's EC with a lag and writes it incrementally, so we keep re-reading
    (store_trip_ec overwrites with the latest) until two equal reads lock it (ec_stable=1) or it
    ages out. Returns ec_kwh too so the sweep can compare to the previous read. Skips zero-distance."""
    now = datetime.now(timezone.utc)
    not_after = (now - timedelta(seconds=min_age_s)).isoformat()      # ended_at <= this (old enough)
    not_before = (now - timedelta(seconds=giveup_age_s)).isoformat()  # ended_at >= this (not too old)
    db = _conn_rw()
    rows = db.execute(
        """SELECT id, started_at, ended_at, distance_km, ec_kwh,
                  efficiency_kwh_100km, efficiency_soc, start_soc, end_soc FROM trips
           WHERE merged_into_id IS NULL AND ended_at IS NOT NULL
             AND started_at >= ? AND ended_at <= ? AND ended_at >= ?
             AND COALESCE(ec_stable, 0) = 0 AND COALESCE(ec_tried, 0) < 80 AND distance_km > 0
           ORDER BY started_at DESC LIMIT ?""",
        (cutoff_iso, not_after, not_before, int(limit))).fetchall()
    return [dict(r) for r in rows]



def store_trip_ec(trip_id: int, ec: Optional[dict], distance_km, apply_energy: bool,
                  stable: bool = False) -> None:
    """Record an EC enrichment attempt. Always bumps ec_tried. With data: store the split + total
    (overwriting any earlier partial read), back up the SoC efficiency once, and (if apply_energy)
    override efficiency_kwh_100km with the EC-derived value. `stable=True` locks the trip
    (ec_stable=1) so the sweep stops re-fetching it."""
    db = _conn_rw()
    if not ec:
        db.execute("UPDATE trips SET ec_tried = COALESCE(ec_tried, 0) + 1 WHERE id=?", (trip_id,))
        db.commit()
        return
    drv, ac, oth, tot = ec.get("driving_kwh"), ec.get("ac_kwh"), ec.get("other_kwh"), ec.get("total_kwh")
    db.execute(
        """UPDATE trips SET ec_tried = COALESCE(ec_tried, 0) + 1,
               ec_kwh=?, ec_driving=?, ec_ac=?, ec_other=?, ec_stable=?
           WHERE id=?""",
        (tot, drv, ac, oth, 1 if stable else 0, trip_id))
    # Override the trip's energy/efficiency only once the EC is STABLE — a fresh trip's cloud value
    # is written incrementally, so applying an early partial read would show a wrong figure. Back up
    # the SoC efficiency at the same moment so the override stays exactly reversible.
    if apply_energy and stable and tot and distance_km and distance_km > 0:
        db.execute(
            """UPDATE trips SET efficiency_soc = COALESCE(efficiency_soc, efficiency_kwh_100km),
                   efficiency_kwh_100km=? WHERE id=?""",
            (round(tot / distance_km * 100, 1), trip_id))
    db.commit()



def apply_ec_trip_energy() -> int:
    """Flag ON: make EC the energy for every trip that has EC data (backing up SoC first)."""
    db = _conn_rw()
    cur = db.execute(
        """UPDATE trips SET efficiency_soc = COALESCE(efficiency_soc, efficiency_kwh_100km),
               efficiency_kwh_100km = ROUND(ec_kwh / distance_km * 100, 1)
           WHERE ec_kwh IS NOT NULL AND ec_stable = 1 AND distance_km > 0""")
    db.commit()
    return cur.rowcount



def revert_ec_trip_energy() -> int:
    """Flag OFF: restore the original SoC efficiency for every overridden trip."""
    db = _conn_rw()
    cur = db.execute(
        "UPDATE trips SET efficiency_kwh_100km = efficiency_soc WHERE efficiency_soc IS NOT NULL")
    db.commit()
    return cur.rowcount



def revert_trip_ec(trip_id: int) -> bool:
    """Undo ONE trip's getEC conversion ('Revert to estimate' button): restore the SoC efficiency
    backed up at apply time, drop the EC split, and clear the lock so the trip shows the estimate
    again (and the Convert button comes back). ec_tried is parked at the sweep's give-up threshold
    (see get_trips_needing_ec: `ec_tried < 80`) so the background sweep won't silently re-convert a
    trip the user explicitly reverted — a manual Convert still works (convert_trip ignores ec_tried).
    Only touches trips that were actually converted (efficiency_soc set). Returns True if reverted."""
    db = _conn_rw()
    cur = db.execute(
        """UPDATE trips
              SET efficiency_kwh_100km = COALESCE(efficiency_soc, efficiency_kwh_100km),
                  ec_kwh = NULL, ec_driving = NULL, ec_ac = NULL, ec_other = NULL,
                  ec_stable = 0, ec_tried = 80
            WHERE id = ? AND efficiency_soc IS NOT NULL""",
        (trip_id,))
    db.commit()
    return cur.rowcount > 0



def _gap_minutes(end_iso, start_iso):
    """Minutes from end_iso to start_iso (raw stored UTC ISO). None if unparseable."""
    try:
        return (datetime.fromisoformat(start_iso) - datetime.fromisoformat(end_iso)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None



def _children_by_parent(db) -> dict:
    """All merged child trips grouped by parent id (one query)."""
    out: dict = {}
    for r in db.execute("SELECT * FROM trips WHERE merged_into_id IS NOT NULL").fetchall():
        out.setdefault(r["merged_into_id"], []).append(dict(r))
    return out



def _segment_ids(db, trip_id: int) -> list:
    """Every trip id in the merge-group containing trip_id (parent + children); [trip_id] if none."""
    row = db.execute("SELECT id, merged_into_id FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        return [trip_id]
    parent = row["merged_into_id"] or row["id"]
    return [parent] + [r["id"] for r in
            db.execute("SELECT id FROM trips WHERE merged_into_id=?", (parent,)).fetchall()]



def _trip_group_stats(parent: dict, children: list) -> dict:
    """Parent dict enriched with the combined stats of [parent + children] (earliest start →
    latest end). Pure display math — stored rows are untouched. The merge guard guarantees no
    charge in any gap, so the SoC delta (energy/efficiency) stays valid."""
    from db.health import get_battery_capacity_kwh
    d = dict(parent)
    d["merged_count"] = 1
    d["is_merged"] = False
    if not children:
        return d
    segs = sorted([parent, *children], key=lambda t: t.get("started_at") or "")
    first, last = segs[0], segs[-1]
    d["started_at"], d["start_soc"] = first.get("started_at"), first.get("start_soc")
    d["start_odometer_km"] = first.get("start_odometer_km")
    d["start_lat"], d["start_lon"] = first.get("start_lat"), first.get("start_lon")
    d["ended_at"], d["end_soc"] = last.get("ended_at"), last.get("end_soc")
    d["end_odometer_km"] = last.get("end_odometer_km")
    d["end_lat"], d["end_lon"] = last.get("end_lat"), last.get("end_lon")
    so, eo = first.get("start_odometer_km"), last.get("end_odometer_km")
    if so is not None and eo is not None and eo >= so and so > 0:
        d["distance_km"] = round(eo - so, 2)
    else:
        d["distance_km"] = round(sum((s.get("distance_km") or 0) for s in segs), 2)
    d["duration_min"] = round(sum((s.get("duration_min") or 0) for s in segs), 1)   # DRIVING only
    d["regen_kwh"] = round(sum((s.get("regen_kwh") or 0) for s in segs), 3)
    ssoc, esoc, dist = d["start_soc"], d["end_soc"], d.get("distance_km") or 0
    if ssoc is not None and esoc is not None and dist > 0:
        energy = max((ssoc - esoc) / 100.0 * get_battery_capacity_kwh(), 0)
        d["efficiency_kwh_100km"] = round(energy / dist * 100, 1) if energy > 0 else None
    # If the group was converted to the official cloud EC (stored on the parent over the COMBINED
    # distance, e.g. convert-on-merge), prefer it over the SoC estimate so the headline matches the
    # breakdown card.
    if d.get("ec_stable") and d.get("ec_kwh") and dist > 0:
        d["efficiency_kwh_100km"] = round(d["ec_kwh"] / dist * 100, 1)
    d["merged_count"] = len(segs)
    d["is_merged"] = True
    d["segment_ids"] = [s["id"] for s in segs]
    return d



def get_mergeable_pairs(gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> list:
    """Eligible adjacent top-level trip pairs for the merge UI: B starts within gap_min of A's
    (group) end AND B's start SoC is not higher than A's end SoC (a SoC rise = a charge in the
    gap → never mergeable). Returns [{a_id, b_id, gap_min}]."""
    db = _get()
    kids = _children_by_parent(db)
    tops = [dict(r) for r in db.execute(
        "SELECT * FROM trips WHERE merged_into_id IS NULL AND ended_at IS NOT NULL "
        "ORDER BY started_at").fetchall()]
    groups = [_trip_group_stats(t, kids.get(t["id"], [])) for t in tops]
    pairs = []
    for a, b in zip(groups, groups[1:]):
        gap = _gap_minutes(a.get("ended_at"), b.get("started_at"))
        if gap is None or gap < 0 or gap >= gap_min:
            continue
        if (a.get("end_soc") is not None and b.get("start_soc") is not None
                and b["start_soc"] > a["end_soc"]):
            continue   # SoC rose → charged in the gap
        pairs.append({"a_id": a["id"], "b_id": b["id"], "gap_min": round(gap)})
    return pairs



def merge_trips(parent_id: int, child_id: int, gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> dict:
    """Merge child into parent (the earlier of the two becomes the parent). Re-validates the
    eligibility server-side. Reversible: only sets merged_into_id, nothing is overwritten."""
    db = _conn_rw()
    a = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (parent_id,)).fetchone()
    b = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (child_id,)).fetchone()
    if not a or not b:
        return {"ok": False, "error": "not_found_or_already_merged"}
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a                                   # parent = earlier trip
    kids = _children_by_parent(db)
    a_grp = _trip_group_stats(a, kids.get(a["id"], []))
    gap = _gap_minutes(a_grp.get("ended_at"), b.get("started_at"))
    if gap is None or gap < 0:
        return {"ok": False, "error": "gap_too_large"}
    if gap >= gap_min:
        # Normally a stop ≥ gap_min is a separate trip. EXCEPTION: if the two trips share ONE power-on
        # (Ready) session — the car was never switched off between them — the cloud bundles them into
        # one driving session anyway, so allow the merge at ANY gap (the only way to get the official
        # combined figure). Detected from the real positions.ready log.
        sess = ready_session(a_grp)
        if not (sess and b["id"] in sess.get("trip_ids", [])):
            return {"ok": False, "error": "gap_too_large"}
    if (a_grp.get("end_soc") is not None and b.get("start_soc") is not None
            and b["start_soc"] > a_grp["end_soc"]):
        return {"ok": False, "error": "soc_rose_charge_in_gap"}
    # absorb B and any of B's own children into A (flatten the chain so all point to A)
    db.execute("UPDATE trips SET merged_into_id=? WHERE id=? OR merged_into_id=?",
               (a["id"], b["id"], b["id"]))
    db.commit()
    return {"ok": True, "parent_id": a["id"]}



def unmerge_trip(parent_id: int) -> dict:
    """Split a merged group back into its original trips — clears merged_into_id on every child.
    All rows were untouched, so they reappear exactly as before."""
    db = _conn_rw()
    cur = db.execute("UPDATE trips SET merged_into_id=NULL WHERE merged_into_id=?", (parent_id,))
    # The parent may hold the COMBINED cloud EC (from a convert-on-merge); once split it no longer
    # matches the standalone trip → drop it and restore the SoC efficiency (the user can re-convert
    # the standalone trip). Only touches a parent that actually carries an EC override.
    db.execute(
        "UPDATE trips SET efficiency_kwh_100km=COALESCE(efficiency_soc, efficiency_kwh_100km), "
        "efficiency_soc=NULL, ec_kwh=NULL, ec_driving=NULL, ec_ac=NULL, ec_other=NULL, ec_stable=0 "
        "WHERE id=? AND ec_kwh IS NOT NULL", (parent_id,))
    db.commit()
    return {"ok": True, "restored": cur.rowcount}



def preview_merge(parent_id: int, child_id: int) -> Optional[dict]:
    """Group stats the merge WOULD produce (for the confirm dialog), without committing."""
    db = _get()
    a = db.execute("SELECT * FROM trips WHERE id=?", (parent_id,)).fetchone()
    b = db.execute("SELECT * FROM trips WHERE id=?", (child_id,)).fetchone()
    if not a or not b:
        return None
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a
    kids = _children_by_parent(db)
    children = kids.get(a["id"], []) + [b] + kids.get(b["id"], [])
    g = _trip_group_stats(a, children)
    drive = g.get("duration_min") or 0
    elapsed = _gap_minutes(g.get("started_at"), g.get("ended_at"))
    g["stop_min"] = round(max(elapsed - drive, 0)) if elapsed is not None else None
    g["started_at"] = _local_iso(g.get("started_at"))
    g["ended_at"] = _local_iso(g.get("ended_at"))
    return g



def get_merge_preview_route(a_id: int, b_id: int, max_points: int = 120) -> list[dict]:
    """Downsampled union GPS track of the two trips' groups — for the merge-preview thumbnail."""
    db = _get()
    ids = list(dict.fromkeys(_segment_ids(db, a_id) + _segment_ids(db, b_id)))
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT latitude, longitude FROM trip_positions WHERE trip_id IN ({ph}) "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY recorded_at, id", ids).fetchall()
    pts = [dict(r) for r in rows]
    if len(pts) <= max_points:
        return pts
    step = len(pts) / max_points
    out = [pts[int(i * step)] for i in range(max_points)]
    out[-1] = pts[-1]
    return out



def get_trips(limit: int = 500) -> list[dict]:
    db = _get()
    kids = _children_by_parent(db)
    rows = db.execute(
        """SELECT * FROM trips
           WHERE ended_at IS NOT NULL AND merged_into_id IS NULL
           ORDER BY started_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_trip_group_stats(dict(r), kids.get(r["id"], [])) for r in rows]



def get_trips_grouped() -> list[dict]:
    """Return trips nested as year → month → day for the sidebar tree view."""
    from db.costs import _wac_blend
    from db.settings import get_language, get_setting
    trips = get_trips()
    from collections import OrderedDict

    def _node(label):
        return {"label": label, "km": 0, "count": 0, "regen": 0.0, "cost": 0.0,
                "_eff_wsum": 0.0, "_eff_wdist": 0.0, "avg_eff": None}

    def _add(node, km, eff, regen, cost):
        node["km"]    = round(node["km"] + km, 2)
        node["count"] += 1
        node["regen"] = round(node["regen"] + (regen or 0), 3)
        node["cost"]  = round(node["cost"] + (cost or 0), 2)
        if eff and km > 0:
            node["_eff_wsum"]  += km * eff
            node["_eff_wdist"] += km

    def _finalize(node):
        if node["_eff_wdist"] > 0:
            node["avg_eff"] = round(node["_eff_wsum"] / node["_eff_wdist"], 1)

    lang = get_language()
    # Provisional-SoC marker per trip (same rule as get_trip_detail) so the list shows which trips are
    # still waiting for the official cloud value. Settings read once, not per trip.
    _ec_on = get_setting("ec_trip_energy_enabled", "1") == "1"
    _ec_cutoff = get_setting("ec_trip_since", "")
    _now_ts = datetime.now(timezone.utc).timestamp()
    # Cost per group = Σ per-trip cost, each at the battery's blended €/kWh AT the trip's time (#53,
    # same basis as get_trip_detail). The blend only moves when a PRICED charge ends, so build that
    # (ended_at → blended price) timeline ONCE per vehicle instead of calling blended_price_at per trip.
    _cost_bp: dict = {}
    _seen_ch: dict = {}
    for _c in _get().execute(
            "SELECT vehicle_id, ended_at, start_soc, end_soc, cost, ac_energy_kwh, location_type, "
            "energy_added_kwh FROM charges WHERE ended_at IS NOT NULL AND cost IS NOT NULL "
            "AND energy_added_kwh > 0 ORDER BY vehicle_id, ended_at").fetchall():
        _seen_ch.setdefault(_c["vehicle_id"], []).append(dict(_c))
        _cost_bp.setdefault(_c["vehicle_id"], []).append(
            (_c["ended_at"], _wac_blend(_seen_ch[_c["vehicle_id"]])))

    def _rate_at(vehicle_id, ts_utc):
        """Blended €/kWh in effect at ts_utc = the last breakpoint whose charge ended at/before it."""
        rate = None
        for ended_at, wac in _cost_bp.get(vehicle_id, ()):   # ascending → last ≤ ts wins
            if ended_at <= ts_utc:
                rate = wac
            else:
                break
        return rate

    years: dict = OrderedDict()
    for t in trips:
        if not t.get("started_at"):
            continue
        dt = _local_dt(t["started_at"])
        if dt is None:
            continue
        # ec_pending + cost rate must use the RAW (UTC) started_at — capture before the local rewrite.
        _raw_start = t["started_at"]
        _ee = _trip_epoch(t.get("ended_at")) if t.get("ended_at") else None
        t["ec_pending"] = bool(
            _ec_on and not t.get("ec_stable") and _ec_cutoff
            and t["started_at"] >= _ec_cutoff
            and _ee and (_now_ts - _ee) < 6 * 3600)
        # Rewrite to local-time ISO so the template (started_at[11:16]) shows local
        t["started_at"] = dt.isoformat()
        t["ended_at"] = _local_iso(t.get("ended_at"))

        yr  = dt.strftime("%Y")
        mo  = i18n.fmt_month_year(lang, dt)
        day = i18n.fmt_day_month_year(lang, dt)

        years.setdefault(yr, {**_node(yr), "months": OrderedDict()})
        years[yr]["months"].setdefault(mo, {**_node(mo), "days": OrderedDict()})
        years[yr]["months"][mo]["days"].setdefault(day, {**_node(day), "trips": []})

        years[yr]["months"][mo]["days"][day]["trips"].append(t)

        km  = t.get("distance_km") or 0
        eff = t.get("efficiency_kwh_100km")
        regen = t.get("regen_kwh") or 0
        energy = (eff * km / 100) if (eff and km) else 0
        rate = _rate_at(t.get("vehicle_id"), _raw_start) if energy else None
        cost = (energy * rate) if (energy and rate) else 0
        for node in [years[yr], years[yr]["months"][mo], years[yr]["months"][mo]["days"][day]]:
            _add(node, km, eff, regen, cost)

    # Compute weighted avg efficiency for every node
    for yr_node in years.values():
        _finalize(yr_node)
        for mo_node in yr_node["months"].values():
            _finalize(mo_node)
            for day_node in mo_node["days"].values():
                _finalize(day_node)

    return list(years.values())



def get_trips_summary() -> dict:
    """Grand totals for the trips dashboard hero (no extra polling — pure SQL).

    Values are returned RAW, with no rounding — the template decides how to
    display them. avg_eff is a weighted mean (an inherently fractional ratio)."""
    db = _get()
    r = db.execute(
        """SELECT SUM(CASE WHEN merged_into_id IS NULL THEN 1 ELSE 0 END) AS n,
                  COALESCE(SUM(distance_km), 0)              AS km,
                  COALESCE(SUM(regen_kwh), 0)                AS regen,
                  SUM(distance_km * efficiency_kwh_100km)    AS eff_wsum,
                  SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                           THEN distance_km END)             AS eff_wdist
           FROM trips WHERE ended_at IS NOT NULL"""
    ).fetchone()
    return {
        "count":    r["n"],
        "km":       r["km"] or 0,
        "regen":    r["regen"] or 0,
        "avg_eff":  (r["eff_wsum"] / r["eff_wdist"]) if r["eff_wdist"] else None,
    }



def get_first_trip_date() -> Optional[str]:
    """Earliest trip date (YYYY-MM-DD, local) — the lower bound for the 'all-time' EC window on the
    Trips page. None if there are no trips yet."""
    db = _get()
    r = db.execute("SELECT MIN(started_at) AS m FROM trips WHERE started_at IS NOT NULL").fetchone()
    if not r or not r["m"]:
        return None
    return (_local_iso(r["m"]) or r["m"])[:10]



def get_first_trip_ts() -> Optional[int]:
    """Epoch seconds of the earliest recorded trip's start — the lower bound of Mate's LOCAL trip
    coverage. Cloud getEC windows can reach back to the car's first day (long before Mate was
    installed), so callers pairing local trip totals with a getEC total use this to detect when
    the two do NOT cover the same span (GitHub #105). None if there are no trips yet."""
    db = _get()
    r = db.execute("SELECT MIN(started_at) AS m FROM trips WHERE started_at IS NOT NULL").fetchone()
    dt = _local_dt(r["m"]) if r else None
    return int(dt.timestamp()) if dt else None



def get_trip_detail(trip_id: int) -> Optional[dict]:
    from db.costs import blended_price_at
    from db.settings import get_setting
    db = _get()
    row = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not row:
        return None
    # A merged child resolves to (and shows) its parent group.
    parent_id = row["merged_into_id"] or row["id"]
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (parent_id,)).fetchone()
    children = _children_by_parent(db).get(parent_id, [])
    seg_ids = _segment_ids(db, parent_id)
    ph = ",".join("?" * len(seg_ids))
    positions = db.execute(
        "SELECT recorded_at, latitude, longitude, speed_kmh, soc FROM trip_positions "
        f"WHERE trip_id IN ({ph}) ORDER BY recorded_at, id",
        seg_ids,
    ).fetchall()
    trip_d = _trip_group_stats(dict(trip), children)
    if trip_d.get("is_merged"):
        elapsed = _gap_minutes(trip_d.get("started_at"), trip_d.get("ended_at"))
        trip_d["stop_min"] = (round(max(elapsed - (trip_d.get("duration_min") or 0), 0))
                              if elapsed is not None else None)
    trip_d["started_at"] = _local_iso(trip_d.get("started_at"))
    trip_d["ended_at"] = _local_iso(trip_d.get("ended_at"))

    # #107: per-trip user note + manual driving tags — read from the parent row (the detail page
    # always shows the parent, so the note/tags saved against it are the ones edited here).
    _tp = dict(trip)
    trip_d["note"] = _tp.get("note")
    trip_d["drive_mode"] = _tp.get("drive_mode")
    trip_d["one_pedal"] = _tp.get("one_pedal")

    # Speed stats derived from the GPS track (speed_kmh per point).
    speeds = [p["speed_kmh"] for p in positions if p["speed_kmh"] is not None]
    trip_d["max_speed_kmh"] = round(max(speeds)) if speeds else None
    # Average over moving points only (>1 km/h) so long idle stretches don't skew it.
    moving = [s for s in speeds if s > 1]
    trip_d["avg_speed_kmh"] = round(sum(moving) / len(moving)) if moving else None

    # ── #18: total energy consumed + trip cost ──────────────────────────────────
    # Energy consumed = efficiency × distance / 100 (consistent with the stored efficiency).
    eff = trip_d.get("efficiency_kwh_100km")
    dist = trip_d.get("distance_km") or 0
    trip_d["energy_kwh"] = round(eff * dist / 100, 2) if (eff and dist) else None
    # Cost = trip energy × the battery's BLENDED €/kWh at the trip's start (weighted-average-cost,
    # GitHub #53). Replaces the old "rate of the single last charge", which over-billed every trip
    # after an expensive top-up (a small public charge made all the cheaper home energy bill at the
    # premium rate). The blend mixes every PRICED charge by the energy it added (blended_price_at /
    # _wac_blend); unconfirmed charges don't move it (Mate's "no cost until confirmed, HOME excluded").
    # Stores the number only — the `money` filter applies the currency. Final trip cost → 2 decimals.
    trip_d["cost"] = None
    trip_d["cost_per_kwh"] = None
    if trip_d["energy_kwh"]:
        rate = blended_price_at(trip["vehicle_id"], trip["started_at"])
        if rate and rate > 0:
            trip_d["cost_per_kwh"] = round(rate, 4)
            trip_d["cost"] = round(trip_d["energy_kwh"] * rate, 2)

    # Provisional-SoC marker: a getEC-candidate trip (feature on, started on/after the cutoff) whose
    # official cloud value hasn't locked yet is showing the SoC ESTIMATE for energy/efficiency/cost.
    # Flag it so the UI can label it "provisional — waiting for cloud" instead of looking like a final
    # (and slightly imprecise) number. Only while still inside the enrichment retry window (~6h); older
    # trips the cloud never enriched stay plain SoC with no "waiting" claim.
    trip_d["ec_pending"] = False
    try:
        if get_setting("ec_trip_energy_enabled", "1") == "1" and not trip_d.get("ec_stable"):
            cutoff = get_setting("ec_trip_since", "")
            sa, ea = trip["started_at"], trip["ended_at"]
            ee = _trip_epoch(ea) if ea else None
            if cutoff and sa and sa >= cutoff and ee and \
                    (datetime.now(timezone.utc).timestamp() - ee) < 6 * 3600:
                trip_d["ec_pending"] = True
    except Exception:  # noqa: BLE001
        pass

    return {
        **trip_d,
        "positions": [dict(p) for p in positions],
    }



def get_trip_route(trip_id: int, max_points: int = 80) -> list[dict]:
    """Lat/lon track for a single trip, downsampled to at most ``max_points``
    points — used to draw the lightweight route thumbnail in the trips list."""
    db = _get()
    ids = _segment_ids(db, trip_id)
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT latitude, longitude FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id",
        ids,
    ).fetchall()
    pts = [dict(r) for r in rows]
    if len(pts) <= max_points:
        return pts
    step = len(pts) / max_points
    sampled = [pts[int(i * step)] for i in range(max_points)]
    sampled[-1] = pts[-1]  # always keep the real end point
    return sampled



def get_trip_totals_between(begin_ts: int, end_ts: int) -> dict:
    """Distance/duration/count of LOCAL trips started within [begin_ts, end_ts] (epoch seconds) —
    paired by the caller with a live getEC total for the SAME window, to show distance + average
    kWh/100km alongside the official split (mirrors the car's own "since last charge" screen, which
    shows Distanza/Durata/Media next to the same Guida/AC/Altro breakdown)."""
    b = datetime.fromtimestamp(begin_ts, tz=timezone.utc).isoformat()
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
    db = _get()
    row = db.execute(
        """SELECT COUNT(*) AS trip_count,
                  ROUND(SUM(distance_km), 2) AS distance_km,
                  ROUND(SUM(duration_min), 0) AS duration_min
           FROM trips WHERE ended_at IS NOT NULL AND started_at >= ? AND started_at <= ?""",
        (b, e),
    ).fetchone()
    return dict(row) if row else {}
