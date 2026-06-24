"""Pulse detection — find the short coherent pulses of a SuperDARN burst.

SuperDARN pulses are ~300 µs rectangular pulses (which is why the range gates
come out at 45 km, cτ/2).  A matched filter for a rectangular pulse is a boxcar
of the pulse length applied to the instantaneous power; it peaks when aligned
with a pulse.  We threshold the matched-filter output against a robust
(median/MAD) noise floor and cluster supra-threshold runs into one detection per
pulse.

The output is a time-ordered list of candidate pulse epochs that
``sequence_match`` then tests against the known multi-pulse tables.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PulseDetection:
    sample_index: int
    time_s: float        # seconds from the start of the analysed buffer
    snr_db: float
    power: float
    phasor: complex = 0j  # coherent sum of IQ over the pulse → carrier phase/amp

    def to_dict(self) -> dict:
        return {
            "sample_index": self.sample_index,
            "time_s": round(self.time_s, 6),
            "snr_db": round(self.snr_db, 2),
        }


def detect_pulses(
    iq: np.ndarray,
    sample_rate_hz: float,
    *,
    pulse_width_us: float = 300.0,
    snr_threshold_db: float = 12.0,
    max_pulses: int = 64,
) -> list[PulseDetection]:
    """Detect ~``pulse_width_us`` pulses in a complex IQ buffer.

    Returns the candidate pulses in time order (strongest kept if more than
    ``max_pulses`` clear the threshold).
    """
    iq = np.asarray(iq)
    if iq.size == 0:
        return []
    power = (iq.real.astype(np.float64) ** 2 + iq.imag.astype(np.float64) ** 2)

    w = max(1, int(round(pulse_width_us * 1e-6 * sample_rate_hz)))
    kernel = np.ones(w, dtype=np.float64) / w
    mf = np.convolve(power, kernel, mode="same")   # mean power over a pulse window

    # Robust noise floor: the median of the matched-filter output is the noise
    # level when pulses are sparse (a SuperDARN burst occupies a few % of the
    # buffer).  MAD guards the threshold against a near-zero median.
    noise = float(np.median(mf))
    if noise <= 0.0:
        mad = float(np.median(np.abs(mf - noise)))
        noise = mad if mad > 0 else float(mf.mean()) or 1e-30
    thresh = noise * (10.0 ** (snr_threshold_db / 10.0))

    above = mf > thresh
    if not above.any():
        return []

    min_sep = max(1, w // 2)
    dets: list[PulseDetection] = []
    n = mf.size
    i = 0
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            k = i + int(np.argmax(mf[i:j]))
            peak = float(mf[k])
            snr_db = 10.0 * np.log10(peak / noise) if noise > 0 else 0.0
            # Coherent sum of the IQ across the pulse window → carrier phasor
            # (its angle is the carrier phase, |.| the pulse amplitude).
            lo = max(0, k - w // 2)
            hi = min(iq.size, k + w // 2 + 1)
            phasor = complex(np.sum(iq[lo:hi]))
            dets.append(PulseDetection(
                sample_index=k,
                time_s=k / sample_rate_hz,
                snr_db=float(snr_db),
                power=peak,
                phasor=phasor,
            ))
            i = j + min_sep
        else:
            i += 1

    if len(dets) > max_pulses:
        dets.sort(key=lambda d: d.snr_db, reverse=True)
        dets = dets[:max_pulses]
    dets.sort(key=lambda d: d.sample_index)
    return dets
