"""Sigmond client contract v0.8 — inventory and validate JSON builders.

  §3   inventory --json — per-instance resource view
  §4   stdout cleanliness
  §11  log level, SIGHUP reload
  §12  validate --json — config validation
  §14  configuration interview — config init/edit
  §15  radiod channel contributions ([[radiod.fragment]])
  §17  output sinks — data_sinks array per instance (file)

One ``instances[]`` entry per configured ``[[radiod]]`` block: a superdarn
daemon binds one radiod and monitors the SuperDARN sub-band on it, detecting
whichever radars are audible.  Each entry carries the ``reporter_id`` from the
per-instance config's ``[instance]`` block (sigmond multi-instance: one systemd
instance per signal source, reporting under a unique reporter id); the reporter
id keys the spool dir and stamps every row.
"""
from __future__ import annotations

import logging
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from superdarn_sounder.config import (
    bands,
    missing_band_fields,
    radiod_blocks,
)
from superdarn_sounder.core.radars import audible_radars
from superdarn_sounder.version import GIT_INFO


CONTRACT_VERSION = "0.8"


def _client_version() -> str:
    try:
        return pkg_version("superdarn-sounder")
    except Exception:
        return "0.1.0"


def build_inventory(config: dict, config_path: Path) -> dict:
    paths = config.get("paths", {})
    log_dir = paths.get("log_dir", "/var/log/superdarn-sounder")
    output_dir = paths.get("output_dir", "/var/lib/superdarn-sounder")

    instances: list[dict] = []
    all_log_paths: dict[str, Any] = {}

    # One reporter id per per-instance config ([instance] block, populated by
    # `smd instance migrate`).  It stamps every detection row and keys the spool
    # dir (matching the systemd unit's ExecStartPre mkdir %i); legacy shared
    # configs have no [instance] block and fall back to the radiod status.
    reporter_id = (config.get("instance") or {}).get("reporter_id") or None
    blocks = radiod_blocks(config)
    single_block = len(blocks) == 1

    for block in blocks:
        status_dns = block.get("status", "")
        radiod_id = status_dns
        # Spool/instance key: the reporter id for a one-reporter-per-config
        # instance, else the radiod status (legacy, or the multi-block edge
        # case where one reporter id can't disambiguate the spool).
        inst_key = reporter_id if (reporter_id and single_block) else radiod_id
        chans = bands(block)
        freqs = [int(b.get("center_freq_hz", 0)) for b in chans
                 if b.get("center_freq_hz")]

        data_sinks: list[dict[str, Any]] = [{
            "kind":           "file",
            "target":         f"{output_dir}/{inst_key}",
            "schema_ref":     "superdarn-sounder:1",
            "retention_days": 365,
            "mb_per_day":     5,
        }]

        instances.append({
            "instance": inst_key,
            "reporter_id": reporter_id,
            "radiod_id": radiod_id,
            "host": "localhost",
            "radiod_status_dns": status_dns,
            "data_destination": None,
            "frequencies_hz": freqs,
            "ka9q_channels": len(chans),
            "required_cores": [],
            "preferred_cores": "worker",
            "data_sinks": data_sinks,
            "uses_timing_calibration": False,
            "provides_timing_calibration": False,
            # RTP-default mode (UTC label from the RTP counter + opportunistic
            # offset), same convention as codar/wspr/psk; becomes a populated
            # object only if a future iteration *gates* on a §18 authority.
            "timing_authority_applied": None,
        })

        all_log_paths[inst_key] = {
            "process": f"{log_dir}/{inst_key}.log",
            "products": f"{output_dir}/{inst_key}",
        }

    effective_level = logging.getLogger().getEffectiveLevel()

    payload: dict[str, Any] = {
        "client": "superdarn-sounder",
        "version": _client_version(),
        "contract_version": CONTRACT_VERSION,
        "config_path": str(config_path),
        "deploy_toml_path": "/opt/git/sigmond/superdarn-sounder/deploy.toml",
    }
    if GIT_INFO:
        payload["git"] = GIT_INFO
    if all_log_paths:
        payload["log_paths"] = all_log_paths
    payload["log_level"] = logging.getLevelName(effective_level)
    payload["instances"] = instances
    payload["deps"] = {
        "pypi": [
            {"name": "ka9q-python", "version": ">=3.14.0"},
            {"name": "numpy", "version": ">=1.24.0"},
            {"name": "hamsci-dsp", "version": ">=0.1.0"},
        ],
    }
    payload["issues"] = _collect_issues(config)
    return payload


def build_validate(config: dict, config_path: Path | None = None) -> dict:
    issues = _collect_issues(config)
    payload: dict[str, Any] = {
        "ok": not any(i["severity"] == "fail" for i in issues),
    }
    if config_path is not None:
        payload["config_path"] = str(config_path)
    payload["issues"] = issues
    return payload


def _collect_issues(config: dict) -> list[dict]:
    issues: list[dict] = []

    station = config.get("station", {})
    if not station.get("callsign"):
        issues.append({"severity": "warn", "instance": "all",
                       "message": "station.callsign is empty"})

    rx_lat = station.get("receiver_lat")
    rx_lon = station.get("receiver_lon")
    if rx_lat in (None, "") or rx_lon in (None, ""):
        issues.append({
            "severity": "fail", "instance": "all",
            "message": "station.receiver_lat / receiver_lon not set "
                       "(needed to select audible radars by great-circle range)",
        })

    blocks = radiod_blocks(config)
    if not blocks:
        issues.append({"severity": "fail", "instance": "all",
                       "message": "no [[radiod]] blocks configured"})

    for block in blocks:
        rid = block.get("status", "<unnamed>")
        if not block.get("status"):
            issues.append({
                "severity": "fail", "instance": rid,
                "message": "[[radiod]] block has no `status` field (mDNS name)",
            })
        chans = bands(block)
        if not chans:
            issues.append({
                "severity": "fail", "instance": rid,
                "message": f"radiod {rid!r} has no [[radiod.band]] blocks "
                           f"(the wideband IQ channel(s) to monitor)",
            })
        for b in chans:
            missing = missing_band_fields(b)
            if missing:
                issues.append({
                    "severity": "fail", "instance": f"{rid}/{b.get('id','?')}",
                    "message": f"band missing fields: {', '.join(missing)}",
                })

    # Tracking: if enabled, it must name at least one radar to follow.
    track = config.get("tracking", {}) or {}
    if track.get("enabled"):
        radars = track.get("radars")
        if isinstance(radars, str):
            radars = [radars]
        radars = [r for r in (radars or []) if r]
        if not radars and track.get("radar"):
            radars = [track["radar"]]
        if not radars:
            issues.append({
                "severity": "fail", "instance": "all",
                "message": "[tracking] enabled but no radars set "
                           "(radars = [\"fhe\", ...])",
            })

    # Informational: warn if no radar is audible from the configured receiver.
    if rx_lat not in (None, "") and rx_lon not in (None, ""):
        radar_cfg = config.get("radars", {})
        try:
            cands = audible_radars(
                float(rx_lat), float(rx_lon),
                min_range_km=float(radar_cfg.get("min_range_km", 200)),
                max_range_km=float(radar_cfg.get("max_range_km", 4000)),
                only=list(radar_cfg.get("only", []) or []),
            )
        except Exception:
            cands = []
        if not cands:
            issues.append({
                "severity": "warn", "instance": "all",
                "message": "no SuperDARN radar within range of the configured "
                           "receiver — check receiver_lat/lon and radars.min/max_range_km",
            })

    return issues
