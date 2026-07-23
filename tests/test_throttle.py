"""CPU throttle decision logic."""
import throttle
from throttle import CpuThrottle


def test_ceiling_from_headroom():
    assert CpuThrottle(headroom_pct=50).ceiling == 50
    assert CpuThrottle(headroom_pct=75).ceiling == 25
    assert CpuThrottle(headroom_pct=0).ceiling == 95    # clamped
    assert CpuThrottle(headroom_pct=100).ceiling == 5   # clamped


def test_pauses_only_above_the_ceiling():
    t = CpuThrottle(headroom_pct=50)   # ceiling 50
    assert not t.should_pause(30.0)
    assert not t.should_pause(50.0)
    assert t.should_pause(50.1)
    assert t.should_pause(90.0)


def test_disabled_never_pauses():
    t = CpuThrottle(headroom_pct=50, enabled=False)
    assert not t.should_pause(99.0)
    assert t.backoff_seconds(99.0) == 0.0
    assert t.wait_for_slack() == 0.0


def test_backoff_grows_with_load_and_is_capped():
    t = CpuThrottle(headroom_pct=50)   # ceiling 50
    assert t.backoff_seconds(40.0) == 0.0          # under ceiling
    light = t.backoff_seconds(60.0)
    heavy = t.backoff_seconds(100.0)
    assert 0 < light < heavy
    assert heavy <= 2.0                            # capped


def test_from_settings_reads_keys():
    t = throttle.from_settings(lambda: {"ingest_background": True,
                                        "ingest_cpu_headroom": 75})
    assert t.enabled and t.ceiling == 25
    off = throttle.from_settings(lambda: {})
    assert not off.enabled            # defaults to off


def test_wait_returns_quickly_when_idle(monkeypatch):
    """With CPU reported idle, wait_for_slack must return ~immediately."""
    import psutil
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 5.0)
    t = CpuThrottle(headroom_pct=50)
    assert t.wait_for_slack(max_wait=5.0) == 0.0
