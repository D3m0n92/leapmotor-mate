"""MATE_DEMO must be fully opt-in: off by default, and demo.install() a no-op unless the
flag is set — so a normal install is never altered by the demo code being present."""
import types

import demo


def test_is_demo_off_by_default(monkeypatch):
    monkeypatch.delenv("MATE_DEMO", raising=False)
    assert demo.is_demo() is False
    for v in ("", "0", "false", "False", "no"):
        monkeypatch.setenv("MATE_DEMO", v)
        assert demo.is_demo() is False


def test_is_demo_on(monkeypatch):
    monkeypatch.setenv("MATE_DEMO", "1")
    assert demo.is_demo() is True


def test_install_is_noop_when_off(monkeypatch):
    monkeypatch.delenv("MATE_DEMO", raising=False)
    cc = types.SimpleNamespace(
        _session=types.SimpleNamespace(execute=lambda *a, **k: ("real", "real")),
        get_fresh_signals=lambda *a, **k: "REAL")
    ha = types.SimpleNamespace(is_configured=lambda *a, **k: "REAL")
    demo.install(cc, ha)                      # flag off → must change nothing
    assert cc.get_fresh_signals() == "REAL"
    assert cc._session.execute() == ("real", "real")
    assert ha.is_configured() == "REAL"


def test_install_patches_when_on(monkeypatch):
    monkeypatch.setenv("MATE_DEMO", "1")
    cc = types.SimpleNamespace(
        _session=types.SimpleNamespace(execute=lambda *a, **k: ("real", "real")),
        get_fresh_signals=lambda *a, **k: "REAL")
    demo.install(cc)                          # flag on → cloud calls neutered
    assert cc.get_fresh_signals() is None
    assert cc._session.execute() == (True, "demo")
