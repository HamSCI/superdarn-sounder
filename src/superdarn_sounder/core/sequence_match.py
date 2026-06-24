"""Multi-pulse sequence matching — the QRM discriminator + mode identifier.

Given candidate pulse epochs (from ``pulse_detect``), decide whether they form a
SuperDARN multi-pulse sequence and, if so, which one.  SuperDARN inter-pulse
spacings are deliberately NON-REDUNDANT (chosen so the ACF can be sampled at
many lags without ambiguity), which is exactly what makes passive identification
robust: random interference almost never reproduces a valid lag set.

For each known sequence (``ptab`` in units of the lag unit τ = mpinc) we scan τ
over the physically plausible range and, anchoring on the earliest observed
pulse, score how many ptab entries land on an observed pulse.  The best
(sequence, τ) above threshold is the identification; nothing above threshold
means "not a SuperDARN sequence" (QRM rejected).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class SequenceMatch:
    sequence_name: str
    modes: list[str]
    tau_us_est: float        # estimated lag unit (mpinc)
    score: float             # fraction of ptab entries matched (0..1)
    n_matched: int
    n_pulses_observed: int
    mppul: int

    def to_dict(self) -> dict:
        return {
            "sequence_name": self.sequence_name,
            "modes": list(self.modes),
            "tau_us_est": round(self.tau_us_est, 1),
            "score": round(self.score, 3),
            "n_matched": self.n_matched,
            "n_pulses_observed": self.n_pulses_observed,
            "mppul": self.mppul,
        }


def _tau_grid_us(seq: dict, steps: int = 80) -> np.ndarray:
    rng = seq.get("mpinc_us_range")
    if rng and len(rng) == 2:
        lo, hi = float(rng[0]), float(rng[1])
    else:
        mp = float(seq.get("mpinc_us", 2400))
        lo, hi = 0.7 * mp, 1.3 * mp
    return np.linspace(lo, hi, steps)


def _score_template(
    rel_s: np.ndarray, ptab: list[int], tau_s: float, tol_s: float,
) -> int:
    """Best number of ptab entries matched, anchoring the earliest observed
    pulse on each possible ptab index (robust to a missed leading pulse)."""
    best = 0
    first = float(rel_s[0])
    for j in range(len(ptab)):
        base = first - ptab[j] * tau_s        # implied t of ptab index 0
        used: set[int] = set()
        matched = 0
        for p in ptab:
            target = base + p * tau_s
            if target < -tol_s:
                continue                       # before the observation window
            # nearest unused observed pulse within tolerance
            best_idx, best_d = -1, tol_s
            for idx in range(rel_s.size):
                if idx in used:
                    continue
                d = abs(float(rel_s[idx]) - target)
                if d <= best_d:
                    best_idx, best_d = idx, d
            if best_idx >= 0:
                used.add(best_idx)
                matched += 1
        best = max(best, matched)
    return best


def match_sequence(
    pulse_times_s,
    pulse_tables: dict[str, dict],
    *,
    min_score: float = 0.6,
    tol_frac: float = 0.12,
    tol_floor_us: float = 150.0,
) -> Optional[SequenceMatch]:
    """Identify the best-matching sequence, or None if nothing clears
    ``min_score`` (→ treat as QRM).

    ``tol_frac`` sets the per-pulse timing tolerance as a fraction of τ, with a
    floor of ``tol_floor_us`` (≈ half a pulse width) so very short τ still has a
    usable window.
    """
    times = np.sort(np.asarray(list(pulse_times_s), dtype=np.float64))
    if times.size < 3:
        return None
    rel = times - times[0]

    best: Optional[SequenceMatch] = None
    for name, seq in pulse_tables.items():
        ptab = [int(p) for p in seq.get("ptab", [])]
        if len(ptab) < 3:
            continue
        mppul = int(seq.get("mppul", len(ptab)))
        for tau_us in _tau_grid_us(seq):
            tau_s = tau_us * 1e-6
            tol_s = max(tol_frac * tau_s, tol_floor_us * 1e-6)
            matched = _score_template(rel, ptab, tau_s, tol_s)
            score = matched / mppul
            if best is None or score > best.score:
                best = SequenceMatch(
                    sequence_name=name,
                    modes=list(seq.get("modes", [])),
                    tau_us_est=float(tau_us),
                    score=score,
                    n_matched=matched,
                    n_pulses_observed=int(times.size),
                    mppul=mppul,
                )
    if best is not None and best.score >= min_score:
        return best
    return None
