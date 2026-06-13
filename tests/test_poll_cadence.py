"""Poll cadence: a sleeping car or a flaky car↔cloud link must NOT throttle the poller to a long
fixed backoff. OFFLINE now keeps the user's configured parked cadence, so the poller re-catches a
trip start the moment data returns instead of sleeping up to 15 min (the #52 root cause)."""
from state_machine import State, StateMachine


def _sm(parked=30, driving=10):
    sm = StateMachine()
    sm.poll_parked = parked
    sm.poll_driving = driving
    return sm


def test_offline_keeps_parked_cadence():
    sm = _sm(parked=30, driving=10)
    sm.state = State.OFFLINE
    assert sm.poll_interval == 30        # was a fixed 900 s (15 min) — now the user's parked cadence


def test_offline_honours_the_users_parked_value():
    sm = _sm(parked=45, driving=5)
    sm.state = State.OFFLINE
    assert sm.poll_interval == 45        # whatever the user set, not a hidden override


def test_driving_and_parked_cadences_unchanged():
    sm = _sm(parked=30, driving=10)
    sm.state = State.DRIVING
    assert sm.poll_interval == 10
    sm.state = State.PARKED_ALERT
    assert sm.poll_interval == 10        # drive imminent → fast
    sm.state = State.PARKED_ACTIVE
    assert sm.poll_interval == 30
    sm.state = State.CHARGING
    assert sm.poll_interval == 30
