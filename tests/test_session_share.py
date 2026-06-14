"""GitHub #54 — the account TLS cert Leapmotor issues at login is a per-login temp file that
gets cleaned up. The shared session must survive a vanished cert by RE-CREATING it from the
saved bytes (reuse, no re-login), instead of bailing to a full login every cycle (which evicts
the shared session and triggers a token-eviction storm + cloud throttling)."""
import json
import os
import sqlite3

import session_share


class _API:
    pass


def _fresh():
    a = _API()
    for attr in session_share._ATTRS:
        setattr(a, attr, None)
    return a


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.commit()
    con.close()
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return db


def test_reuse_survives_vanished_cert(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    cert = tmp_path / "tmpX-leapmotor-cert.pem"
    key = tmp_path / "tmpX-leapmotor-key.pem"
    cert.write_bytes(b"CERTDATA")
    key.write_bytes(b"KEYDATA")

    a = _fresh()
    a.token = "TOK"
    a.user_id = "u"
    a.device_id = "D"
    a.account_cert_file = str(cert)
    a.account_key_file = str(key)
    session_share._save(a)

    blob = json.loads(sqlite3.connect(str(db)).execute(
        "SELECT value FROM settings WHERE key='shared_session'").fetchone()[0])
    assert "account_cert_b64" in blob   # bytes stashed for re-materialization

    # the per-login tempfiles AND the stable copy both vanish
    for p in {str(cert), str(key), blob["account_cert_file"], blob["account_key_file"]}:
        if os.path.exists(p):
            os.remove(p)

    a2 = _fresh()
    assert session_share._restore(a2) is True       # reuse, NOT a fresh login
    assert a2.token == "TOK"
    assert os.path.exists(a2.account_cert_file)      # cert was re-created on the fly
    assert open(a2.account_cert_file, "rb").read() == b"CERTDATA"


def test_restore_without_session_returns_false(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert session_share._restore(_fresh()) is False
