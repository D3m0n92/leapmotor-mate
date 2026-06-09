"""Self-update check: ask the GitHub Releases API for the latest published Mate version and
compare it to the running one, so the UI can show an unobtrusive "update available" badge next
to the version. Best-effort and OFF the request path — the check runs in a background thread on a
TTL and caches its result in `settings`; a page render only READS the cached value (instant, and
works offline / when GitHub is unreachable, it simply shows nothing). No data is sent — it's a
plain public GET of the latest release tag."""
import json
import threading
import time
import urllib.request

import db_reader

_RELEASES_API = "https://api.github.com/repos/ProtossBlaster/leapmotor-mate/releases/latest"
_RELEASES_PAGE = "https://github.com/ProtossBlaster/leapmotor-mate/releases/latest"
_TTL = 6 * 3600          # re-check at most every 6h — releases are rare, stays well under GH's rate limit
_checking = False
_lock = threading.Lock()


def _ver_tuple(v: str) -> tuple:
    """'1.14.0' / 'v1.14.0' → (1,14,0). Non-numeric junk degrades to 0 so a weird tag never crashes."""
    out = []
    for part in (v or "").lstrip("vV").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _refresh() -> None:
    global _checking
    try:
        req = urllib.request.Request(
            _RELEASES_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "leapmotor-mate"})
        with urllib.request.urlopen(req, timeout=6) as r:
            tag = (json.load(r).get("tag_name") or "").lstrip("vV")
        if tag:
            db_reader.set_setting("update_latest", tag)
    except Exception:  # noqa: BLE001 — offline / rate-limited / GH down: skip this round, keep last value
        pass
    finally:
        db_reader.set_setting("update_checked_at", str(int(time.time())))
        with _lock:
            _checking = False


def _maybe_refresh() -> None:
    global _checking
    try:
        last = int(db_reader.get_setting("update_checked_at", "0") or 0)
    except (TypeError, ValueError):
        last = 0
    if time.time() - last < _TTL:
        return
    with _lock:
        if _checking:
            return
        _checking = True
    threading.Thread(target=_refresh, daemon=True).start()


def get_update_status(current: str) -> dict:
    """{available:bool, latest:str|None, url:str}. Reads the cached latest version (instant) and
    kicks off a background refresh when the cache is stale. Never blocks the render or raises."""
    _maybe_refresh()
    latest = db_reader.get_setting("update_latest", "") or None
    available = bool(latest and _ver_tuple(latest) > _ver_tuple(current))
    return {"available": available, "latest": latest, "url": _RELEASES_PAGE}
