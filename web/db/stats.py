"""Database queries — stats domain."""
from db import _get, _conn_rw, _local_dt, _local_iso, DB_PATH, _LOCAL_TZ, _iso_to_utc, _billed_kwh
import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import i18n
import time



def get_stats_grouped() -> list[dict]:
    """Trip stats nested as year → month → day (aggregated, no individual trips)."""
    from db.settings import get_language
    from collections import OrderedDict
    db = _get()
    rows = db.execute("""
        SELECT
            strftime('%Y', started_at)    AS year,
            strftime('%Y-%m', started_at) AS month_key,
            date(started_at)              AS day_key,
            COUNT(*)                      AS trip_count,
            ROUND(SUM(distance_km), 2)    AS total_km,
            ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km, 0) / 100), 2) AS total_kwh,
            ROUND(
                SUM(distance_km * COALESCE(efficiency_kwh_100km, 0) / 100) /
                NULLIF(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                               THEN distance_km END), 0) * 100, 1
            ) AS avg_efficiency,
            ROUND(SUM(regen_kwh), 2) AS total_regen_kwh
        FROM trips
        WHERE ended_at IS NOT NULL
        GROUP BY year, month_key, day_key
        ORDER BY started_at DESC
    """).fetchall()

    lang = get_language()
    years: dict = OrderedDict()
    for r in rows:
        d = dict(r)
        yr, mo_key, day_key = d["year"], d["month_key"], d["day_key"]

        # Localize labels in Python (SQLite %B/%b not supported; strftime is English-only)
        try:
            mo_dt  = datetime.strptime(mo_key, "%Y-%m")
            mo_label = i18n.fmt_month_year(lang, mo_dt)
            day_dt   = datetime.strptime(day_key, "%Y-%m-%d")
            d["day_label"] = i18n.fmt_day_month_year(lang, day_dt)
        except Exception:
            mo_label = mo_key
            d["day_label"] = day_key

        if yr not in years:
            years[yr] = {"label": yr, "trip_count": 0, "total_km": 0.0,
                         "total_kwh": 0.0, "total_regen_kwh": 0.0,
                         "_ws": 0.0, "_wd": 0.0,
                         "avg_efficiency": None, "months": OrderedDict()}
        if mo_key not in years[yr]["months"]:
            years[yr]["months"][mo_key] = {"label": mo_label, "trip_count": 0,
                                           "total_km": 0.0, "total_kwh": 0.0,
                                           "total_regen_kwh": 0.0,
                                           "_ws": 0.0, "_wd": 0.0,
                                           "avg_efficiency": None, "days": []}

        years[yr]["months"][mo_key]["days"].append(d)

        km  = d.get("total_km") or 0
        eff = d.get("avg_efficiency")
        for node in (years[yr], years[yr]["months"][mo_key]):
            node["trip_count"]      += d["trip_count"]
            node["total_km"]         = round(node["total_km"] + km, 2)
            node["total_kwh"]        = round(node["total_kwh"] + (d.get("total_kwh") or 0), 2)
            node["total_regen_kwh"]  = round(node["total_regen_kwh"] + (d.get("total_regen_kwh") or 0), 2)
            if eff and km > 0:
                node["_ws"] += km * eff
                node["_wd"] += km

    for yr_node in years.values():
        if yr_node["_wd"] > 0:
            yr_node["avg_efficiency"] = round(yr_node["_ws"] / yr_node["_wd"], 1)
        for mo_node in yr_node["months"].values():
            if mo_node["_wd"] > 0:
                mo_node["avg_efficiency"] = round(mo_node["_ws"] / mo_node["_wd"], 1)
            mo_node["trips"] = []

    # Attach individual trips (chronological ASC) to each month for per-trip charts
    db2 = _get()
    trip_rows = db2.execute(
        """SELECT id, started_at, distance_km, efficiency_kwh_100km, regen_kwh
           FROM trips WHERE ended_at IS NOT NULL ORDER BY started_at ASC"""
    ).fetchall()
    for r in trip_rows:
        t = dict(r)
        if not t.get("started_at"):
            continue
        dt = _local_dt(t["started_at"])
        if dt is None:
            continue
        yr, mo_key = dt.strftime("%Y"), dt.strftime("%Y-%m")
        t["label"] = dt.strftime("%d/%m %H:%M")
        if yr in years and mo_key in years[yr]["months"]:
            years[yr]["months"][mo_key]["trips"].append(t)

    return list(years.values())



def get_monthly_stats() -> list[dict]:
    db = _get()
    rows = db.execute(
        """SELECT
               strftime('%Y-%m', started_at) AS month,
               COUNT(*)                       AS trip_count,
               ROUND(SUM(distance_km), 2)     AS total_km,
               ROUND(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                              THEN distance_km END), 2) AS km_with_eff,
               ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km,0) / 100), 2) AS total_kwh,
               ROUND(AVG(efficiency_kwh_100km), 1) AS avg_efficiency
           FROM trips
           WHERE ended_at IS NOT NULL
           GROUP BY month
           ORDER BY month DESC
           LIMIT 12""",
    ).fetchall()
    return [dict(r) for r in rows]



def get_stats_summary() -> dict:
    db = _get()
    trips = db.execute(
        """SELECT
               COUNT(*)                                                       AS trip_count,
               ROUND(SUM(distance_km), 2)                                    AS total_km,
               ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km,0)/100), 2) AS total_kwh_used,
               ROUND(SUM(duration_min), 0)                                   AS total_drive_min,
               -- distance-weighted = total energy / total distance (#42): a simple AVG
               -- over-weights short trips and disagreed with both the Trips-page header
               -- and this page's own "energy used ÷ distance". Matches get_trips_summary.
               ROUND(SUM(distance_km * efficiency_kwh_100km) /
                     NULLIF(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                                     THEN distance_km END), 0), 1)           AS avg_efficiency,
               -- "Best" must come from a real trip, not a 3 km downhill coast or a glitch frame
               -- (#86): a min-distance floor keeps this metric representative of the car.
               ROUND(MIN(CASE WHEN efficiency_kwh_100km > 0 AND distance_km >= 15
                              THEN efficiency_kwh_100km END), 1) AS best_efficiency,
               ROUND(SUM(regen_kwh), 2)                                      AS total_regen_kwh,
               ROUND(AVG(regen_kwh), 2)                                      AS avg_regen_kwh
           FROM trips WHERE ended_at IS NOT NULL"""
    ).fetchone()
    charges = db.execute(
        """SELECT
               COUNT(*)                         AS charge_count,
               ROUND(SUM(energy_added_kwh), 2)  AS total_kwh_charged,
               ROUND(SUM(cost), 2)              AS total_cost
           FROM charges WHERE ended_at IS NOT NULL"""
    ).fetchone()
    t = dict(trips) if trips else {}
    c = dict(charges) if charges else {}
    total_kwh = t.get("total_kwh_used") or 0
    total_regen = t.get("total_regen_kwh") or 0
    t["regen_pct"] = round(total_regen / total_kwh * 100, 1) if total_kwh > 0 else None
    return {**t, **c}



def get_charge_stats() -> dict:
    db = _get()
    row = db.execute(
        """SELECT
               COUNT(*)                            AS session_count,
               -- billed energy: wallbox AC for HOME w/ a reading, else battery DC (mirrors _billed_kwh)
               ROUND(SUM(CASE WHEN location_type='HOME' AND ac_energy_kwh IS NOT NULL AND ac_energy_kwh > 0
                              THEN ac_energy_kwh ELSE energy_added_kwh END), 2)  AS total_kwh,
               ROUND(AVG(duration_min / 60.0), 1) AS avg_duration_h,
               ROUND(SUM(cost), 2)                AS total_cost,
               ROUND(AVG(end_soc - start_soc), 1) AS avg_soc_delta,
               ROUND(MAX(max_power_kw), 2)        AS peak_power_kw
           FROM charges
           WHERE ended_at IS NOT NULL"""
    ).fetchone()
    return dict(row) if row else {}



def get_ac_dc_stats() -> dict:
    """Count + energy of AC vs DC charge sessions. DC = charge_type 'DC', or (when not
    set) a measured peak power above 11 kW (AC tops out at ~11 kW; DC is faster)."""
    db = _get()
    rows = db.execute(
        "SELECT charge_type, max_power_kw, energy_added_kwh FROM charges WHERE ended_at IS NOT NULL"
    ).fetchall()
    ac = {"count": 0, "kwh": 0.0}
    dc = {"count": 0, "kwh": 0.0}
    for r in rows:
        ct = r["charge_type"]
        is_dc = ct == "DC" or (ct is None and (r["max_power_kw"] or 0) > 11)
        b = dc if is_dc else ac
        b["count"] += 1
        b["kwh"] += r["energy_added_kwh"] or 0
    ac["kwh"] = round(ac["kwh"], 2)
    dc["kwh"] = round(dc["kwh"], 2)
    return {"ac": ac, "dc": dc, "total": ac["count"] + dc["count"]}



# ── Monthly report (driving + charging + cost, one month) ──────────────────────


def _month_shift(month_key: str, delta: int) -> str:
    """'YYYY-MM' shifted by `delta` calendar months (delta may be negative)."""
    y, m = int(month_key[:4]), int(month_key[5:7])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"



def _report_bucket() -> dict:
    return {
        "trip_count": 0, "total_km": 0.0, "total_kwh_used": 0.0,
        "regen_kwh": 0.0, "drive_min": 0.0,
        "_eff_wsum": 0.0, "_eff_wdist": 0.0, "avg_efficiency": None,
        "charge_count": 0, "charge_kwh": 0.0, "charge_cost": 0.0, "has_cost": False,
        "unconfirmed": 0,
        "home":   {"count": 0, "kwh": 0.0, "cost": 0.0},
        "public": {"count": 0, "kwh": 0.0, "cost": 0.0},
        "_days": {},   # day-of-month -> {"km": float, "cost": float}
    }



def _collect_monthly_buckets() -> dict:
    """Bucket every trip and charge into its LOCAL 'YYYY-MM'. One pass, reused for the
    selected month, the previous month (deltas) and the month list (navigation). Trips come
    from get_trips() (merged-aware, same as the Trips page); charges carry the frozen per-row
    cost and the billed-kWh basis (_billed_kwh) so the report's € matches the Charges page."""
    from db.charges import get_charges
    from db.trips import get_trips
    buckets: dict = {}

    for tr in get_trips(limit=1_000_000):
        dt = _local_dt(tr.get("started_at"))
        if dt is None:
            continue
        b = buckets.setdefault(dt.strftime("%Y-%m"), _report_bucket())
        km  = tr.get("distance_km") or 0
        eff = tr.get("efficiency_kwh_100km")
        b["trip_count"]     += 1
        b["total_km"]       += km
        b["total_kwh_used"] += km * (eff or 0) / 100.0
        b["regen_kwh"]      += tr.get("regen_kwh") or 0
        b["drive_min"]      += tr.get("duration_min") or 0
        if eff and km > 0:
            b["_eff_wsum"]  += km * eff
            b["_eff_wdist"] += km
        b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["km"] += km

    for c in get_charges(limit=1_000_000):
        dt = _local_dt(c.get("started_at"))
        if dt is None:
            continue
        b = buckets.setdefault(dt.strftime("%Y-%m"), _report_bucket())
        kwh  = _billed_kwh(c)
        cost = c.get("cost")
        lt   = c.get("location_type")
        b["charge_count"] += 1
        b["charge_kwh"]   += kwh
        if cost is not None:
            b["charge_cost"] += cost
            b["has_cost"]     = True
        grp = b["home"] if lt == "HOME" else (b["public"] if lt else None)
        if grp is not None:
            grp["count"] += 1
            grp["kwh"]   += kwh
            if cost is not None:
                grp["cost"] += cost
        else:
            b["unconfirmed"] += 1   # untyped charge: counted in totals, left out of the split
        if cost is not None:
            b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["cost"] += cost

    for b in buckets.values():
        if b["_eff_wdist"] > 0:
            b["avg_efficiency"] = round(b["_eff_wsum"] / b["_eff_wdist"], 1)
        for k in ("total_km", "total_kwh_used", "regen_kwh", "charge_kwh", "charge_cost"):
            b[k] = round(b[k], 2)
        b["drive_min"] = int(round(b["drive_min"]))
        for g in ("home", "public"):
            b[g]["kwh"]  = round(b[g]["kwh"], 2)
            b[g]["cost"] = round(b[g]["cost"], 2)
    return buckets



def get_monthly_report(month: Optional[str] = None) -> dict:
    """One-month digest combining driving, charging and cost, with deltas vs the previous
    calendar month and the list of months that have data (for the ◀ ▶ / dropdown nav).
    `month` = local 'YYYY-MM'; defaults to the most recent month with any data."""
    from db.settings import get_language
    import calendar
    buckets = _collect_monthly_buckets()
    if not buckets:
        return {"has_data": False, "month": None, "months": []}

    months_desc = sorted(buckets.keys(), reverse=True)
    if not month or month not in buckets:
        month = months_desc[0]

    lang = get_language()
    def _label(mk):
        return i18n.fmt_month_year(lang, datetime.strptime(mk, "%Y-%m"))

    cur      = buckets[month]
    prev_key = _month_shift(month, -1)
    prev     = buckets.get(prev_key)

    older = [m for m in months_desc if m < month]   # desc → nearest past is first
    newer = [m for m in months_desc if m > month]   # desc → nearest future is last

    def _delta(now, was):
        if not was:                                 # None or 0 → no meaningful %
            return {"diff": round(now, 2), "pct": None}
        return {"diff": round(now - was, 2), "pct": int(round((now - was) / was * 100))}

    deltas = None
    if prev:
        eff_d = None
        if cur["avg_efficiency"] is not None and prev["avg_efficiency"] is not None:
            eff_d = _delta(cur["avg_efficiency"], prev["avg_efficiency"])
        deltas = {
            "km":         _delta(cur["total_km"], prev["total_km"]),
            "kwh_used":   _delta(cur["total_kwh_used"], prev["total_kwh_used"]),
            "cost":       _delta(cur["charge_cost"], prev["charge_cost"]),
            "charge_kwh": _delta(cur["charge_kwh"], prev["charge_kwh"]),
            "efficiency": eff_d,
        }

    avg_price = (round(cur["charge_cost"] / cur["charge_kwh"], 3)
                 if cur["charge_kwh"] > 0 and cur["has_cost"] else None)

    ndays = calendar.monthrange(int(month[:4]), int(month[5:7]))[1]
    daily = [{"day": d,
              "km":   cur["_days"].get(d, {}).get("km", 0.0),
              "cost": cur["_days"].get(d, {}).get("cost", 0.0)}
             for d in range(1, ndays + 1)]

    return {
        "has_data": True,
        "month": month, "label": _label(month),
        "prev_month": older[0] if older else None,
        "next_month": newer[-1] if newer else None,
        "months": [{"key": m, "label": _label(m)} for m in months_desc],
        "cur": cur, "prev": prev, "prev_label": _label(prev_key) if prev else None,
        "deltas": deltas, "avg_price": avg_price, "daily": daily,
    }
