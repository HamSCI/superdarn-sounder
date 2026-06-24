"""Propagation-condition products from passive SuperDARN reception.

Turns detections into "what is the path doing" using the shared
``hamsci_dsp`` propagation library:

* **dTEC / dTEC/dt** — carrier phase across detected pulses → ionospheric Doppler
  and TEC-rate along the radar→RX path (relative; no absolute epoch needed).
* **scintillation** — S4 / sigma_phi from the pulse-amplitude/phase series.
* **oblique products** — group delay → virtual height / equivalent vertical freq
  / MUF (needs an absolute group delay; see ``oblique_products``).
* **propagation window** — which radars/frequencies are reaching us = an observed
  lower bound on the supported frequency (MUF) along each path.

These are the "more than detection" observables: the radars are signals of
opportunity for characterising the ionosphere along known great-circle paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from hamsci_dsp.constants import C_KM_S
from hamsci_dsp.propagation import (
    classify_layer,
    compute_scintillation,
    dtec_from_phase,
    equivalent_vertical_freq_mhz,
    oblique_muf_mhz,
    takeoff_zenith_deg,
    virtual_height_km,
)


# --------------------------------------------------------------------------
# Carrier dTEC / Doppler from detected pulses
# --------------------------------------------------------------------------

def dtec_from_pulses(pulse_times_s: Sequence[float],
                     phasors: Sequence[complex],
                     frequency_mhz: float):
    """Relative dTEC + dTEC/dt from the carrier phase of detected pulses.

    ``phasors`` are the per-pulse coherent IQ sums (``PulseDetection.phasor``);
    their angle is the carrier phase.  Returns a ``hamsci_dsp`` ``CarrierDTEC``
    (or None if < 3 pulses).  This is *relative* — it needs no absolute epoch,
    so it works directly on a dwell's pulse stream.
    """
    t = np.asarray(pulse_times_s, dtype=np.float64)
    ph = np.angle(np.asarray(phasors, dtype=np.complex128))
    if t.size < 3:
        return None
    order = np.argsort(t)
    return dtec_from_phase(t[order], ph[order], frequency_mhz)


def scintillation_from_pulses(phasors: Sequence[complex], rate_hz: float):
    """S4 / sigma_phi from the per-pulse complex amplitude series."""
    z = np.asarray(phasors, dtype=np.complex128)
    return compute_scintillation(z, sample_rate_hz=rate_hz)


# --------------------------------------------------------------------------
# Oblique sounding (needs an absolute group delay)
# --------------------------------------------------------------------------

@dataclass
class ObliqueProducts:
    group_range_km: float
    ground_distance_km: float
    virtual_height_km: float
    equivalent_vertical_freq_mhz: float
    muf_mhz: float
    takeoff_zenith_deg: float
    layer: str
    n_hops: int


def oblique_products(group_delay_s: float, ground_distance_km: float,
                     frequency_mhz: float, *, n_hops: int = 1,
                     critical_freq_mhz: Optional[float] = None
                     ) -> ObliqueProducts:
    """Oblique-sounding products from a one-way group delay (TX→RX).

    ``group_delay_s`` is the absolute propagation delay (the hard part for a
    passive node — bridged via the rawACF TX epoch).  Group path
    ``P = c * group_delay`` (one-way), ground distance ``D`` from radar geometry.
    """
    group_range_km = C_KM_S * group_delay_s
    h = virtual_height_km(group_range_km, ground_distance_km, n_hops)
    fv = equivalent_vertical_freq_mhz(frequency_mhz, group_range_km, ground_distance_km)
    crit = critical_freq_mhz if critical_freq_mhz is not None else fv
    muf = oblique_muf_mhz(crit, group_range_km, ground_distance_km)
    return ObliqueProducts(
        group_range_km=group_range_km,
        ground_distance_km=ground_distance_km,
        virtual_height_km=h,
        equivalent_vertical_freq_mhz=fv,
        muf_mhz=muf,
        takeoff_zenith_deg=takeoff_zenith_deg(group_range_km, ground_distance_km),
        layer=classify_layer(h),
        n_hops=n_hops,
    )


# --------------------------------------------------------------------------
# Propagation window — which radars/frequencies reach us
# --------------------------------------------------------------------------

@dataclass
class PathWindow:
    radar: str
    n_detections: int
    freq_min_hz: float
    freq_max_hz: float
    mean_snr_db: float

    def to_dict(self) -> dict:
        return {
            "radar": self.radar,
            "n_detections": self.n_detections,
            "freq_min_mhz": round(self.freq_min_hz / 1e6, 4),
            "freq_max_mhz": round(self.freq_max_hz / 1e6, 4),
            "observed_muf_lower_bound_mhz": round(self.freq_max_hz / 1e6, 4),
            "mean_snr_db": round(self.mean_snr_db, 2),
        }


def propagation_window(records: Iterable[dict]) -> list[dict]:
    """Aggregate detection records into a per-radar supported-frequency window.

    The highest frequency on which we hear a radar is a measured lower bound on
    the path MUF; the spread shows the open window.  ``records`` are detection
    dicts with ``candidate_radar``, ``center_freq_hz``, ``snr_db``.
    """
    by_radar: dict[str, list[dict]] = {}
    for r in records:
        radar = r.get("candidate_radar")
        if radar is None or r.get("center_freq_hz") is None:
            continue
        by_radar.setdefault(radar, []).append(r)
    out: list[dict] = []
    for radar, rs in sorted(by_radar.items()):
        freqs = [float(r["center_freq_hz"]) for r in rs]
        snrs = [float(r["snr_db"]) for r in rs if r.get("snr_db") is not None]
        out.append(PathWindow(
            radar=radar,
            n_detections=len(rs),
            freq_min_hz=min(freqs),
            freq_max_hz=max(freqs),
            mean_snr_db=float(np.mean(snrs)) if snrs else float("nan"),
        ).to_dict())
    return out
