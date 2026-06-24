"""Output — daily-rotated JSONL (canonical L1 artefact) plus the additive
HamSCI SQLite sink row.

JSONL is the canonical record; the sink write augments it for cross-client
aggregation and no-ops cleanly when /var/lib/sigmond/sink.db is absent (so
standalone hosts work file-only).  Mirrors codar-sounder/core/output.py.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JsonlWriter:
    """Append one JSON record per line to a daily-rotated file:
    ``<root>/<radiod_id>/YYYY/MM/DD.jsonl``."""

    def __init__(self, output_root: str, radiod_id: str):
        self.base = Path(output_root) / radiod_id
        self._lock = threading.Lock()

    def _path_for(self, ts: datetime) -> Path:
        d = self.base / f"{ts.year:04d}" / f"{ts.month:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{ts.day:02d}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        ts = _parse_ts(record.get("timestamp"))
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            with open(self._path_for(ts), "a", encoding="utf-8") as f:
                f.write(line + "\n")


class SinkWriter:
    """Thin wrapper over sigmond.hamsci_sink.Writer that no-ops when the sink
    library or DB is unavailable."""

    def __init__(self, schema_version: int = 1):
        self._writer = None
        try:
            from sigmond.hamsci_sink import Writer
            self._writer = Writer.from_env(
                table="detections", mode="superdarn",
                schema_version=schema_version, batch_rows=64)
        except Exception as exc:   # library absent / sink not writable
            logger.info("hamsci sink unavailable (%s); JSONL-only", exc)

    @property
    def active(self) -> bool:
        return self._writer is not None and not getattr(
            self._writer, "is_noop", False)

    def write(self, row: dict[str, Any]) -> None:
        if self._writer is None:
            return
        try:
            self._writer.insert([row])
        except Exception as exc:
            logger.warning("sink insert failed (%s); continuing file-only", exc)

    def flush(self) -> None:
        if self._writer is not None:
            try:
                self._writer.flush()
            except Exception:
                pass


def sink_row(record: dict[str, Any]) -> dict[str, Any]:
    """Project a detection record into a flat sink row (superdarn.detections)."""
    seq = record.get("sequence") or {}
    return {
        "time": record.get("timestamp"),
        "host_call": record.get("host_call"),
        "host_grid": record.get("host_grid"),
        "radiod_id": record.get("radiod_id"),
        "reporter_id": record.get("reporter_id"),
        "processing_version": record.get("processing_version"),
        "center_freq_hz": record.get("center_freq_hz"),
        "snr_db": record.get("snr_db"),
        "candidate_radar": record.get("candidate_radar"),
        "sequence_name": seq.get("sequence_name"),
        "mode_guess": (seq.get("modes") or [None])[0],
        "tau_us_est": seq.get("tau_us_est"),
        "sequence_match_score": seq.get("score"),
        "n_pulses": record.get("n_pulses"),
        "beam_index_est": record.get("beam_index_est"),
    }


def _parse_ts(ts: Optional[str]) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    try:
        s = ts[:-1] if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
