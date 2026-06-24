"""Contract-surface tests: inventory/validate JSON shape + the end-to-end
synthetic detection pipeline (process_frame)."""
import numpy as np

from superdarn_sounder.contract import (
    CONTRACT_VERSION,
    build_inventory,
    build_validate,
)
from superdarn_sounder.core.daemon import process_frame
from superdarn_sounder.core.stream import synth_sequence_frame

EIGHT = [0, 14, 22, 24, 27, 31, 42, 43]

GOOD_CONFIG = {
    "station": {"callsign": "AC0G", "grid_square": "EM38ww",
                "receiver_lat": 38.85, "receiver_lon": -91.95},
    "paths": {"output_dir": "/var/lib/superdarn-sounder",
              "log_dir": "/var/log/superdarn-sounder"},
    "radiod": [{
        "status": "bee1-hf-status.local",
        "band": [{"id": "superdarn-12mhz",
                  "center_freq_hz": 12_000_000, "sample_rate_hz": 100_000}],
    }],
    "detection": {"pulse_width_us": 300.0, "snr_threshold_db": 10.0,
                  "min_pulses": 6, "match_score_threshold": 0.6},
    "radars": {"min_range_km": 200, "max_range_km": 4000},
    "beam_scan": {"scan_period_s": 60.0, "n_beams": 16, "integration_s": 3.75},
}

PULSE_TABLES = {
    "eight_pulse": {"ptab": EIGHT, "mppul": 8, "mpinc_us_range": [1500, 2400],
                    "modes": ["normalscan"]},
}


def test_inventory_shape():
    inv = build_inventory(GOOD_CONFIG, "/etc/superdarn-sounder/x.toml")
    assert inv["client"] == "superdarn-sounder"
    assert inv["contract_version"] == CONTRACT_VERSION
    assert len(inv["instances"]) == 1
    inst = inv["instances"][0]
    assert inst["radiod_id"] == "bee1-hf-status.local"
    assert inst["frequencies_hz"] == [12_000_000]
    assert inst["ka9q_channels"] == 1
    # harmonize needs these fields present
    assert "data_sinks" in inst and inst["data_sinks"][0]["kind"] == "file"


def test_validate_good_config_ok():
    v = build_validate(GOOD_CONFIG, "/etc/superdarn-sounder/x.toml")
    assert v["ok"] is True


def test_validate_missing_receiver_fails():
    bad = {**GOOD_CONFIG, "station": {"callsign": "AC0G"}}
    v = build_validate(bad)
    assert v["ok"] is False
    assert any(i["severity"] == "fail" and "receiver_lat" in i["message"]
               for i in v["issues"])


def test_validate_no_band_fails():
    bad = {**GOOD_CONFIG,
           "radiod": [{"status": "bee1-hf-status.local", "band": []}]}
    v = build_validate(bad)
    assert v["ok"] is False
    assert any("no [[radiod.band]]" in i["message"] for i in v["issues"])


def test_process_frame_detects_and_identifies():
    sr = 100_000.0
    frame = synth_sequence_frame(
        int(0.2 * sr), sr, ptab=EIGHT, tau_us=2400.0, pulse_width_us=300.0,
        freq_offset_hz=3000.0, snr_db=20.0, start_sample=int(0.02 * sr),
        rng=np.random.default_rng(2))
    from datetime import datetime, timezone
    recs = process_frame(
        frame, datetime(2026, 6, 23, 12, 0, 30, tzinfo=timezone.utc),
        GOOD_CONFIG, GOOD_CONFIG["radiod"][0],
        reporter_id="AC0G-SD", pulse_tables=PULSE_TABLES)
    assert len(recs) == 1
    r = recs[0]
    assert r["sequence"]["sequence_name"] == "eight_pulse"
    assert r["candidate_radar"] in ("fhe", "fhw")     # Fort Hays nearest
    assert r["reporter_id"] == "AC0G-SD"
    assert r["n_pulses"] == 8
    assert "timing_authority" in r and "beam_index_est" in r


def test_process_frame_rejects_noise():
    sr = 100_000.0
    rng = np.random.default_rng(5)
    noise = (rng.standard_normal(int(0.2 * sr))
             + 1j * rng.standard_normal(int(0.2 * sr))).astype(np.complex64)
    from datetime import datetime, timezone
    recs = process_frame(
        noise, datetime.now(timezone.utc), GOOD_CONFIG,
        GOOD_CONFIG["radiod"][0], pulse_tables=PULSE_TABLES)
    assert recs == []
