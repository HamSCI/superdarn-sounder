"""Stream-source tests.

Guards the ka9q callback contract that the synthetic path can't exercise:
``RadiodStream`` invokes ``on_samples(samples, quality)`` — a one-arg callback
silently breaks the live path (caught during the sigma RX888 integration test).
"""
from datetime import datetime, timezone

import numpy as np

from superdarn_sounder.core.stream import RadiodIQSource, SyntheticIQSource


def _src():
    return RadiodIQSource(
        radiod_status_dns="x-status.local",
        center_freq_hz=12_000_000, sample_rate_hz=100_000, frame_seconds=0.5,
        lifetime_frames=100,
    )


class _FakeQuality:
    def __init__(self, first_rtp_timestamp):
        self.first_rtp_timestamp = first_rtp_timestamp


class _NoOffsetReader:
    """AuthorityReader stub with no usable offset (forces a clean fallback)."""

    def read(self):
        return None


def test_on_samples_accepts_quality_arg():
    src = _src()
    block = np.ones(8, dtype=np.complex64)
    # RadiodStream delivers (samples, quality); both forms must enqueue.
    src._on_samples(block, quality="ignored")
    src._on_samples(block)                       # quality optional
    assert src._q.qsize() == 2


def test_on_samples_sanitizes_nans():
    src = _src()
    bad = np.array([1 + 1j, np.nan + 0j, 2 + 0j], dtype=np.complex64)
    src._on_samples(bad, quality=None)
    got = src._q.get_nowait()
    assert np.isfinite(got).all()


def test_on_samples_captures_first_rtp_once():
    src = _src()
    src._on_samples(np.ones(4, dtype=np.complex64), _FakeQuality(1234))
    assert src._anchor_first_rtp == 1234
    # A later packet's timestamp must not overwrite the first one.
    src._on_samples(np.ones(4, dtype=np.complex64), _FakeQuality(9999))
    assert src._anchor_first_rtp == 1234
    # A bare callback / non-quality object leaves it None (no crash).
    src2 = _src()
    src2._on_samples(np.ones(4, dtype=np.complex64), quality="ignored")
    assert src2._anchor_first_rtp is None


def test_frame_utc_uses_rtp_anchor(monkeypatch):
    """With an RTP timestamp + channel_info, the anchor is RTP-referenced."""
    import ka9q

    fixed = 1_700_000_000.0  # epoch seconds rtp_to_utc will return
    monkeypatch.setattr(ka9q, "rtp_to_utc", lambda *a, **k: fixed)

    src = _src()
    src._anchor_first_rtp = 4242
    src._channel_info = object()           # truthy channel_info
    src._authority = _NoOffsetReader()      # no offset → source == "rtp_to_utc"

    utc0 = src._frame_utc(0)
    assert utc0 == datetime.fromtimestamp(fixed, tz=timezone.utc)
    # Frame 2 projects forward by 2 frames of samples (n_samples/sample_rate).
    utc2 = src._frame_utc(2)
    dt = 2 * (src.n_samples / src.sample_rate_hz)
    assert abs((utc2 - utc0).total_seconds() - dt) < 1e-6


def test_frame_utc_wallclock_fallback(monkeypatch):
    """No RTP timestamp → clean host-clock fallback (no offset applied)."""
    import hamsci_dsp.timing as timing

    fixed = 1_700_000_500.0
    monkeypatch.setattr(timing.time, "time", lambda: fixed)

    src = _src()
    src._anchor_first_rtp = None            # no RTP → fallback path
    src._channel_info = None
    src._authority = _NoOffsetReader()

    utc0 = src._frame_utc(0)
    assert utc0 == datetime.fromtimestamp(fixed, tz=timezone.utc)


def test_synthetic_source_yields_frames():
    src = SyntheticIQSource(100_000.0, 0.2, ptab=[0, 14, 22, 24, 27, 31, 42, 43],
                            tau_us=2400.0, n_frames=2)
    frames = [f for f, _utc in src]
    assert len(frames) == 2
    assert frames[0].dtype == np.complex64
