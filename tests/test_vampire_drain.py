"""get_vampire_drain: SoC lost while PARKED and NOT charging, from the per-poll positions log.
Windows are bounded by driving (speed OR an odometer rise) or charging; short/tiny drops are dropped.
Pure db_reader (no fastapi) → runs in CI."""
import sqlite3
from datetime import timezone

import db_reader

BIG = 100000  # lookback_days huge so the test rows are never filtered by the recency cutoff


def _setup(monkeypatch, rows):
    monkeypatch.setattr(db_reader, "_LOCAL_TZ", timezone.utc)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, soc REAL, charging INT, "
                "speed_kmh REAL, odometer_km REAL)")
    con.executemany("INSERT INTO positions VALUES (?,?,?,?,?)", rows)
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)


def P(hhmm, soc, charging=0, speed=0, odo=1000.0):
    return (f"2026-06-08T{hhmm}:00+00:00", soc, charging, speed, odo)


def test_basic_parked_drain(monkeypatch):
    # parked & unplugged, SoC 80→77 over 6h → one window, 3% / 6h = 12 %/day
    _setup(monkeypatch, [P("00:00", 80), P("02:00", 79), P("04:00", 78), P("06:00", 77)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert (w["drop_pct"], w["hours"], w["pct_per_day"]) == (3.0, 6.0, 12.0)
    assert out["typical_pct_per_day"] == 12.0


def test_driving_breaks_window_and_is_not_counted(monkeypatch):
    # park A (80→78, 4h) · a drive (speed>0, consumes 78→77) · park B (77→75, 3h)
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("04:00", 78, odo=1000),
        P("04:30", 77, speed=40, odo=1010),                 # driving → breaks A, not counted
        P("05:00", 77, odo=1010), P("08:00", 75, odo=1010),
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 2
    drops = sorted(w["drop_pct"] for w in out["windows"])
    assert drops == [2.0, 2.0]                              # the 1% driving loss is excluded


def test_odometer_jump_breaks_window(monkeypatch):
    # park A (80→79, 3h) · GAP with a drive (odo +50, no speed sample, 79→70) · park B (70→69, 3h)
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("03:00", 79, odo=1000),
        P("09:00", 70, odo=1050), P("12:00", 69, odo=1050),  # odo jumped → a drive happened → break
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 2
    # the 9% lost to the (unsampled) drive must NOT appear as a window
    assert all(w["drop_pct"] <= 1.5 for w in out["windows"])


def test_short_tiny_and_charging_are_excluded(monkeypatch):
    _setup(monkeypatch, [
        P("00:00", 80), P("00:30", 79),                     # 30 min < 1h → excluded
        P("10:00", 60, charging=1), P("12:00", 70, charging=1),  # charging → not a park
        P("20:00", 50), P("23:00", 49.9),                   # 0.1% drop < 0.2 → jitter, excluded
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 0 and out["typical_pct_per_day"] is None


def test_issue41_short_window_rate_is_flagged_unreliable(monkeypatch):
    # riri19's exact case (#41): 0.4% over ~1.1h extrapolates to ~9 %/day. The window stays
    # charted (the drop is real awake-state draw) but its rate must carry a wide error band
    # and reliable=False so the UI renders it as an estimate, not an alarm.
    _setup(monkeypatch, [P("00:00", 56.8), P("01:06", 56.4)])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert w["drop_pct"] == 0.4 and w["pct_per_day"] > 8
    assert w["reliable"] is False and w["rate_err"] >= 4
    assert w["ongoing"] is True                             # trailing park is still open


def test_exact_4_quanta_drop_over_long_park_is_reliable(monkeypatch):
    # 77.3−76.9 is 0.3999… in float — the rounded comparison must not coin-flip it (#41
    # verifier finding). 0.4% over 6h: relative error ≤50%, band ±0.8 %/day → reliable.
    _setup(monkeypatch, [
        P("00:00", 77.3), P("06:00", 76.9),
        P("06:30", 76.5, speed=40, odo=1010),               # drive closes the window
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    w = out["windows"][0]
    assert w["drop_pct"] == 0.4 and w["rate_err"] == 0.8
    assert w["reliable"] is True and w["ongoing"] is False


def test_typical_is_time_weighted_and_counts_zero_drop_parks(monkeypatch):
    # 6h/1.0% park + (drive) + 12h/0.0% park → the zero-drop park is not charted but still
    # weighs the headline: 1.0% / 18h × 24 = 1.3 %/day, NOT the charted window's 4.0.
    _setup(monkeypatch, [
        P("00:00", 80, odo=1000), P("06:00", 79, odo=1000),
        P("06:30", 79, speed=40, odo=1010),
        P("07:00", 79, odo=1010), P("19:00", 79, odo=1010),
    ])
    out = db_reader.get_vampire_drain(lookback_days=BIG)
    assert out["count"] == 1
    assert out["windows"][0]["pct_per_day"] == 4.0
    assert out["typical_pct_per_day"] == 1.3
