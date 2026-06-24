"""superdarn Phase-2 propagation products (built on hamsci_dsp)."""
import math

import numpy as np

from hamsci_dsp.constants import C_KM_S
from superdarn_sounder.core.propagation import (
    dtec_from_pulses,
    oblique_products,
    propagation_window,
    scintillation_from_pulses,
)


def test_dtec_from_pulses_recovers_drift():
    # 60 pulses at 1 Hz, carrier phase drifts 6 rad over the dwell (small steps)
    t = np.arange(60.0)
    phase = np.linspace(0.0, 6.0, t.size)
    phasors = np.exp(1j * phase)
    r = dtec_from_pulses(t, phasors, frequency_mhz=11.0)
    assert r is not None
    assert r.n_points == 60
    assert abs(r.dtec_tecu[0]) < 1e-9
    assert abs(r.dtec_tecu[-1]) > 0           # non-zero drift detected
    assert r.unwrap_quality == 1.0


def test_dtec_from_pulses_none_when_sparse():
    assert dtec_from_pulses([0.0, 1.0], [1 + 0j, 1 + 0j], 11.0) is None


def test_scintillation_quiet_vs_fading():
    steady = np.ones(60, dtype=complex)
    quiet = scintillation_from_pulses(steady * (1 + 0.01j), rate_hz=1.0)
    rng = np.random.default_rng(0)
    fading = scintillation_from_pulses(
        rng.standard_normal(60) + 1j * rng.standard_normal(60), rate_hz=1.0)
    assert quiet.s4_index < fading.s4_index


def test_oblique_products_recovers_virtual_height():
    D, h, N = 628.0, 250.0, 1
    P = math.sqrt(D ** 2 + (2 * N * h) ** 2)          # km
    group_delay_s = P / C_KM_S                          # one-way
    op = oblique_products(group_delay_s, D, 11.0, n_hops=N)
    assert abs(op.virtual_height_km - h) < 0.5
    assert op.equivalent_vertical_freq_mhz < 11.0       # vertical < oblique
    assert op.muf_mhz > op.equivalent_vertical_freq_mhz
    assert op.layer in ("F1", "F2")
    assert abs(op.group_range_km - P) < 1e-3


def test_propagation_window_aggregates_per_radar():
    recs = [
        {"candidate_radar": "fhe", "center_freq_hz": 11_028_000, "snr_db": 12.0},
        {"candidate_radar": "fhe", "center_freq_hz": 11_300_000, "snr_db": 14.0},
        {"candidate_radar": "bks", "center_freq_hz": 11_700_000, "snr_db": 9.0},
        {"candidate_radar": None,  "center_freq_hz": 12_000_000, "snr_db": 5.0},
    ]
    win = propagation_window(recs)
    fhe = next(w for w in win if w["radar"] == "fhe")
    assert fhe["n_detections"] == 2
    assert fhe["freq_min_mhz"] == 11.028
    assert fhe["freq_max_mhz"] == 11.3
    assert fhe["observed_muf_lower_bound_mhz"] == 11.3
    assert {w["radar"] for w in win} == {"fhe", "bks"}     # None dropped
