"""Read-only DB queries for the web layer.

This module re-exports all functions from the db/ sub-package.
Existing consumers can continue importing from db_reader without changes.
"""

# Re-export shared utilities
from db import (  # noqa: F401
    _local_dt, _local_iso, _conn, _get, _conn_rw, DB_PATH, _LOCAL_TZ,
)


# --- charges ---
from db.charges import (  # noqa: F401
    CHARGE_TYPES, _LOCATION_CANDIDATES_WHERE, _SCAN_MAX_KW, update_charge_type,
    auto_confirm_home_charges, has_location_lookup_candidates, get_location_lookup_candidates, get_labelled_locations,
    set_charge_location_name, save_charge_note, add_manual_charge, delete_charge,
    get_charges, get_last_charge_end, get_charge_power_curve, latest_charge_id_with_power,
    charges_with_power, is_home_charge, unconfirmed_charges_count, latest_home_charge_cost,
    _iso_to_utc, _charge_active_window, _charge_window_display, _billed_kwh,
    get_charges_grouped, scan_missed_charges, _integrate_charge_energy_kwh, _charge_has_soc_jump,
    _charge_has_active_use, _charge_temp_odo,
)

# --- costs ---
from db.costs import (  # noqa: F401
    PRICE_KEYS, _TOU_TYPES, get_charge_prices, _mode_allowed,
    get_cost_config, _default_mode_for, save_cost_modes, get_dynamic_price_entity,
    save_dynamic_price_entity, get_dynamic_price_entity_for, save_dynamic_price_entity_for, save_cost_config,
    _parse_hhmm, _time_in_window, _band_covers, _match_band,
    _resolve_band_price, _next_charge_start_utc, _power_window_bounds, _dynamic_sensor_cost,
    compute_cost, update_charge_price, _wac_blend, blended_price_at,
)

# --- geo ---
from db.geo import (  # noqa: F401
    get_v2l_sessions, get_v2l_status, get_v2l_total_kwh, _rows_to_segments,
    get_all_track, get_month_track, get_frequent_places,
)

# --- health ---
from db.health import (  # noqa: F401
    _AC_CHARGE_TYPES, SOC_QUANTUM, _DROP_ERR, _VAMPIRE_NOISE_FLOOR,
    _VAMPIRE_ACTIVE_USE_RATE, get_battery_capacity_kwh, get_battery_health, get_vampire_drain,
)

# --- misc ---
from db.misc import (  # noqa: F401
    auto_location_type, add_logbook_note, get_logbook, count_raw_signals,
    get_raw_signal_rows, get_db_size_bytes, checkpoint, _ensure_command_log,
    log_command, command_responsiveness,
)

# --- settings ---
from db.settings import (  # noqa: F401
    CURRENCIES, _DEFAULT_CURRENCY, _READY_SEAT_MODES, get_setting,
    set_setting, get_secret, set_secret, get_or_create_device_id,
    is_setup_complete, get_language, get_currency_code, get_currency,
    set_currency, get_ready_automation_config, save_ready_automation_config,
)

# --- stats ---
from db.stats import (  # noqa: F401
    get_stats_grouped, get_monthly_stats, get_stats_summary, get_charge_stats,
    get_ac_dc_stats, _month_shift, _report_bucket, _collect_monthly_buckets,
    get_monthly_report,
)

# --- trips ---
from db.trips import (  # noqa: F401
    _READY_DEBOUNCE_S, _READY_LOOKBACK_S, TRIP_MERGE_GAP_DEFAULT, TRIP_MERGE_GAP_MIN,
    TRIP_MERGE_GAP_MAX, get_trip_track, save_trip_note, delete_trip,
    _trip_epoch, trip_epoch_window, trip_ec_window, ready_session,
    get_trips_needing_ec, store_trip_ec, apply_ec_trip_energy, revert_ec_trip_energy,
    revert_trip_ec, _gap_minutes, _children_by_parent, _segment_ids,
    _trip_group_stats, get_mergeable_pairs, merge_trips, unmerge_trip,
    preview_merge, get_merge_preview_route, get_trips, get_trips_grouped,
    get_trips_summary, get_first_trip_date, get_first_trip_ts, get_trip_detail,
    get_trip_route, get_trip_totals_between,
)

# --- vehicle ---
from db.vehicle import (  # noqa: F401
    _OPT_TTL, DRIVE_MODES, upsert_vehicle, get_vehicle,
    clear_optimistic_status, extend_optimistic_status, write_optimistic_status, save_fresh_signals,
    get_latest_status, get_ota_status,
)

