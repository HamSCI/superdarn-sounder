"""UT-locked beam-scan phase tagging.

A SuperDARN radar scans through its beams (normalscan = 16 beams, ~3–7 s dwell
each, a full scan in ~1 minute) with the scan **locked to the UT minute
boundary**.  So from a GPSDO-disciplined detection time we can predict which
beam the radar was dwelling on — a crude direction discriminant and a clean way
to confirm we're locked onto a real radar (the amplitude should rise and fall
as the beam sweeps past our location once per scan).

This is a *predicted* beam index from the nominal schedule, not a measurement of
the radar's actual program; the exact dwell/scan can vary by control program and
is refined later against rawACF ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanModel:
    """Nominal beam-scan timing.  Defaults match classic normalscan."""
    scan_period_s: float = 60.0
    n_beams: int = 16
    integration_s: float = 60.0 / 16.0   # ~3.75 s
    ut_locked: bool = True
    epoch_offset_s: float = 0.0           # phase offset within the scan period

    @classmethod
    def from_config(cls, cfg: dict) -> "ScanModel":
        scan = float(cfg.get("scan_period_s", 60.0))
        n = int(cfg.get("n_beams", 16))
        integ = float(cfg.get("integration_s", scan / n if n else scan))
        return cls(
            scan_period_s=scan,
            n_beams=n,
            integration_s=integ,
            ut_locked=bool(cfg.get("ut_locked", True)),
            epoch_offset_s=float(cfg.get("epoch_offset_s", 0.0)),
        )


@dataclass(frozen=True)
class BeamPhase:
    beam_index: int
    seconds_into_scan: float
    scan_fraction: float          # 0..1 position within the scan

    def to_dict(self) -> dict:
        return {
            "beam_index": self.beam_index,
            "seconds_into_scan": round(self.seconds_into_scan, 3),
            "scan_fraction": round(self.scan_fraction, 4),
        }


def beam_phase_at(utc_epoch_s: float, model: ScanModel) -> BeamPhase:
    """Beam-dwell index for a detection at ``utc_epoch_s`` (Unix seconds, UTC).

    With ``ut_locked`` the scan is anchored to the UT period boundary, so the
    phase is simply ``utc_epoch_s`` modulo the scan period.  beam_index is
    clamped to [0, n_beams-1].
    """
    period = model.scan_period_s if model.scan_period_s > 0 else 60.0
    into = (utc_epoch_s - model.epoch_offset_s) % period
    frac = into / period
    integ = model.integration_s if model.integration_s > 0 else period
    idx = int(into // integ)
    if idx >= model.n_beams:
        idx = model.n_beams - 1
    if idx < 0:
        idx = 0
    return BeamPhase(beam_index=idx, seconds_into_scan=into, scan_fraction=frac)
