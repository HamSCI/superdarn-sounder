"""superdarn-sounder CLI entry point.

Subcommands:
    inventory    — contract v0.8 §3 inventory JSON
    validate     — contract v0.8 §12 config validation
    version      — version + git block
    daemon       — long-running detector
    detect-scan  — capture one window and report detections / identification
    config init|edit|show|apply — configuration interview (contract §14)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path


def _resolve_log_level() -> int:
    for env_key in ("SUPERDARN_SOUNDER_LOG_LEVEL", "CLIENT_LOG_LEVEL"):
        val = os.environ.get(env_key, "").upper().strip()
        if val and hasattr(logging, val):
            return getattr(logging, val)
    return logging.INFO


def _install_sighup_handler() -> None:
    def _on_sighup(signum, frame):
        level = _resolve_log_level()
        logging.getLogger().setLevel(level)
        logging.getLogger(__name__).info(
            "SIGHUP: log level set to %s", logging.getLevelName(level))
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _on_sighup)


def main():
    # §4: inventory/validate/version emit clean JSON — quiet logging early and
    # route the root logger to stderr so nothing leaks onto stdout.
    _contract_quiet = any(
        arg in ("inventory", "validate", "version") for arg in sys.argv[1:3])

    root = logging.getLogger()
    root.setLevel(logging.WARNING if _contract_quiet else _resolve_log_level())
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        root.addHandler(handler)

    parser = argparse.ArgumentParser(
        prog="superdarn-sounder",
        description="Passive SuperDARN coherent-radar monitor (detection / ID)")
    sub = parser.add_subparsers(dest="command", help="Command to run")

    def _common(p):
        p.add_argument("--config", type=Path, default=None,
                       help="Path to superdarn-sounder-config.toml")
        p.add_argument("--log-level", default=None,
                       help="Override log level (DEBUG/INFO/WARNING/ERROR)")

    for name, helptext in (("inventory", "Contract v0.8 §3 inventory"),
                           ("validate", "Contract v0.8 §12 validation"),
                           ("version", "Version + git provenance")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--json", action="store_true", default=True)
        _common(p)

    p_dae = sub.add_parser("daemon", help="Run the detector daemon")
    p_dae.add_argument("--instance", default=None,
                       help="Reporter-ID instance (loads /etc/superdarn-sounder/"
                            "<instance>.toml when present)")
    p_dae.add_argument("--radiod-id", default=None,
                       help="status of the [[radiod]] block to use")
    _common(p_dae)

    p_scan = sub.add_parser(
        "detect-scan",
        help="Capture one window and report SuperDARN detections / identification")
    p_scan.add_argument("--radiod-id", default=None)
    p_scan.add_argument("--seconds", type=float, default=2.0,
                        help="Capture duration (default 2 s)")
    p_scan.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable output")
    p_scan.add_argument("--synthetic", action="store_true",
                        help="Use the synthetic source (no radiod needed)")
    p_scan.add_argument("--track", metavar="RADAR", default=None,
                        help="Follow a radar's live operating frequency from the "
                             "VT real-time feed (e.g. --track bks), re-tuning as "
                             "it hops. Needs the 'track' extra.")
    p_scan.add_argument("--dwell", type=float, default=120.0,
                        help="With --track: total dwell seconds (default 120)")
    _common(p_scan)

    p_cfg = sub.add_parser("config", help="Configure superdarn-sounder")
    cfg_sub = p_cfg.add_subparsers(dest="config_command")
    p_init = cfg_sub.add_parser("init", help="write fresh config from template")
    p_init.add_argument("--reconfig", action="store_true")
    p_init.add_argument("--non-interactive", action="store_true")
    _common(p_init)
    p_edit = cfg_sub.add_parser("edit", help="review/update existing config")
    p_edit.add_argument("--non-interactive", action="store_true")
    _common(p_edit)
    p_show = cfg_sub.add_parser("show", help="emit current config (TOML→JSON)")
    p_show.add_argument("--json", action="store_true", default=True)
    p_show.add_argument("--defaults", action="store_true")
    _common(p_show)
    p_apply = cfg_sub.add_parser("apply", help="apply a JSON payload (stdin)")
    p_apply.add_argument("--json", action="store_true", default=True)
    p_apply.add_argument("input", nargs="?", default="-")
    _common(p_apply)

    args = parser.parse_args()

    if not _contract_quiet and getattr(args, "log_level", None):
        level_name = args.log_level.upper()
        if hasattr(logging, level_name):
            root.setLevel(getattr(logging, level_name))

    if args.command == "inventory":
        _handle_inventory(args)
    elif args.command == "validate":
        _handle_validate(args)
    elif args.command == "version":
        _handle_version(args)
    elif args.command == "daemon":
        _handle_daemon(args)
    elif args.command == "detect-scan":
        _handle_detect_scan(args)
    elif args.command == "config":
        _handle_config(args)
    else:
        parser.print_help()
        sys.exit(1)


def _resolved_config_path(args) -> Path:
    return args.config or Path(os.environ.get(
        "SUPERDARN_SOUNDER_CONFIG",
        "/etc/superdarn-sounder/superdarn-sounder-config.toml"))


def _handle_inventory(args):
    from superdarn_sounder.config import load_config
    from superdarn_sounder.contract import CONTRACT_VERSION, build_inventory
    config_path = _resolved_config_path(args)
    try:
        config = load_config(config_path)
    except (FileNotFoundError, OSError) as exc:
        payload = {
            "client": "superdarn-sounder", "version": "0.1.0",
            "contract_version": CONTRACT_VERSION, "config_path": str(config_path),
            "instances": [],
            "issues": [{"severity": "fail", "instance": "all",
                        "message": f"config not loadable: {exc}"}],
        }
        print(json.dumps(payload, indent=2))
        return
    print(json.dumps(build_inventory(config, config_path), indent=2))


def _handle_validate(args):
    from superdarn_sounder.config import load_config
    from superdarn_sounder.contract import build_validate
    config_path = _resolved_config_path(args)
    try:
        config = load_config(config_path)
    except (FileNotFoundError, OSError) as exc:
        payload = {"ok": False, "config_path": str(config_path),
                   "issues": [{"severity": "fail", "instance": "all",
                               "message": f"config not loadable: {exc}"}]}
        print(json.dumps(payload, indent=2))
        sys.exit(1)
    payload = build_validate(config, config_path)
    print(json.dumps(payload, indent=2))
    if not payload["ok"]:
        sys.exit(1)


def _handle_version(args):
    from superdarn_sounder import __version__
    from superdarn_sounder.version import GIT_INFO
    payload = {"client": "superdarn-sounder", "version": __version__}
    if GIT_INFO:
        payload["git"] = GIT_INFO
    print(json.dumps(payload, indent=2))


def _handle_daemon(args):
    _install_sighup_handler()
    from superdarn_sounder.config import (
        extract_reporter_id, load_config, resolve_config_path,
        resolve_radiod_block,
    )
    from superdarn_sounder.core.daemon import SounderDaemon

    instance = getattr(args, "instance", None)
    config_path = resolve_config_path(instance=instance, explicit_path=args.config)
    config = load_config(config_path)
    try:
        block = resolve_radiod_block(config, args.radiod_id)
    except ValueError:
        if args.radiod_id is None or instance is None:
            raise
        logging.getLogger("superdarn_sounder.daemon").warning(
            "--radiod-id=%r did not match any [[radiod]] block; using the "
            "single per-instance block", args.radiod_id)
        block = resolve_radiod_block(config, None)

    reporter_id = extract_reporter_id(config_path)
    SounderDaemon(config, block, instance=instance, reporter_id=reporter_id).run()


def _handle_detect_scan(args):
    _install_sighup_handler()
    from superdarn_sounder.config import (
        bands, load_config, resolve_radiod_block,
    )
    from superdarn_sounder.core.daemon import process_frame
    from superdarn_sounder.core.stream import make_iq_source

    config_path = _resolved_config_path(args)
    config = load_config(config_path)
    block = resolve_radiod_block(config, args.radiod_id)

    if getattr(args, "track", None):
        _handle_track(args, config, block)
        return

    chans = bands(block)
    if not chans:
        sys.stderr.write("no [[radiod.band]] configured\n")
        sys.exit(2)
    band = chans[0]
    det = config.get("detection", {})

    src = make_iq_source(
        radiod_status_dns=str(block.get("status", "")),
        center_freq_hz=float(band["center_freq_hz"]),
        sample_rate_hz=float(band["sample_rate_hz"]),
        frame_seconds=float(args.seconds),
        force_synthetic=bool(args.synthetic),
        # A one-shot scan: give the channel a finite lifetime (~capture + 30 s
        # margin) so it self-cleans off the live radiod afterward.
        lifetime_frames=int((float(args.seconds) + 30.0) * 50),
    )
    try:
        frame, utc = next(iter(src))
    except StopIteration:
        sys.stderr.write("IQ source produced no samples\n")
        sys.exit(3)
    finally:
        if hasattr(src, "stop"):
            src.stop()

    records = process_frame(frame, utc, config, block, reporter_id=None)
    if args.json:
        print(json.dumps({"records": records}, indent=2))
        return
    if not records:
        print("no SuperDARN sequence detected in the captured window")
        return
    for r in records:
        seq = r.get("sequence", {})
        print(f"{r['timestamp']}  {r['center_freq_hz']/1e6:.3f} MHz  "
              f"snr={r['snr_db']:.1f} dB  seq={seq.get('sequence_name','?')} "
              f"τ={seq.get('tau_us_est','?')}µs score={seq.get('score','?')} "
              f"radar≈{r.get('candidate_radar','?')} beam≈{r.get('beam_index_est','?')}")


def _handle_track(args, config, block):
    """Follow a radar's live VT frequency, re-tuning the scan as it hops."""
    import time as _time
    from datetime import datetime, timezone

    from superdarn_sounder.config import bands, load_pulse_tables
    from superdarn_sounder.core.daemon import process_frame
    from superdarn_sounder.core.pulse_detect import detect_pulses
    from superdarn_sounder.core.stream import RadiodIQSource
    from superdarn_sounder.core.vt_realtime import VTRealtimeClient

    radar = args.track
    chans = bands(block)
    sr = float(chans[0]["sample_rate_hz"]) if chans else 150_000.0
    frame_s = float(config.get("detection", {}).get("frame_seconds", 0.5))
    det = config.get("detection", {})
    tables = load_pulse_tables()
    retune_hz = sr * 0.4               # re-tune when the radar moves out of window

    log = logging.getLogger("superdarn_sounder.track")
    vt = VTRealtimeClient([radar])
    try:
        vt.start()
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(2)
    log.info("VT real-time connected; waiting for %s frequency...", radar)
    st = vt.wait_for(radar, timeout_s=20.0)
    if st is None:
        sys.stderr.write(f"no live frequency for {radar!r} from VT "
                         f"(offline, or wrong abbreviation)\n")
        vt.stop()
        sys.exit(3)

    deadline = _time.monotonic() + float(args.dwell)
    src = None
    center_hz = 0.0
    n_frames = 0
    matches = 0
    records: list = []
    try:
        while _time.monotonic() < deadline:
            st = vt.current(radar) or st
            live_hz = st.freq_khz * 1000.0
            if src is None or abs(live_hz - center_hz) > retune_hz:
                if src is not None:
                    src.stop()
                center_hz = live_hz
                log.info("tuning %s -> %.3f MHz (beam %s)",
                         radar, center_hz / 1e6, st.beam)
                src = RadiodIQSource(
                    radiod_status_dns=str(block.get("status", "")),
                    center_freq_hz=center_hz, sample_rate_hz=sr,
                    frame_seconds=frame_s,
                    lifetime_frames=int((float(args.dwell) + 30) * 50))
                it = iter(src)
            try:
                frame, utc = next(it)
            except StopIteration:
                break
            n_frames += 1
            band = {"id": f"{radar}-track", "center_freq_hz": center_hz,
                    "sample_rate_hz": sr}
            recs = process_frame(frame, utc, config, block, band=band,
                                 reporter_id=None, pulse_tables=tables)
            if recs:
                matches += len(recs)
                for r in recs:
                    # In track mode we tuned to THIS radar's live frequency, so
                    # the source is known — override the nearest-audible guess.
                    r["candidate_radar"] = radar
                    r["candidate_via"] = "tracked-frequency"
                records.extend(recs)
                for r in recs:
                    seq = r["sequence"]
                    print(f"  {r['timestamp']} {center_hz/1e6:.3f}MHz "
                          f"MATCH {seq['sequence_name']} tau={seq['tau_us_est']:.0f}us "
                          f"score={seq['score']:.2f} pulses={r['n_pulses']} "
                          f"snr={r['snr_db']:.1f}dB beam~{r['beam_index_est']}",
                          flush=True)
            else:
                d = detect_pulses(frame, sr,
                                  pulse_width_us=float(det.get("pulse_width_us", 300.0)),
                                  snr_threshold_db=float(det.get("snr_threshold_db", 10.0)))
                if d:
                    print(f"  {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                          f"{center_hz/1e6:.3f}MHz pulses={len(d)} "
                          f"maxsnr={max(x.snr_db for x in d):.1f}dB (no sequence)",
                          flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if src is not None:
            src.stop()
        vt.stop()

    summary = _dwell_propagation_summary(records, center_hz)
    if args.json:
        print(json.dumps({"radar": radar, "frames": n_frames, "matches": matches,
                          "propagation": summary, "records": records}, indent=2))
    else:
        print(f"\n=> {radar}: {n_frames} frames, {matches} sequence match(es) "
              f"over {args.dwell:.0f}s dwell")
        if summary.get("dtec"):
            d = summary["dtec"]
            print(f"   dTEC: span {d['dtec_span_tecu']:+.4f} TECU over "
                  f"{d['n_points']} pts (|rate| max {d['abs_rate_max_tecu_per_s']:.5f} "
                  f"TECU/s, unwrap_q {d['unwrap_quality']:.2f})")
        if summary.get("scintillation"):
            s = summary["scintillation"]
            print(f"   scint: S4={s['s4']:.2f} ({s['s4_severity']}) "
                  f"sigma_phi={s['sigma_phi_rad']:.2f} rad")
        for w in summary.get("propagation_window", []):
            print(f"   path {w['radar']}: {w['n_detections']} det, "
                  f"{w['freq_min_mhz']:.3f}-{w['freq_max_mhz']:.3f} MHz "
                  f"(observed MUF >= {w['observed_muf_lower_bound_mhz']:.3f} MHz)")


def _dwell_propagation_summary(records: list, center_hz: float) -> dict:
    """dTEC/dt + scintillation + propagation-window over a tracked dwell."""
    from datetime import datetime, timezone

    from superdarn_sounder.core.propagation import (
        dtec_from_pulses, propagation_window, scintillation_from_pulses,
    )

    out: dict = {"propagation_window": propagation_window(records)}
    # carrier phase series across the dwell (one phasor per matched frame)
    phasors, times = [], []
    for r in records:
        ph = r.get("carrier_phasor")
        ts = r.get("timestamp")
        if ph and ts:
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            phasors.append(complex(ph[0], ph[1]))
            times.append(t)
    if len(phasors) >= 3:
        freq_mhz = center_hz / 1e6
        dt = dtec_from_pulses(times, phasors, freq_mhz)
        if dt is not None:
            rate = dt.dtec_rate_tecu_per_s
            out["dtec"] = {
                "n_points": dt.n_points,
                "dtec_span_tecu": float(dt.dtec_tecu[-1] - dt.dtec_tecu[0]),
                "abs_rate_max_tecu_per_s": float(abs(rate).max()) if len(rate) else 0.0,
                "unwrap_quality": dt.unwrap_quality,
                "n_cycle_slips": dt.n_cycle_slips,
            }
        sc = scintillation_from_pulses(phasors, rate_hz=1.0)
        out["scintillation"] = {
            "s4": sc.s4_index, "s4_severity": sc.s4_severity,
            "sigma_phi_rad": sc.sigma_phi_rad,
        }
    return out


def _handle_config(args):
    from superdarn_sounder import configurator
    sub = getattr(args, "config_command", None)
    if sub == "init":
        sys.exit(configurator.cmd_config_init(args))
    if sub == "edit":
        sys.exit(configurator.cmd_config_edit(args))
    if sub == "show":
        sys.exit(configurator.cmd_config_show(args))
    if sub == "apply":
        sys.exit(configurator.cmd_config_apply(args))
    print("usage: superdarn-sounder config {init|edit|show|apply}")
    sys.exit(2)


if __name__ == "__main__":
    main()
