"""Sigmond TUI hooks: receiver-channels parser.

Consumed by sigmond's lib/sigmond/client_features.py (declared in deploy.toml
[client_features.receiver_channels]).  Reports the wide IQ channel(s) the
daemon monitors so the TUI Receiver-channels screen can show them.
"""
from __future__ import annotations

from typing import Any


def parse_receiver_channels(inventory: dict) -> list[dict[str, Any]]:
    """Map an inventory payload to receiver-channel rows (one per band)."""
    rows: list[dict[str, Any]] = []
    for inst in inventory.get("instances", []):
        rid = inst.get("radiod_id", "")
        for hz in inst.get("frequencies_hz", []):
            rows.append({
                "radiod_id": rid,
                "channel": f"superdarn-{int(hz) // 1_000_000}mhz",
                "frequency_hz": hz,
                "mode": "iq",
            })
    return rows
