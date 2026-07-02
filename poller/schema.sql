CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id          INTEGER PRIMARY KEY,
    vin         TEXT UNIQUE NOT NULL,
    car_type    TEXT,
    year        INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY,
    vehicle_id   INTEGER REFERENCES vehicles(id),
    recorded_at  TEXT NOT NULL,
    latitude     REAL,
    longitude    REAL,
    speed_kmh    REAL,
    odometer_km  REAL,
    soc                 REAL,
    outside_temp        REAL,
    inside_temp         REAL,
    climate_target_temp REAL,
    battery_min_temp    REAL,
    range_km            REAL,
    gear             TEXT,
    charging         INTEGER DEFAULT 0,
    is_locked        INTEGER DEFAULT NULL,
    climate_on       INTEGER DEFAULT NULL,
    climate_cooling  INTEGER DEFAULT NULL,
    climate_heating  INTEGER DEFAULT NULL,
    climate_defrost  INTEGER DEFAULT NULL,
    trunk_open       INTEGER DEFAULT NULL,
    windows_open     INTEGER DEFAULT NULL,
    sunshade_open    INTEGER DEFAULT NULL,
    plug_connected   INTEGER DEFAULT NULL,
    ready            INTEGER DEFAULT NULL,
    charge_completed INTEGER DEFAULT NULL,
    security_active  INTEGER DEFAULT NULL,
    windows_open_count INTEGER DEFAULT NULL,
    door_driver_open     INTEGER DEFAULT NULL,
    door_passenger_open  INTEGER DEFAULT NULL,
    door_rear_left_open  INTEGER DEFAULT NULL,
    door_rear_right_open INTEGER DEFAULT NULL,
    window_fl_open       INTEGER DEFAULT NULL,
    window_rl_open       INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id                   INTEGER PRIMARY KEY,
    vehicle_id           INTEGER REFERENCES vehicles(id),
    started_at           TEXT,
    ended_at             TEXT,
    start_lat            REAL,
    start_lon            REAL,
    end_lat              REAL,
    end_lon              REAL,
    distance_km          REAL,
    start_soc            REAL,
    end_soc              REAL,
    start_odometer_km    REAL,
    end_odometer_km      REAL,
    regen_kwh            REAL DEFAULT 0,
    duration_min         REAL,
    efficiency_kwh_100km REAL,
    efficiency_soc       REAL,                    -- backup of the SoC-derived efficiency (EC override is reversible)
    ec_kwh               REAL,                    -- cloud getEC total for this trip (driving energy)
    ec_driving           REAL,
    ec_ac                REAL,
    ec_other             REAL,
    ec_tried             INTEGER DEFAULT 0,       -- EC enrichment attempts (cloud aggregation lags a fresh trip)
    ec_stable            INTEGER DEFAULT 0,       -- 1 once the cloud EC stabilised (two equal reads) → stop re-fetching
    merged_into_id       INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS trip_positions (
    id          INTEGER PRIMARY KEY,
    trip_id     INTEGER REFERENCES trips(id),
    recorded_at TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    speed_kmh   REAL,
    soc         REAL
);

CREATE TABLE IF NOT EXISTS charges (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    started_at       TEXT,
    ended_at         TEXT,
    start_soc        REAL,
    end_soc          REAL,
    energy_added_kwh REAL,
    duration_min     REAL,
    latitude         REAL,
    longitude        REAL,
    charge_type      TEXT DEFAULT 'AC',        -- AC / DC (from power level)
    location_type    TEXT DEFAULT NULL,         -- HOME / AC / FAST / HPC (user-set)
    max_power_kw     REAL,
    cost             REAL,
    ac_energy_kwh    REAL,         -- wallbox energy a HOME charge is billed on = sum of the counter's rises
    wallbox_energy_start_kwh REAL  -- last wallbox counter reading seen (running baseline for that sum)
);

CREATE TABLE IF NOT EXISTS maintenance_logs (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    service_type     TEXT NOT NULL,             -- matches a pack item's service_type
    done_date        TEXT NOT NULL,             -- ISO date the service was performed
    done_odometer_km REAL,                       -- odometer at the service (prefilled with current)
    note             TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_positions_vehicle ON positions(vehicle_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_trip_positions_trip ON trip_positions(trip_id);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_charges_vehicle ON charges(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_vehicle ON maintenance_logs(vehicle_id, service_type);
-- Charge/Wallbox queries (power curve, time-of-use cost split, "has power" EXISTS)
-- filter charging=1 and range/scan recorded_at; a small partial index keeps them
-- fast as `positions` grows to millions of rows (~8% of rows are charging=1).
CREATE INDEX IF NOT EXISTS idx_positions_charging_recorded ON positions(recorded_at) WHERE charging = 1;

-- Research / BetaTester mode only (MateBetaTesterOnly build). Full raw-signal history (delta:
-- one row per signal that changed value), plus the tester's logbook. Empty/unused in the
-- normal build. Pruned by retention so it can't grow unbounded.
CREATE TABLE IF NOT EXISTS raw_signals_log (
    id          INTEGER PRIMARY KEY,
    vehicle_id  INTEGER,
    ts          INTEGER NOT NULL,   -- epoch ms (signal timestamp)
    sig_key     TEXT NOT NULL,      -- raw Leapmotor signal id, e.g. "3235"
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_signals_ts ON raw_signals_log(ts);

CREATE TABLE IF NOT EXISTS research_logbook (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,   -- epoch ms the note was added
    note        TEXT NOT NULL       -- e.g. "engine started to charge while driving", "refueled to 100%"
);

-- Daily ledger of the car's OFFICIAL lifetime counters (cloud mileage/energy/detail: totalEnergy
-- includes parked/standby, integer kWh) plus the getEC driving split over the window since the
-- previous snapshot. Δ between two rows = total consumption incl. parked, error ≤ ±1 kWh at the
-- window edges REGARDLESS of span (counter sampling — errors don't accumulate); Δ − getEC = the
-- parked/standby share. Raw readings, stored as served, never corrected in place: counter resets
-- and cloud gaps are handled at READ time (total_increasing-style). Silent phase-1 collector, no
-- UI yet.
CREATE TABLE IF NOT EXISTS energy_counter_snapshots (
    id                INTEGER PRIMARY KEY,
    vin               TEXT NOT NULL,
    taken_at          TEXT NOT NULL,     -- UTC ISO
    total_energy_kwh  INTEGER,           -- lifetime consumption counter, integer kWh as served
    total_mileage_km  REAL,              -- from the 0.1-mile field ×1.609344 (finer than the km int)
    ec_driving_kwh    REAL,              -- getEC over [previous snapshot's taken_at, taken_at]
    ec_ac_kwh         REAL,
    ec_other_kwh      REAL,
    ec_status         TEXT               -- 'first' | 'ok' | 'empty' (no driving) | 'miss' (cloud gap)
);
CREATE INDEX IF NOT EXISTS idx_energy_snap_vin_taken ON energy_counter_snapshots(vin, taken_at);
