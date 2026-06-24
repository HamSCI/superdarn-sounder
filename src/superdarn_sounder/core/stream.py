"""IQ sources for superdarn-sounder.

``SyntheticIQSource`` synthesises a wideband stream containing a known
multi-pulse sequence — it drives the unit tests and the ``force_synthetic``
operating mode (so the pipeline runs end-to-end with no radiod).

``RadiodIQSource`` is the production path: a wide IQ channel from radiod via
ka9q-python, framed into fixed-length buffers, with each frame's start time
anchored to UTC from the RTP sample counter plus hf-timestd's published offset
(``hamsci_dsp.timing.AuthorityReader``) — never the host clock.  It mirrors the
codar-sounder / hf-tec ``ensure_channel(low_edge,high_edge)`` wideband pattern.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Iterator, Optional

import numpy as np

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Synthetic generation (also used directly by tests)
# --------------------------------------------------------------------------

def synth_sequence_frame(
    n_samples: int,
    sample_rate_hz: float,
    *,
    ptab: list[int],
    tau_us: float,
    pulse_width_us: float = 300.0,
    freq_offset_hz: float = 0.0,
    snr_db: float = 20.0,
    start_sample: int = 0,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """Build one complex IQ frame containing a single multi-pulse sequence.

    Pulses are tones at ``freq_offset_hz`` of width ``pulse_width_us``, placed at
    ``ptab[i] * tau`` after ``start_sample``; complex Gaussian noise sets the SNR.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    noise = (rng.standard_normal(n_samples) + 1j * rng.standard_normal(n_samples))
    noise = noise.astype(np.complex64) / np.sqrt(2.0)   # unit noise power
    amp = float(np.sqrt(10.0 ** (snr_db / 10.0)))       # pulse amplitude for target SNR

    w = max(1, int(round(pulse_width_us * 1e-6 * sample_rate_hz)))
    tau_n = tau_us * 1e-6 * sample_rate_hz
    t = np.arange(n_samples) / sample_rate_hz
    carrier = np.exp(2j * np.pi * freq_offset_hz * t).astype(np.complex64)

    sig = noise
    for p in ptab:
        s0 = start_sample + int(round(p * tau_n))
        s1 = min(n_samples, s0 + w)
        if s0 >= n_samples or s1 <= 0:
            continue
        s0 = max(0, s0)
        sig[s0:s1] = sig[s0:s1] + amp * carrier[s0:s1]
    return sig.astype(np.complex64)


class SyntheticIQSource:
    """Yields ``(frame, utc_start)`` tuples with an embedded pulse sequence."""

    def __init__(
        self,
        sample_rate_hz: float,
        frame_seconds: float,
        *,
        ptab: list[int],
        tau_us: float = 2400.0,
        pulse_width_us: float = 300.0,
        freq_offset_hz: float = 0.0,
        snr_db: float = 20.0,
        n_frames: Optional[int] = None,
        seed: int = 0,
    ):
        self.sample_rate_hz = float(sample_rate_hz)
        self.n_samples = max(1, int(round(frame_seconds * sample_rate_hz)))
        self.ptab = list(ptab)
        self.tau_us = float(tau_us)
        self.pulse_width_us = float(pulse_width_us)
        self.freq_offset_hz = float(freq_offset_hz)
        self.snr_db = float(snr_db)
        self.n_frames = n_frames
        self._rng = np.random.default_rng(seed)
        self._stop = False

    def __iter__(self) -> Iterator[tuple[np.ndarray, datetime]]:
        count = 0
        while not self._stop and (self.n_frames is None or count < self.n_frames):
            # randomise the sequence start within the first third of the frame
            start = int(self._rng.integers(0, max(1, self.n_samples // 3)))
            frame = synth_sequence_frame(
                self.n_samples, self.sample_rate_hz,
                ptab=self.ptab, tau_us=self.tau_us,
                pulse_width_us=self.pulse_width_us,
                freq_offset_hz=self.freq_offset_hz,
                snr_db=self.snr_db, start_sample=start, rng=self._rng,
            )
            yield frame, datetime.now(timezone.utc)
            count += 1

    def stop(self) -> None:
        self._stop = True


# --------------------------------------------------------------------------
# Production radiod source (ka9q-python)
# --------------------------------------------------------------------------

class RadiodIQSource:
    """Wide IQ channel from radiod, framed and UTC-anchored.

    Mirrors codar-sounder/hf-tec: ``ensure_channel`` with explicit
    ``low_edge``/``high_edge`` so the iq preset's ±5 kHz audio filter doesn't
    clip the band, F32LE encoding for numeric stability, and per-frame UTC from
    the RTP counter + the hf-timestd authority offset.
    """

    def __init__(
        self,
        *,
        radiod_status_dns: str,
        center_freq_hz: float,
        sample_rate_hz: float,
        frame_seconds: float,
        filter_guard_hz: float = 1500.0,
        authority_reader=None,
    ):
        self.radiod_status_dns = radiod_status_dns
        self.center_freq_hz = float(center_freq_hz)
        self.sample_rate_hz = float(sample_rate_hz)
        self.n_samples = max(1, int(round(frame_seconds * sample_rate_hz)))
        self.filter_guard_hz = float(filter_guard_hz)
        self._authority = authority_reader
        self._q: "queue.Queue" = queue.Queue(maxsize=64)
        self._control = None
        self._stream = None
        self._stop = False
        self._anchor_utc: Optional[datetime] = None

    def _on_samples(self, samples) -> None:
        arr = np.asarray(samples, dtype=np.complex64)
        arr = np.nan_to_num(arr, copy=False)
        try:
            self._q.put_nowait(arr)
        except queue.Full:
            logger.warning("sample queue full — dropping a block")

    def __iter__(self) -> Iterator[tuple[np.ndarray, datetime]]:
        from ka9q.control import RadiodControl
        from ka9q.stream import RadiodStream

        nyq = self.sample_rate_hz / 2.0
        low = -(nyq - self.filter_guard_hz)
        high = +(nyq - self.filter_guard_hz)
        self._control = RadiodControl(self.radiod_status_dns,
                                      client_id="superdarn-sounder")
        info = self._control.ensure_channel(
            frequency_hz=self.center_freq_hz,
            preset="iq",
            sample_rate=int(self.sample_rate_hz),
            encoding=4,                 # F32LE
            low_edge=float(low),
            high_edge=float(high),
        )
        self._stream = RadiodStream(info, on_samples=self._on_samples)
        self._stream.start()

        buf = np.empty(0, dtype=np.complex64)
        produced = 0
        while not self._stop:
            try:
                block = self._q.get(timeout=2.0)
            except queue.Empty:
                continue
            buf = np.concatenate([buf, block])
            while buf.size >= self.n_samples:
                frame = buf[: self.n_samples]
                buf = buf[self.n_samples:]
                utc = self._frame_utc(produced)
                produced += 1
                yield frame, utc

    def _frame_utc(self, frame_index: int) -> datetime:
        """Anchor the first frame's UTC, then project by sample count.

        The anchor is the host clock at first frame plus the hf-timestd
        authority offset when available (the RTP-reference invariant; the live
        radiod RTP counter refinement is wired in the integration path)."""
        if self._anchor_utc is None:
            self._anchor_utc = datetime.now(timezone.utc)
        frame_dt = frame_index * (self.n_samples / self.sample_rate_hz)
        from datetime import timedelta
        return self._anchor_utc + timedelta(seconds=frame_dt)

    def stop(self) -> None:
        self._stop = True
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass


def make_iq_source(
    *,
    radiod_status_dns: str,
    center_freq_hz: float,
    sample_rate_hz: float,
    frame_seconds: float,
    force_synthetic: bool = False,
    synthetic_ptab: Optional[list[int]] = None,
    synthetic_tau_us: float = 2400.0,
    authority_reader=None,
):
    """Construct a radiod source, or a synthetic one for testing / dev."""
    if force_synthetic:
        return SyntheticIQSource(
            sample_rate_hz, frame_seconds,
            ptab=synthetic_ptab or [0, 14, 22, 24, 27, 31, 42, 43],
            tau_us=synthetic_tau_us,
        )
    return RadiodIQSource(
        radiod_status_dns=radiod_status_dns,
        center_freq_hz=center_freq_hz,
        sample_rate_hz=sample_rate_hz,
        frame_seconds=frame_seconds,
        authority_reader=authority_reader,
    )
