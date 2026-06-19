"""V2L (vehicle-to-load) monitoring over MQTT — read-only entities (V2L has no remote command).

Covers the discovery configs (v2l_active binary_sensor + v2l_power/v2l_energy_session sensors) and
the live net-power logic in the service: power is gross discharge MINUS the idle baseline frozen at
session start, energy integrates that net power, and a latched 47==2 with no load reads 0 W."""
import json
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import _parse_signal


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _service(prefix="leapmotor"):
    svc = M.MqttService("broker", 1883, topic_prefix=prefix, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


def D(acmode, i, v=400.0):
    return types.SimpleNamespace(ac_port_mode=acmode, charge_current_a=i, charge_voltage_v=v)


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_v2l_entities():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    pub = svc.client.published

    bint = "homeassistant/binary_sensor/leapmotor_mate_vintest/v2l_active/config"
    assert bint in pub
    assert json.loads(pub[bint])["state_topic"] == "leapmotor/VINTEST/v2l_active"

    powt = "homeassistant/sensor/leapmotor_mate_vintest/v2l_power/config"
    pc = json.loads(pub[powt])
    assert pc["device_class"] == "power" and pc["unit_of_measurement"] == "W"
    assert pc["state_topic"] == "leapmotor/VINTEST/v2l_power"

    ent = "homeassistant/sensor/leapmotor_mate_vintest/v2l_energy_session/config"
    assert json.loads(pub[ent])["unit_of_measurement"] == "Wh"


def test_discovery_respects_prefix():
    svc = _service(prefix="myprefix")
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    assert "homeassistant/sensor/myprefix_mate_vintest/v2l_power/config" in svc.client.published


# ── live net-power logic ─────────────────────────────────────────────────────────

def test_net_power_subtracts_idle_baseline():
    svc = _service()
    svc._v2l_live(D(0, 0.5, 400))                 # idle → baseline I0 = 0.5 A
    active, watt, _ = svc._v2l_live(D(2, 3.0, 400))   # V2L: gross 1200 W, net (3.0-0.5)*400
    assert active is True
    assert watt == 1000                            # NET, not the 1200 gross


def test_inactive_reads_off_and_zero():
    svc = _service()
    active, watt, wh = svc._v2l_live(D(0, 0.7, 400))
    assert active is False and watt == 0 and wh == 0.0


def test_latched_mode_with_no_load_reads_zero():
    # 47==2 but current at/below baseline (mode armed, load off) → net clamped to 0.
    svc = _service()
    svc._v2l_live(D(0, 0.7, 400))
    active, watt, _ = svc._v2l_live(D(2, 0.7, 400))
    assert active is True and watt == 0


def test_energy_accumulates_then_resets(monkeypatch):
    svc = _service()
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    svc._v2l_live(D(0, 0.5, 400))                 # baseline 0.5 A
    svc._v2l_live(D(2, 3.0, 400))                 # session start (no dt yet → 0 Wh)
    clock["t"] += 60                               # +60 s at 1000 W net
    _, watt, wh = svc._v2l_live(D(2, 3.0, 400))
    assert watt == 1000 and abs(wh - 1000 * 60 / 3600) < 0.1    # ≈16.7 Wh
    active, watt2, wh2 = svc._v2l_live(D(0, 0.5, 400))          # V2L ends → reset
    assert active is False and watt2 == 0 and wh2 == 0.0


# ── publish integration (real VehicleData via _parse_signal) ─────────────────────

def test_publish_sensors_emits_v2l_net_power():
    svc = _service()
    # idle first so the baseline (0.7 A) is captured, then a 4.1 A / 422.7 V V2L draw.
    svc._publish_sensors(_parse_signal("VIN", {"47": "0", "1178": "0.7", "1177": "422.7", "100003": "79"}))
    svc._publish_sensors(_parse_signal("VIN", {"47": "2", "1178": "4.1", "1177": "422.7", "100003": "79"}))
    pub = svc.client.published
    assert pub["leapmotor/VIN/v2l_active"] == "ON"
    assert float(pub["leapmotor/VIN/v2l_power"]) == round((4.1 - 0.7) * 422.7)   # 1437 W net (gross 1733)
