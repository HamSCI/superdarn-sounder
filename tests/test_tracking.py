"""Tracking primitive + tracked-radar parsing — no network or radiod."""
import time
from datetime import datetime, timezone

import numpy as np

from superdarn_sounder.core.daemon import SounderDaemon
from superdarn_sounder.core.tracking import TrackedSource
from superdarn_sounder.core.vt_realtime import RadarStatus


class _FakeVT:
    """Stand-in for a (shared) VTRealtimeClient with a settable frequency."""

    def __init__(self):
        self.freq_khz = None

    def current(self, site, max_age_s=180.0):
        if self.freq_khz is None:
            return None
        return RadarStatus(freq_khz=self.freq_khz, beam=3,
                           received_monotonic=time.monotonic())

    def start(self):
        pass

    def stop(self):
        pass


def _src_factory(created):
    def factory(center):
        created.append(center)

        def gen():
            while True:
                yield (np.zeros(4, dtype=np.complex64),
                       datetime(2026, 1, 1, tzinfo=timezone.utc))
        return gen()
    return factory


def test_tracked_source_fallback_then_retune():
    fake = _FakeVT()
    created: list = []
    ts = TrackedSource(
        radiod_status_dns="x", radar="fhe", sample_rate_hz=1e5,
        frame_seconds=1.0, fallback_center_hz=11_100_000.0, retune_hz=30_000.0,
        source_factory=_src_factory(created), vt_client=fake)
    it = iter(ts)

    # No live frequency yet → blind capture at the configured fallback centre.
    _, _, c = next(it)
    assert c == 11_100_000.0

    # Radar hops far (>retune) → re-tune the channel to the live frequency.
    fake.freq_khz = 10_808
    _, _, c = next(it)
    assert c == 10_808_000.0

    # Small move (<retune) → stay put (no needless channel churn).
    fake.freq_khz = 10_815
    _, _, c = next(it)
    assert c == 10_808_000.0

    ts.stop()
    assert created == [11_100_000.0, 10_808_000.0]


def test_tracked_source_does_not_touch_shared_vt_on_stop():
    # A shared (external) VT client is the daemon's to manage — stop() must not
    # disconnect it (other trackers may still be using it).
    class _VT(_FakeVT):
        stopped = False

        def stop(self):
            self.stopped = True

    vt = _VT()
    ts = TrackedSource(
        radiod_status_dns="x", radar="bks", sample_rate_hz=1e5,
        frame_seconds=1.0, fallback_center_hz=11_700_000.0,
        source_factory=_src_factory([]), vt_client=vt)
    ts.stop()
    assert vt.stopped is False


def test_tracked_radars_parsing():
    f = SounderDaemon._tracked_radars
    assert f({"radars": ["fhe", "fhw", "bks"]}) == ["fhe", "fhw", "bks"]
    assert f({"radars": "fhe"}) == ["fhe"]          # single string tolerated
    assert f({"radar": "fhe"}) == ["fhe"]           # legacy single key
    assert f({"radars": ["fhe", ""]}) == ["fhe"]    # drops blanks
    assert f({}) == []
    assert f({"enabled": True}) == []
