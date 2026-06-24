#!/usr/bin/env python3
"""Cross-validate superdarn-sounder detections against published rawACF.

This is the scientific-rigour hinge for the detection demo: SuperDARN publishes
rawACF/fitACF files openly (JHU/APL and Virginia Tech mirrors) that log the
exact operating frequency, timestamp, control-program id (cpid), and scan
parameters for every integration period.  We compare our passive detections for
a UT window against the radar's own logged record as ground truth — turning the
node from an uncalibrated detector into a validated one.

This is an OFFLINE tool, not part of the daemon.  It needs ``pydarnio`` (or
``pydarn``) and a downloaded rawACF file — install with the ``validate`` extra:
    uv pip install -e ".[validate]"

Usage:
    validate_against_rawacf.py --detections DAY.jsonl --rawacf FILE.rawacf \\
        [--freq-tol-khz 30] [--time-tol-s 90]

It does NOT download data (mirror access varies by site/credentials); point it
at a rawACF you fetched for the same radar + UT window as the detections.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_detections(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _load_rawacf_records(path: Path) -> list[dict]:
    """Read a rawACF file into a list of {time, tfreq_khz, cpid} dicts."""
    try:
        import pydarnio
    except ImportError:
        sys.exit("pydarnio not installed — `uv pip install -e \".[validate]\"`")
    reader = pydarnio.SDarnRead(str(path))
    recs = reader.read_rawacf()
    out = []
    for r in recs:
        try:
            t = datetime(int(r["time.yr"]), int(r["time.mo"]), int(r["time.dy"]),
                         int(r["time.hr"]), int(r["time.mt"]), int(r["time.sc"]),
                         tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({
            "time": t,
            "tfreq_khz": int(r.get("tfreq", 0)),
            "cpid": int(r.get("cp", 0)),
            "bmnum": int(r.get("bmnum", -1)),
        })
    return out


def _parse_iso(s: str) -> datetime:
    s = s[:-1] if s.endswith("Z") else s
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detections", required=True, type=Path,
                    help="superdarn-sounder JSONL (one detection per line)")
    ap.add_argument("--rawacf", required=True, type=Path,
                    help="rawACF file for the same radar + UT window")
    ap.add_argument("--freq-tol-khz", type=float, default=30.0)
    ap.add_argument("--time-tol-s", type=float, default=90.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dets = _load_detections(args.detections)
    truth = _load_rawacf_records(args.rawacf)
    if not truth:
        return _report([], len(dets), args, note="no rawACF records parsed")

    matched = []
    for d in dets:
        try:
            dt = _parse_iso(d["timestamp"])
            f_khz = float(d["center_freq_hz"]) / 1000.0
        except (KeyError, ValueError):
            continue
        best = None
        for t in truth:
            dt_s = abs((dt - t["time"]).total_seconds())
            df_khz = abs(f_khz - t["tfreq_khz"])
            if dt_s <= args.time_tol_s and df_khz <= args.freq_tol_khz:
                if best is None or (dt_s + df_khz) < best[0]:
                    best = (dt_s + df_khz, t)
        if best is not None:
            matched.append({
                "detection_time": d["timestamp"],
                "detection_freq_khz": round(f_khz, 1),
                "rawacf_time": best[1]["time"].isoformat(),
                "rawacf_tfreq_khz": best[1]["tfreq_khz"],
                "rawacf_cpid": best[1]["cpid"],
                "rawacf_beam": best[1]["bmnum"],
                "delta_freq_khz": round(abs(f_khz - best[1]["tfreq_khz"]), 1),
            })
    return _report(matched, len(dets), args)


def _report(matched, n_det, args, note: str = "") -> int:
    rate = (len(matched) / n_det) if n_det else 0.0
    if args.json:
        print(json.dumps({
            "n_detections": n_det,
            "n_matched": len(matched),
            "match_rate": round(rate, 3),
            "note": note,
            "matches": matched,
        }, indent=2))
    else:
        print(f"detections: {n_det}   matched to rawACF: {len(matched)}   "
              f"rate: {rate:.1%}")
        if note:
            print(f"note: {note}")
        for m in matched[:20]:
            print(f"  {m['detection_time']}  {m['detection_freq_khz']:.0f} kHz "
                  f"→ cpid={m['rawacf_cpid']} beam={m['rawacf_beam']} "
                  f"Δf={m['delta_freq_khz']:.0f} kHz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
