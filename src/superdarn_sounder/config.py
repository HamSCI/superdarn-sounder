"""Config + reference-data loading and the geometry helpers shared across
superdarn-sounder.

Config schema (see config/superdarn-sounder-config.toml.template):

    [station]      callsign, grid_square, receiver_lat, receiver_lon
    [paths]        output_dir, log_dir
    [[radiod]]     status (mDNS), channel_name, [[radiod.band]] sub-blocks
    [detection]    pulse_width_us, snr_threshold_db, frame_seconds, ...
    [radars]       min_range_km, max_range_km, only[]
    [beam_scan]    scan_period_s, n_beams, integration_s, ut_locked

Vendored reference data lives in ``data/`` at the repo root:
    radars.toml         site geometry (from SuperDARN/hdw)
    pulse_tables.toml   multi-pulse ptab sequences (from SuperDARN/rst lit.)
"""
from __future__ import annotations

import math
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib


# --------------------------------------------------------------------------
# Config loading + per-instance resolution (mirrors the sibling clients)
# --------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("/etc/superdarn-sounder/superdarn-sounder-config.toml")


def load_config(path: Path) -> dict:
    """Load a TOML config. Raises FileNotFoundError if absent."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_config_path(
    instance: Optional[str] = None,
    explicit_path: Optional[Path] = None,
) -> Path:
    """Resolve which config file to load (Phase-5 per-instance cutover).

    Precedence: explicit --config > $SUPERDARN_SOUNDER_CONFIG >
    per-instance /etc/superdarn-sounder/<instance>.toml (when --instance and
    the file exist) > legacy shared config (with a DeprecationWarning if an
    instance was requested but only the shared file exists).
    """
    if explicit_path is not None:
        return Path(explicit_path)
    env = os.environ.get("SUPERDARN_SOUNDER_CONFIG")
    if env:
        return Path(env)
    if instance:
        per = Path(f"/etc/superdarn-sounder/{instance}.toml")
        if per.exists():
            return per
        warnings.warn(
            f"per-instance config {per} not found; falling back to shared "
            f"{DEFAULT_CONFIG_PATH}",
            DeprecationWarning,
            stacklevel=2,
        )
    return DEFAULT_CONFIG_PATH


def extract_reporter_id(config_path: Path) -> Optional[str]:
    """Reporter id from the per-instance config's [instance] block, if any."""
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    rid = data.get("instance", {}).get("reporter_id")
    return str(rid) if rid else None


# --------------------------------------------------------------------------
# Radiod-block / band accessors
# --------------------------------------------------------------------------

def radiod_blocks(config: dict) -> list[dict]:
    blocks = config.get("radiod", [])
    if isinstance(blocks, dict):  # single-block convenience form
        return [blocks]
    return list(blocks)


def resolve_radiod_block(config: dict, radiod_id: Optional[str]) -> dict:
    """Return the [[radiod]] block whose `status` matches radiod_id, or the
    sole block when radiod_id is None. Raises ValueError otherwise."""
    blocks = radiod_blocks(config)
    if not blocks:
        raise ValueError("no [[radiod]] blocks configured")
    if radiod_id is None:
        if len(blocks) == 1:
            return blocks[0]
        raise ValueError(
            "multiple [[radiod]] blocks; --radiod-id required to disambiguate"
        )
    for b in blocks:
        if b.get("status") == radiod_id:
            return b
    raise ValueError(f"no [[radiod]] block with status={radiod_id!r}")


def bands(block: dict) -> list[dict]:
    """The wideband IQ channels for a radiod block."""
    bs = block.get("band", [])
    if isinstance(bs, dict):
        return [bs]
    return list(bs)


def missing_band_fields(band: dict) -> list[str]:
    required = ["id", "center_freq_hz", "sample_rate_hz"]
    return [f for f in required if band.get(f) in (None, "")]


# --------------------------------------------------------------------------
# Reference data (vendored under data/)
# --------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_data_toml(name: str, env_override: Optional[str] = None) -> dict:
    if env_override:
        cand = os.environ.get(env_override)
        if cand and Path(cand).exists():
            with open(cand, "rb") as f:
                return tomllib.load(f)
    path = _DATA_DIR / name
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_radars() -> dict[str, dict]:
    """Return {abbr: radar-dict} from data/radars.toml."""
    return _load_data_toml("radars.toml", "SUPERDARN_RADARS_TOML").get("radar", {})


def load_pulse_tables() -> dict[str, dict]:
    """Return {name: sequence-dict} from data/pulse_tables.toml."""
    return _load_data_toml(
        "pulse_tables.toml", "SUPERDARN_PULSE_TABLES_TOML"
    ).get("sequence", {})


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

_EARTH_R_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * _EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees [0,360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
