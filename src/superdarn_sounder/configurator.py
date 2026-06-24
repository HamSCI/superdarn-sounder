"""Configuration interview (CONTRACT §14): config init|edit|show|apply.

Kept deliberately simple — like the sibling recorders, init scaffolds the
config from the template and surfaces the sigmond-provided STATION_* env
defaults; edit opens $EDITOR; show/apply give sigmond's TUI a JSON round-trip.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path("/etc/superdarn-sounder")
CONFIG_PATH = CONFIG_DIR / "superdarn-sounder-config.toml"
_TEMPLATE = (Path(__file__).resolve().parent.parent.parent
             / "config" / "superdarn-sounder-config.toml.template")


def _load_toml(path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def cmd_config_init(args) -> int:
    reconfig = getattr(args, "reconfig", False)
    target = getattr(args, "config", None) or CONFIG_PATH
    target = Path(target)
    if target.exists() and not reconfig:
        print(f"config already exists: {target} (use --reconfig to overwrite)",
              file=sys.stderr)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_TEMPLATE, target)
        print(f"wrote {target} from template", file=sys.stderr)

    # Surface the sigmond-provided station defaults (CONTRACT §14.3) for the
    # operator to paste into [station].
    call = os.environ.get("STATION_CALL", "")
    grid = os.environ.get("STATION_GRID", "")
    lat = os.environ.get("STATION_LAT", "")
    lon = os.environ.get("STATION_LON", "")
    radiod = os.environ.get("SIGMOND_RADIOD_STATUS", "")
    print("station defaults from sigmond coordination.env:", file=sys.stderr)
    print(f"  callsign={call or '(unset)'}  grid={grid or '(unset)'}  "
          f"receiver_lat={lat or '(unset)'}  receiver_lon={lon or '(unset)'}",
          file=sys.stderr)
    if radiod:
        print(f"  radiod status (mDNS): {radiod}", file=sys.stderr)
    print(f"\nEdit {target} to set [station] and the [[radiod]] status + band, "
          f"then: superdarn-sounder validate", file=sys.stderr)
    return 0


def cmd_config_edit(args) -> int:
    target = Path(getattr(args, "config", None) or CONFIG_PATH)
    if not target.exists():
        print(f"no config at {target}; run `config init` first", file=sys.stderr)
        return 1
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if getattr(args, "non_interactive", False):
        print(f"non-interactive: edit {target} manually", file=sys.stderr)
        return 0
    return subprocess.call([editor, str(target)])


def cmd_config_show(args) -> int:
    target = Path(getattr(args, "config", None) or CONFIG_PATH)
    try:
        data = _load_toml(target)
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"error": str(exc), "config_path": str(target)}))
        return 1
    print(json.dumps(data, indent=2, default=str))
    return 0


def cmd_config_apply(args) -> int:
    src = getattr(args, "input", "-")
    raw = sys.stdin.read() if src == "-" else Path(src).read_text()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON: {exc}", file=sys.stderr)
        return 2
    # v0.1: apply is a thin merge surface — write the provided keys back as
    # TOML.  Full structured-merge is deferred; sigmond's TUI uses show/apply
    # for the simple [station] / [detection] scalar fields.
    target = Path(getattr(args, "config", None) or CONFIG_PATH)
    try:
        import tomli_w
    except ImportError:
        print("tomli-w not installed; cannot write config (edit manually)",
              file=sys.stderr)
        return 3
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        tomli_w.dump(payload, f)
    print(f"wrote {target}", file=sys.stderr)
    return 0
