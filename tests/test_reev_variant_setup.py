"""REEV variant in the setup wizard — B10 and C10 get a range-extender battery option,
and selecting it persists is_reev=1 (the gate that later turns on the fuel features). T03
has no REEV variant. Cuts: C10 REEV 28.4 kWh, B10 REEV 18.8 kWh (per published EU specs;
B10 REEV to be confirmed against a real car).

Needs web.main (fastapi); the minimal CI env skips this module cleanly.
"""
import asyncio
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import db as D
import db_reader
import main


class _Req:
    """Minimal Starlette Request stand-in: setup_submit awaits .form() and reads .headers."""
    def __init__(self, data):
        self._data = data
        self.headers = {}

    async def form(self):
        return self._data


def test_eu_battery_map_offers_reev_for_b10_and_c10():
    reev_c10 = [o for o in main._EU_BATTERY_MAP["C10"] if o.get("reev")]
    reev_b10 = [o for o in main._EU_BATTERY_MAP["B10"] if o.get("reev")]
    assert [o["v"] for o in reev_c10] == ["28.4"]
    assert [o["v"] for o in reev_b10] == ["18.8"]


def test_t03_has_no_reev_variant():
    assert not any(o.get("reev") for o in main._EU_BATTERY_MAP["T03"])


def test_reev_option_does_not_replace_the_bev_variants():
    # The BEV packs must still be selectable — REEV is an *added* option, not a swap.
    assert {"69.9", "81.9"} <= {o["v"] for o in main._EU_BATTERY_MAP["C10"]}
    assert {"55.0", "65.0"} <= {o["v"] for o in main._EU_BATTERY_MAP["B10"]}


def _run_setup(tmp_path, monkeypatch, form):
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    asyncio.run(main.setup_submit(_Req(form)))


_BASE = {"user": "u@example.com", "password": "pw", "pin": "1234", "language": "en",
         "vin": "LVIN0000000000001"}


def test_selecting_reev_persists_flag_and_small_battery(tmp_path, monkeypatch):
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "C10", "battery": "28.4", "is_reev": "1"})
    assert db_reader.get_setting("is_reev") == "1"
    assert db_reader.get_setting("battery_capacity_kwh") == "28.4"


def test_selecting_a_bev_pack_leaves_is_reev_off(tmp_path, monkeypatch):
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "B10", "battery": "67.1", "is_reev": "0"})
    assert db_reader.get_setting("is_reev") == "0"


def test_missing_is_reev_defaults_off(tmp_path, monkeypatch):
    # An old client that doesn't send the field must not accidentally flag the car as REEV.
    _run_setup(tmp_path, monkeypatch, {**_BASE, "car_type": "B10", "battery": "65.0"})
    assert db_reader.get_setting("is_reev") == "0"
