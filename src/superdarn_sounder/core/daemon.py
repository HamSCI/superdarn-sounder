"""SounderDaemon — the per-frame detection/identification pipeline.

For each wideband IQ frame:
    detect_pulses  →  match_sequence (ptab)  →  beam_phase tag  →  record

``process_frame`` is pure (no I/O) so the same code backs both the daemon and
``detect-scan``; the daemon adds the IQ source loop, JSONL + sink writes, and
systemd notify/watchdog.

v0.1 monitors the FIRST [[radiod.band]] of the bound radiod; multi-band
monitoring via ka9q MultiStream is a follow-up.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Optional

from superdarn_sounder.config import bands, load_pulse_tables
from superdarn_sounder.core.beam_phase import ScanModel, beam_phase_at
from superdarn_sounder.core.pulse_detect import detect_pulses
from superdarn_sounder.core.radars import audible_radars
from superdarn_sounder.core.sequence_match import match_sequence
from superdarn_sounder.core.stream import make_iq_source
from superdarn_sounder.version import GIT_INFO

logger = logging.getLogger("superdarn_sounder.daemon")

PROCESSING_VERSION = "superdarn-sounder/" + (GIT_INFO.get("short") or "0.1.0")


def _timing_authority(radiod_id: str) -> dict:
    """Provenance block from hf-timestd's authority.json, or the standalone
    fallback when hf-timestd is absent."""
    try:
        from hamsci_dsp.timing import AuthorityReader, standalone_timing_authority
    except Exception:
        return {"source": "unavailable", "schema": "v1"}
    snap = AuthorityReader().read()
    if snap is not None:
        return snap.to_timing_authority(client_radiod=radiod_id)
    return standalone_timing_authority(client_radiod=radiod_id)


def process_frame(
    frame,
    utc: datetime,
    config: dict,
    block: dict,
    *,
    band: Optional[dict] = None,
    reporter_id: Optional[str] = None,
    pulse_tables: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """Run the detection pipeline on one IQ frame; return 0..1 records."""
    if band is None:
        chans = bands(block)
        band = chans[0] if chans else {}
    det = config.get("detection", {})
    station = config.get("station", {})
    radar_cfg = config.get("radars", {})

    sample_rate = float(band.get("sample_rate_hz", 100_000))
    center_hz = float(band.get("center_freq_hz", 0))
    radiod_id = str(block.get("status", ""))

    pulses = detect_pulses(
        frame, sample_rate,
        pulse_width_us=float(det.get("pulse_width_us", 300.0)),
        snr_threshold_db=float(det.get("snr_threshold_db", 12.0)),
    )
    if len(pulses) < int(det.get("min_pulses", 6)):
        return []

    tables = pulse_tables if pulse_tables is not None else load_pulse_tables()
    match = match_sequence(
        [p.time_s for p in pulses], tables,
        min_score=float(det.get("match_score_threshold", 0.6)))
    if match is None:
        return []                       # not a SuperDARN sequence → QRM

    # Burst-start UTC = frame start + earliest detected pulse time.
    burst_start = utc.timestamp() + min(p.time_s for p in pulses)
    scan = ScanModel.from_config(config.get("beam_scan", {}))
    bp = beam_phase_at(burst_start, scan)

    cands = audible_radars(
        float(station.get("receiver_lat", 0.0)),
        float(station.get("receiver_lon", 0.0)),
        min_range_km=float(radar_cfg.get("min_range_km", 200)),
        max_range_km=float(radar_cfg.get("max_range_km", 4000)),
        only=list(radar_cfg.get("only", []) or []),
    )
    candidate = cands[0].abbr if cands else None

    snr_db = max(p.snr_db for p in pulses)
    record = {
        "timestamp": datetime.fromtimestamp(burst_start, tz=timezone.utc)
                     .isoformat().replace("+00:00", "Z"),
        "client": "superdarn-sounder",
        "radiod_id": radiod_id,
        "reporter_id": reporter_id or radiod_id,
        "host_call": station.get("callsign"),
        "host_grid": station.get("grid_square"),
        "processing_version": PROCESSING_VERSION,
        "center_freq_hz": center_hz,
        "snr_db": round(snr_db, 2),
        "n_pulses": len(pulses),
        "sequence": match.to_dict(),
        "candidate_radar": candidate,
        "audible_candidates": [c.abbr for c in cands],
        "beam_index_est": bp.beam_index,
        "beam_phase": bp.to_dict(),
        "timing_authority": _timing_authority(radiod_id),
    }
    return [record]


def _sd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode("utf-8"), addr)
    except OSError:
        pass


class SounderDaemon:
    def __init__(self, config: dict, block: dict, *, reporter_id: Optional[str] = None):
        self.config = config
        self.block = block
        self.reporter_id = reporter_id
        self.radiod_id = str(block.get("status", ""))
        paths = config.get("paths", {})
        self.output_root = paths.get("output_dir", "/var/lib/superdarn-sounder")
        self._stop = False
        self._pulse_tables = load_pulse_tables()

        chans = bands(block)
        if not chans:
            raise ValueError(f"radiod {self.radiod_id!r} has no [[radiod.band]]")
        if len(chans) > 1:
            logger.warning("v0.1 monitors only the first of %d bands on %s; "
                           "multi-band (MultiStream) is a follow-up",
                           len(chans), self.radiod_id)
        self.band = chans[0]

    def run(self) -> None:
        from superdarn_sounder.core.output import (
            JsonlWriter, SinkWriter, sink_row,
        )
        det = self.config.get("detection", {})
        jsonl = JsonlWriter(self.output_root, self.radiod_id)
        sink = SinkWriter(schema_version=1)

        src = make_iq_source(
            radiod_status_dns=self.radiod_id,
            center_freq_hz=float(self.band["center_freq_hz"]),
            sample_rate_hz=float(self.band["sample_rate_hz"]),
            frame_seconds=float(det.get("frame_seconds", 1.0)),
            force_synthetic=bool(self.config.get("processing", {}).get(
                "force_synthetic", False)),
        )
        logger.info("superdarn-sounder daemon up: radiod=%s band=%s @ %.3f MHz",
                    self.radiod_id, self.band.get("id"),
                    float(self.band["center_freq_hz"]) / 1e6)
        _sd_notify("READY=1\nSTATUS=detecting")
        try:
            for frame, utc in src:
                if self._stop:
                    break
                _sd_notify("WATCHDOG=1")
                records = process_frame(
                    frame, utc, self.config, self.block,
                    band=self.band, reporter_id=self.reporter_id,
                    pulse_tables=self._pulse_tables)
                for r in records:
                    jsonl.write(r)
                    sink.write(sink_row(r))
                    logger.info("detection: %s %s τ=%sµs score=%s radar≈%s",
                                r["timestamp"], r["sequence"]["sequence_name"],
                                r["sequence"]["tau_us_est"],
                                r["sequence"]["score"], r["candidate_radar"])
        except KeyboardInterrupt:
            pass
        finally:
            sink.flush()
            if hasattr(src, "stop"):
                src.stop()

    def stop(self) -> None:
        self._stop = True
