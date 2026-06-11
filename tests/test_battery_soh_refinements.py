"""SoH trend refinements (db_reader.get_battery_health):
- cold charges are SHOWN but excluded from the figure (LFP reads low when cold);
- charges that end near 100% weigh more (the BMS recalibrates SoC there);
- each point carries battery temp + odometer (for the per-distance axis).
Seeds a poller DB and reads it back through the web db_reader, like test_capacity_override."""
import db as D
import db_reader


def _seed(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(67.1)            # nominal reference for SoH/plausibility band
    return db


def _charge(db, cid, day, start_soc, end_soc, amps, temp, odo):
    """One ended charge + 3 charging samples 15 min apart at 400 V × `amps`, so
    ∫V·I = (400·amps/1000)·0.5 h = 0.2·amps kWh of measured DC energy."""
    t0 = f"2026-{day}T08:00:00+00:00"
    t1 = f"2026-{day}T08:15:00+00:00"
    t2 = f"2026-{day}T08:30:00+00:00"
    db._conn.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
        "VALUES (?,1,?,?,?,?,'AC')", (cid, t0, f"2026-{day}T08:31:00+00:00", start_soc, end_soc))
    for t in (t0, t1, t2):
        db._conn.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,charging,charge_voltage_v,"
            "charge_current_a,battery_min_temp,odometer_km) VALUES (1,?,1,400,?,?,?)",
            (t, amps, temp, odo))
    db._conn.commit()


def test_cold_charge_is_shown_but_excluded(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    # both: ΔSoC 80, ∫V·I = 0.2·268 = 53.6 kWh → est = 53.6/0.8 = 67.0 kWh (in band)
    _charge(db, 1, "06-01", 20, 100, amps=268, temp=25, odo=1000)   # warm
    _charge(db, 2, "06-05", 20, 100, amps=268, temp=5,  odo=1500)   # cold (5°C < 15°C gate)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    h = db_reader.get_battery_health()
    pts = {p["charge_id"]: p for p in h["points"]}
    assert pts[1]["excluded"] is False
    assert pts[2]["excluded"] is True and pts[2]["exclude_reason"] == "cold"
    assert h["sample_count"] == 1 and h["excluded_count"] == 1       # cold one out of the figure
    assert pts[2]["temp_c"] == 5.0 and pts[1]["odometer_km"] == 1000  # temp + odometer carried
    assert h["latest_capacity_kwh"] == 67.0                          # headline = the warm one only


def test_full_charges_weigh_more_in_headline(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    # X ends at 100% (weight 1.0), est 67.0 ; Y ends at 70% (weight 0.4), est 42/0.7 = 60.0
    _charge(db, 1, "06-01", 20, 100, amps=268, temp=25, odo=1000)
    _charge(db, 2, "06-05", 0,  70,  amps=210, temp=25, odo=2000)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))

    h = db_reader.get_battery_health()
    assert h["sample_count"] == 2
    # plain mean would be 63.5; weighting toward the 100%-ender gives (67·1 + 60·0.4)/1.4 = 65.0
    assert h["latest_capacity_kwh"] == 65.0
