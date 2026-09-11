"""The §3 ``timing_authority_applied`` report the running daemon leaves.

CLIENT-CONTRACT §18.5 (amendment 2026-09-04): the field describes the labels
the running client writes.  The daemon aggregates one anchor per tracked
source and writes the block once a minute to
``<output_dir>/<inst_key>/timing-authority.json``; ``inventory --json``
(another process) reads the same path.  Both sides take the path from one
helper so they cannot drift apart.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from hamsci_dsp.timing import AnchorUTC, read_applied_state

from superdarn_sounder.contract import build_inventory
from superdarn_sounder.core.applied_state import (
    applied_state_for,
    applied_state_path,
    instance_key,
)
from superdarn_sounder.core.daemon import SounderDaemon

BLOCK = {
    "status": "bee1-hf-status.local",
    "band": [{"id": "superdarn-12mhz",
              "center_freq_hz": 12_000_000, "sample_rate_hz": 100_000}],
}


def _config(output_dir, reporter_id=None):
    cfg = {
        "station": {"callsign": "AC0G", "grid_square": "EM38ww",
                    "receiver_lat": 38.85, "receiver_lon": -91.95},
        "paths": {"output_dir": str(output_dir),
                  "log_dir": str(output_dir / "log")},
        "radiod": [BLOCK],
        "detection": {},
    }
    if reporter_id:
        cfg["instance"] = {"reporter_id": reporter_id}
    return cfg


def _anchor(offset_ns, utc=1_700_000_500.0):
    snap = SimpleNamespace(
        t_level_active="T6", sigma_ns=4210, governor_radiod="gov",
        utc_published=None, host_clock=None,
    ) if offset_ns is not None else None
    return AnchorUTC(
        utc=utc, source="rtp_to_utc+authority" if offset_ns is not None else "rtp_to_utc",
        offset_seconds=(offset_ns or 0) / 1e9, offset_ns=offset_ns,
        snapshot=snap, rtp_referenced=True,
    )


def _tracker(anchor, *, has_source=True):
    if not has_source:
        return SimpleNamespace(current_source=None)
    return SimpleNamespace(current_source=SimpleNamespace(anchor=anchor))


# -- the pure aggregate ---------------------------------------------------

def test_applied_state_for_all_corrected_is_populated():
    block = applied_state_for([_anchor(4_250_000), _anchor(4_250_000)], "rx")
    assert block["tier"] == "T6"
    assert block["radiod_id"] == "rx"
    assert block["channels"] == {"total": 2, "anchored": 2, "applied": 2}
    json.dumps(block)


def test_applied_state_for_mixed_is_null():
    assert applied_state_for([_anchor(4_250_000), _anchor(None)], "rx") is None


# -- the daemon writer ----------------------------------------------------

def _daemon(tmp_path, reporter_id=None, now=1_700_000_600.0):
    cfg = _config(tmp_path, reporter_id)
    d = SounderDaemon(cfg, BLOCK, instance=reporter_id, reporter_id=reporter_id)
    clock = {"t": now}
    d._now_fn = lambda: clock["t"]
    return d, clock


def test_writer_mixed_trackers_write_null(tmp_path):
    d, _ = _daemon(tmp_path)
    d._trackers = [_tracker(_anchor(4_250_000)), _tracker(_anchor(None))]
    d._write_applied_state(force=True)
    path = applied_state_path(tmp_path, "bee1-hf-status.local")
    data = json.loads(path.read_text())
    assert data["schema"] == "applied-state/v1"
    assert data["timing_authority_applied"] is None


def test_writer_all_corrected_writes_block_with_counts(tmp_path):
    d, clock = _daemon(tmp_path)
    d._trackers = [_tracker(_anchor(4_250_000)), _tracker(_anchor(4_250_000))]
    d._write_applied_state(force=True)
    block = read_applied_state(applied_state_path(tmp_path, "bee1-hf-status.local"),
                               now_fn=lambda: clock["t"])
    assert block["tier"] == "T6"
    assert block["radiod_id"] == "bee1-hf-status.local"
    assert block["channels"] == {"total": 2, "anchored": 2, "applied": 2}


def test_writer_tracker_without_source_counts_in_total_only(tmp_path):
    d, clock = _daemon(tmp_path)
    d._trackers = [_tracker(_anchor(4_250_000)), _tracker(None, has_source=False)]
    d._write_applied_state(force=True)
    block = read_applied_state(applied_state_path(tmp_path, "bee1-hf-status.local"),
                               now_fn=lambda: clock["t"])
    assert block["channels"] == {"total": 2, "anchored": 1, "applied": 1}


def test_writer_blind_source_is_the_single_anchor(tmp_path):
    d, clock = _daemon(tmp_path)
    d._blind_source = SimpleNamespace(anchor=_anchor(4_250_000))
    d._write_applied_state(force=True)
    block = read_applied_state(applied_state_path(tmp_path, "bee1-hf-status.local"),
                               now_fn=lambda: clock["t"])
    assert block["channels"] == {"total": 1, "anchored": 1, "applied": 1}


def test_writer_time_gate_once_per_minute(tmp_path):
    d, clock = _daemon(tmp_path)
    d._trackers = [_tracker(_anchor(4_250_000))]
    path = applied_state_path(tmp_path, "bee1-hf-status.local")

    d._write_applied_state()                       # first call writes
    first = json.loads(path.read_text())["written_epoch"]
    clock["t"] += 30.0
    d._write_applied_state()                       # 30 s later: gated
    assert json.loads(path.read_text())["written_epoch"] == first
    clock["t"] += 30.0
    d._write_applied_state()                       # 60 s later: writes
    assert json.loads(path.read_text())["written_epoch"] == first + 60.0


def test_writer_never_raises(tmp_path):
    d, _ = _daemon(tmp_path)

    class _Broken:
        @property
        def current_source(self):
            raise RuntimeError("boom")

    d._trackers = [_Broken()]
    d._write_applied_state(force=True)             # must not propagate


# -- one path rule for daemon and inventory --------------------------------

def test_instance_key_matches_contract_rule():
    cfg = _config(Path("/x"))
    assert instance_key(cfg, BLOCK) == "bee1-hf-status.local"
    cfg = _config(Path("/x"), reporter_id="AC0G-SUPERDARN")
    assert instance_key(cfg, BLOCK) == "AC0G-SUPERDARN"
    # Two blocks: one reporter id cannot disambiguate, so the radiod status keys.
    cfg["radiod"] = [BLOCK, {**BLOCK, "status": "other.local"}]
    assert instance_key(cfg, BLOCK) == "bee1-hf-status.local"


def test_applied_state_path_shape():
    assert applied_state_path("/var/lib/sd", "AC0G-SUPERDARN") == Path(
        "/var/lib/sd/AC0G-SUPERDARN/timing-authority.json")


def test_daemon_and_inventory_name_one_file(tmp_path):
    for reporter_id in (None, "AC0G-SUPERDARN"):
        cfg = _config(tmp_path, reporter_id)
        d = SounderDaemon(cfg, BLOCK, instance=reporter_id, reporter_id=reporter_id)
        inv = build_inventory(cfg, tmp_path / "x.toml")
        inst = inv["instances"][0]
        assert d.inst_key == inst["instance"]
        assert d.applied_state_path() == applied_state_path(tmp_path, inst["instance"])
        # And the inventory really reads what the daemon writes there.
        d._trackers = [_tracker(_anchor(4_250_000))]
        d._write_applied_state(force=True)
        inv = build_inventory(cfg, tmp_path / "x.toml")
        assert inv["instances"][0]["timing_authority_applied"]["tier"] == "T6"
