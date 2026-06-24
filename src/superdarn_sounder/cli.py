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
    SounderDaemon(config, block, reporter_id=reporter_id).run()


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
