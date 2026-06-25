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
import queue
import socket
import threading
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

    strongest = max(pulses, key=lambda p: p.snr_db)
    snr_db = strongest.snr_db
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
        # carrier phasor of the strongest pulse → feeds dTEC/dt + scintillation
        # across a dwell (core/propagation.py).
        "carrier_phasor": [float(strongest.phasor.real), float(strongest.phasor.imag)],
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
    def __init__(self, config: dict, block: dict, *, instance: Optional[str] = None,
                 reporter_id: Optional[str] = None):
        self.config = config
        self.block = block
        self.radiod_id = str(block.get("status", ""))
        # Instance identity (sigmond MULTI-INSTANCE-ARCHITECTURE.md §3): one
        # systemd instance per signal source, each reporting under a unique
        # reporter id.  The systemd instance name (%i) is the reporter id after
        # migration and is the directory key systemd prepared (ExecStartPre
        # mkdir/chown %i), so the spool is keyed on it.  Both fall back to the
        # radiod status for legacy single-instance / non-systemd runs.
        self.instance = instance or self.radiod_id
        self.reporter_id = reporter_id or instance
        paths = config.get("paths", {})
        self.output_root = paths.get("output_dir", "/var/lib/superdarn-sounder")
        self._stop = False
        self._stop_event = threading.Event()
        self._pulse_tables = load_pulse_tables()

        chans = bands(block)
        if not chans:
            raise ValueError(f"radiod {self.radiod_id!r} has no [[radiod.band]]")
        if len(chans) > 1:
            logger.warning("v0.1 monitors only the first of %d bands on %s; "
                           "multi-band (MultiStream) is a follow-up",
                           len(chans), self.radiod_id)
        self.band = chans[0]

    @staticmethod
    def _tracked_radars(track: dict) -> list:
        """The radars to follow: `radars = [...]`, or a single `radar = "..."`."""
        radars = track.get("radars")
        if isinstance(radars, str):
            radars = [radars]
        elif not isinstance(radars, list):
            radars = []
        radars = [r for r in radars if r]
        if not radars and track.get("radar"):
            radars = [track["radar"]]
        return radars

    def _write_records(self, records, jsonl, sink, sink_row) -> None:
        for r in records:
            jsonl.write(r)
            sink.write(sink_row(r))
            logger.info("detection: %s %s τ=%sµs score=%s radar≈%s",
                        r["timestamp"], r["sequence"]["sequence_name"],
                        r["sequence"]["tau_us_est"],
                        r["sequence"]["score"], r["candidate_radar"])
        # Flush the additive sink as soon as a frame produces detections.
        # SuperDARN detections are bursty (a beam dwell, then minutes of quiet),
        # and the sink writer only auto-flushes on a later insert() or at its
        # batch size — so without this a burst's rows would sit in memory until
        # the next burst (or be lost if SIGTERM'd before the shutdown flush).
        # flush() no-ops on an empty buffer.
        if records:
            sink.flush()

    def run(self) -> None:
        from superdarn_sounder.core.output import (
            JsonlWriter, SinkWriter, sink_row,
        )
        det = self.config.get("detection", {})
        jsonl = JsonlWriter(self.output_root, self.instance)
        sink = SinkWriter(schema_version=1)

        track = self.config.get("tracking", {}) or {}
        radars = self._tracked_radars(track)
        force_synth = bool(self.config.get("processing", {}).get(
            "force_synthetic", False))

        if track.get("enabled") and radars and not force_synth:
            self._run_tracking(radars, track, det, jsonl, sink, sink_row)
        else:
            self._run_blind(det, jsonl, sink, sink_row)

    def _run_blind(self, det, jsonl, sink, sink_row) -> None:
        src = make_iq_source(
            radiod_status_dns=self.radiod_id,
            center_freq_hz=float(self.band["center_freq_hz"]),
            sample_rate_hz=float(self.band["sample_rate_hz"]),
            frame_seconds=float(det.get("frame_seconds", 1.0)),
            force_synthetic=bool(self.config.get("processing", {}).get(
                "force_synthetic", False)),
        )
        logger.info("superdarn-sounder daemon up (blind): radiod=%s band=%s "
                    "@ %.3f MHz", self.radiod_id, self.band.get("id"),
                    float(self.band["center_freq_hz"]) / 1e6)
        _sd_notify("READY=1\nSTATUS=detecting")
        try:
            for frame, utc in src:
                if self._stop:
                    break
                _sd_notify("WATCHDOG=1")
                self._write_records(process_frame(
                    frame, utc, self.config, self.block,
                    band=self.band, reporter_id=self.reporter_id,
                    pulse_tables=self._pulse_tables), jsonl, sink, sink_row)
        except KeyboardInterrupt:
            pass
        finally:
            sink.flush()
            if hasattr(src, "stop"):
                src.stop()

    def _run_tracking(self, radars, track, det, jsonl, sink, sink_row) -> None:
        """Track several radars at once under this one reporter id.

        One receiver (this instance) hears many radars; each is a separate
        source-of-opportunity on its own hopping frequency, so we provision one
        TrackedSource (one radiod IQ channel) per radar and run them
        concurrently — all detections land under the single reporter_id with
        `candidate_radar` per record.  A single shared VT client feeds every
        tracker (one socket.io connection).  All writes funnel through one
        writer thread because the SQLite sink connection is thread-bound.
        """
        from superdarn_sounder.core.tracking import TrackedSource
        from superdarn_sounder.core.vt_realtime import VTRealtimeClient

        retune_hz = float(track.get("retune_threshold_hz", 30_000.0))
        sr = float(self.band["sample_rate_hz"])
        frame_s = float(det.get("frame_seconds", 1.0))
        fallback_hz = float(self.band["center_freq_hz"])

        # One shared VT feed for all radars, connected in the background so
        # capture never blocks on it (and degrades to blind fallback if down).
        vt = VTRealtimeClient(radars)

        def _vt_connect():
            while not self._stop_event.is_set():
                try:
                    vt.start()
                    logger.info("VT real-time feed connected; tracking %s",
                                ", ".join(radars))
                    return
                except Exception as exc:
                    logger.warning("VT feed unavailable (%s); tracking blind at "
                                   "%.3f MHz, retrying", exc, fallback_hz / 1e6)
                    self._stop_event.wait(60.0)

        # Single writer thread — SQLite sink + JSONL writes are serialized here.
        q: "queue.Queue" = queue.Queue(maxsize=256)

        def _writer():
            while True:
                item = q.get()
                if item is None:
                    return
                try:
                    self._write_records(item, jsonl, sink, sink_row)
                except Exception:
                    logger.exception("writer: failed to persist a batch")

        def _track_one(radar):
            ts = TrackedSource(
                radiod_status_dns=self.radiod_id, radar=radar,
                sample_rate_hz=sr, frame_seconds=frame_s,
                fallback_center_hz=fallback_hz, retune_hz=retune_hz,
                lifetime_frames=None, vt_client=vt)
            self._trackers.append(ts)
            try:
                for frame, utc, center_hz in ts:
                    if self._stop:
                        break
                    band_now = {**self.band, "center_freq_hz": center_hz}
                    records = process_frame(
                        frame, utc, self.config, self.block,
                        band=band_now, reporter_id=self.reporter_id,
                        pulse_tables=self._pulse_tables)
                    # We tuned to THIS radar's live frequency, so attribution is
                    # known — override the nearest-audible geometric guess.
                    for r in records:
                        r["candidate_radar"] = radar
                        r["candidate_via"] = "tracked-frequency"
                    if records:
                        q.put(records)
            except Exception:
                logger.exception("tracker %s exited on error", radar)

        self._trackers: list = []
        threading.Thread(target=_vt_connect, name="vt-connect",
                         daemon=True).start()
        writer = threading.Thread(target=_writer, name="sink-writer", daemon=True)
        writer.start()
        workers = [threading.Thread(target=_track_one, args=(r,),
                                    name=f"track-{r}", daemon=True)
                   for r in radars]
        for w in workers:
            w.start()

        logger.info("superdarn-sounder daemon up (tracking %s): radiod=%s, "
                    "fallback %.3f MHz", ", ".join(radars), self.radiod_id,
                    fallback_hz / 1e6)
        _sd_notify("READY=1\nSTATUS=tracking " + ",".join(radars))

        try:
            # Heartbeat loop: ping the systemd watchdog and watch the workers.
            while not self._stop_event.is_set():
                if not any(w.is_alive() for w in workers):
                    logger.error("all tracker threads exited; shutting down")
                    break
                _sd_notify("WATCHDOG=1")
                self._stop_event.wait(30.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            for ts in self._trackers:
                ts.stop()
            try:
                vt.stop()
            except Exception:
                pass
            for w in workers:
                w.join(timeout=5.0)
            q.put(None)
            writer.join(timeout=5.0)
            sink.flush()

    def stop(self) -> None:
        self._stop = True
        self._stop_event.set()
