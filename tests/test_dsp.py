"""Unit tests for the SuperDARN detection DSP.

The synthetic generator embeds a known 8-pulse sequence; the detector must
recover the pulses, the matcher must identify the sequence and estimate τ, and
random pulses (QRM) must be rejected.
"""
import numpy as np

from superdarn_sounder.core.beam_phase import ScanModel, beam_phase_at
from superdarn_sounder.core.pulse_detect import detect_pulses
from superdarn_sounder.core.radars import audible_radars
from superdarn_sounder.core.sequence_match import match_sequence
from superdarn_sounder.core.stream import synth_sequence_frame

EIGHT = [0, 14, 22, 24, 27, 31, 42, 43]
SEVEN = [0, 9, 12, 20, 22, 26, 27]
TABLES = {
    "eight_pulse": {"ptab": EIGHT, "mppul": 8, "mpinc_us_range": [1500, 2400],
                    "modes": ["normalscan"]},
    "seven_pulse": {"ptab": SEVEN, "mppul": 7, "mpinc_us_range": [1500, 4800],
                    "modes": ["katscan"]},
}

SR = 100_000.0          # 100 kS/s
TAU_US = 2400.0
PW_US = 300.0


def _frame(snr_db=20.0, ptab=EIGHT, tau_us=TAU_US, seed=1):
    # span the whole 8-pulse sequence (43*tau) plus headroom
    n = int(0.20 * SR)
    return synth_sequence_frame(
        n, SR, ptab=ptab, tau_us=tau_us, pulse_width_us=PW_US,
        freq_offset_hz=3000.0, snr_db=snr_db, start_sample=int(0.02 * SR),
        rng=np.random.default_rng(seed),
    )


# ----- pulse_detect -------------------------------------------------------

def test_detects_all_eight_pulses():
    dets = detect_pulses(_frame(snr_db=20), SR, pulse_width_us=PW_US,
                         snr_threshold_db=10.0)
    assert len(dets) == len(EIGHT)
    # time-ordered
    idx = [d.sample_index for d in dets]
    assert idx == sorted(idx)


def test_detector_quiet_on_noise_only():
    rng = np.random.default_rng(7)
    noise = (rng.standard_normal(int(0.2 * SR))
             + 1j * rng.standard_normal(int(0.2 * SR))).astype(np.complex64)
    dets = detect_pulses(noise, SR, pulse_width_us=PW_US, snr_threshold_db=12.0)
    assert len(dets) == 0


# ----- sequence_match -----------------------------------------------------

def test_identifies_eight_pulse_and_tau():
    dets = detect_pulses(_frame(snr_db=20), SR, pulse_width_us=PW_US,
                         snr_threshold_db=10.0)
    m = match_sequence([d.time_s for d in dets], TABLES, min_score=0.6)
    assert m is not None
    assert m.sequence_name == "eight_pulse"
    assert m.score >= 0.99            # all 8 matched
    assert abs(m.tau_us_est - TAU_US) < 120.0   # τ recovered within a grid step


def test_rejects_random_qrm():
    rng = np.random.default_rng(3)
    # 8 random pulse times across a 100 ms window — should NOT match a ptab
    times = sorted(rng.uniform(0.0, 0.10, size=8).tolist())
    m = match_sequence(times, TABLES, min_score=0.75)
    assert m is None


def test_matches_with_a_missed_leading_pulse():
    dets = detect_pulses(_frame(snr_db=20), SR, pulse_width_us=PW_US,
                         snr_threshold_db=10.0)
    times = sorted(d.time_s for d in dets)[1:]   # drop the first pulse
    m = match_sequence(times, TABLES, min_score=0.6)
    assert m is not None and m.sequence_name == "eight_pulse"


# ----- beam_phase ---------------------------------------------------------

def test_beam_phase_ut_locked():
    model = ScanModel(scan_period_s=60.0, n_beams=16, integration_s=3.75)
    base = 1_000_000_020.0          # minute-aligned (base % 60 == 0)
    assert base % 60.0 == 0.0
    # 30 s into the UT minute → middle beam (floor(30/3.75) = 8)
    bp = beam_phase_at(base + 30.0, model)
    assert bp.beam_index == 8
    assert abs(bp.seconds_into_scan - 30.0) < 1e-6
    # top of the minute → beam 0
    assert beam_phase_at(base, model).beam_index == 0
    # last beam clamps
    assert beam_phase_at(base + 59.9, model).beam_index == 15


# ----- radars -------------------------------------------------------------

def test_fort_hays_is_audible_from_missouri_and_nearest():
    # Fulton, MO (≈ AC0G)
    cands = audible_radars(38.85, -91.95, min_range_km=200, max_range_km=4000)
    abbrs = [c.abbr for c in cands]
    assert "fhe" in abbrs and "fhw" in abbrs
    # Fort Hays is the closest; ~600 km W-SW
    assert cands[0].abbr in ("fhe", "fhw")
    assert 400 < cands[0].distance_km < 800
    assert 230 < cands[0].bearing_deg < 290     # roughly westward


def test_only_filter_restricts_set():
    cands = audible_radars(38.85, -91.95, only=["fhe"])
    assert [c.abbr for c in cands] == ["fhe"]
