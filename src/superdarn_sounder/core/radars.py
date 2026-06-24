"""Which SuperDARN radars are audible from the receiver.

A passive node hears a radar's *direct* transmission by skywave; the strongest,
most reliable targets are the nearest radars with a favourable path (the Fort
Hays pair from central Missouri).  This module filters the vendored radar
geometry (data/radars.toml) by great-circle distance from the receiver and
returns the candidate set the detector should expect, sorted nearest-first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from superdarn_sounder.config import (
    haversine_km,
    initial_bearing_deg,
    load_radars,
)


@dataclass(frozen=True)
class RadarCandidate:
    abbr: str
    id: int
    name: str
    lat: float
    lon: float
    boresight: float
    beam_sep: float
    n_beams: int
    distance_km: float
    bearing_deg: float        # from receiver toward the radar
    hemisphere: str

    def to_dict(self) -> dict:
        return {
            "abbr": self.abbr,
            "id": self.id,
            "name": self.name,
            "distance_km": round(self.distance_km, 1),
            "bearing_deg": round(self.bearing_deg, 1),
            "boresight": self.boresight,
            "beam_sep": self.beam_sep,
            "n_beams": self.n_beams,
        }


def audible_radars(
    receiver_lat: float,
    receiver_lon: float,
    *,
    min_range_km: float = 200.0,
    max_range_km: float = 4000.0,
    only: Optional[list[str]] = None,
    radars: Optional[dict[str, dict]] = None,
) -> list[RadarCandidate]:
    """Return radars within [min_range_km, max_range_km] of the receiver.

    ``only`` (a list of abbreviations) restricts the set when non-empty.
    ``radars`` overrides the vendored table (for tests).  Sorted by distance.
    """
    table = radars if radars is not None else load_radars()
    only_set = {a.lower() for a in (only or [])}
    out: list[RadarCandidate] = []
    for abbr, r in table.items():
        if only_set and abbr.lower() not in only_set:
            continue
        try:
            lat = float(r["lat"])
            lon = float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        d = haversine_km(receiver_lat, receiver_lon, lat, lon)
        if d < min_range_km or d > max_range_km:
            continue
        out.append(RadarCandidate(
            abbr=abbr,
            id=int(r.get("id", 0)),
            name=str(r.get("name", abbr)),
            lat=lat,
            lon=lon,
            boresight=float(r.get("boresight", 0.0)),
            beam_sep=float(r.get("beam_sep", 3.24)),
            n_beams=int(r.get("n_beams", 16)),
            distance_km=d,
            bearing_deg=initial_bearing_deg(receiver_lat, receiver_lon, lat, lon),
            hemisphere=str(r.get("hemisphere", "N")),
        ))
    out.sort(key=lambda c: c.distance_km)
    return out
