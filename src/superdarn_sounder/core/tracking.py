"""Frequency tracking — follow a SuperDARN radar's live operating frequency.

SuperDARN radars do a clear-frequency search and re-tune roughly every scan
(~1 min), so a passive receiver staring at a fixed centre loses them when they
hop (observed on sigma: a full overnight catch of Fort Hays at 11.1 MHz that
vanished at dawn when the radar moved to 10.8 MHz).  ``TrackedSource`` reads the
radar's live frequency from the VT real-time feed (``core/vt_realtime.py``) and
re-provisions the radiod IQ channel as it moves, yielding ``(frame, utc,
center_hz)``.  Both ``detect-scan --track`` and the daemon's track mode consume
it, so the re-tune logic lives in one tested place.

Robust for long-running daemon use:

* **Capture never blocks on VT.**  The VT socket.io connect runs in a background
  thread; the capture loop only reads the cached latest frequency.  Until VT is
  connected (or if it is down, as it was on sigma overnight), the source falls
  back to ``fallback_center_hz`` — i.e. it degrades to a blind capture at the
  configured centre rather than going silent — and picks up the live frequency
  as soon as the feed arrives.
* **Self-healing.**  ``VTRealtimeClient`` reconnects on drop; if the initial
  connect fails the background thread keeps retrying, so the daemon recovers
  when VT comes back without a restart.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Iterator, Optional

from superdarn_sounder.core.stream import RadiodIQSource
from superdarn_sounder.core.vt_realtime import VTRealtimeClient

logger = logging.getLogger("superdarn_sounder.tracking")

# How long to honour a cached VT frequency before treating the radar as "no
# live data" and falling back to the configured centre.
DEFAULT_STALE_S = 180.0
# Re-attempt the VT connect this often while it is down.
_VT_RECONNECT_S = 60.0


class TrackedSource:
    """Yield ``(frame, utc, center_hz)`` following ``radar``'s live frequency.

    ``source_factory(center_hz) -> iterable[(frame, utc)]`` and
    ``vt_factory() -> VTRealtimeClient`` are injectable for testing without a
    radiod or the network.
    """

    def __init__(
        self,
        *,
        radiod_status_dns: str,
        radar: str,
        sample_rate_hz: float,
        frame_seconds: float,
        fallback_center_hz: float,
        retune_hz: float = 30_000.0,
        lifetime_frames: Optional[int] = None,
        stale_s: float = DEFAULT_STALE_S,
        source_factory: Optional[Callable[[float], object]] = None,
        vt_client: Optional[object] = None,
        vt_factory: Optional[Callable[[], object]] = None,
    ):
        self.radiod_status_dns = radiod_status_dns
        self.radar = radar
        self.sample_rate_hz = float(sample_rate_hz)
        self.frame_seconds = float(frame_seconds)
        self.fallback_center_hz = float(fallback_center_hz)
        self.retune_hz = float(retune_hz)
        self.lifetime_frames = lifetime_frames
        self.stale_s = float(stale_s)
        self._source_factory = source_factory or self._default_source
        # A caller (the daemon tracking several radars) can pass ONE shared VT
        # client subscribing to all of them, so there's a single socket.io
        # connection.  When tracking solo (detect-scan, tests) we own a client.
        self._vt = vt_client
        self._owns_vt = vt_client is None
        self._vt_factory = vt_factory or (lambda: VTRealtimeClient([radar]))
        self._stop = False
        self._connector: Optional[threading.Thread] = None

    # -- source / VT construction -------------------------------------------

    def _default_source(self, center_hz: float):
        return RadiodIQSource(
            radiod_status_dns=self.radiod_status_dns,
            center_freq_hz=center_hz,
            sample_rate_hz=self.sample_rate_hz,
            frame_seconds=self.frame_seconds,
            lifetime_frames=self.lifetime_frames,
        )

    def _connect_vt_loop(self) -> None:
        """Background: keep trying to bring up the VT feed until connected."""
        while not self._stop and self._vt is None:
            try:
                vt = self._vt_factory()
                vt.start()
                self._vt = vt
                logger.info("VT real-time feed connected; tracking %s", self.radar)
                return
            except Exception as exc:
                logger.warning(
                    "VT feed unavailable (%s); capturing blind at %.3f MHz, "
                    "retrying in %ds",
                    exc, self.fallback_center_hz / 1e6, int(_VT_RECONNECT_S))
                time.sleep(_VT_RECONNECT_S)

    def _target_center(self) -> float:
        """Live frequency if fresh, else the configured fallback centre."""
        vt = self._vt
        if vt is not None:
            st = vt.current(self.radar, max_age_s=self.stale_s)
            if st is not None:
                return float(st.freq_khz) * 1000.0
        return self.fallback_center_hz

    # -- iteration -----------------------------------------------------------

    def __iter__(self) -> Iterator[tuple]:
        # Only manage our own VT connection when we weren't handed a shared one.
        if self._owns_vt:
            self._connector = threading.Thread(
                target=self._connect_vt_loop, name="vt-connect", daemon=True)
            self._connector.start()

        src = None
        cur_center: Optional[float] = None
        it = None
        try:
            while not self._stop:
                target = self._target_center()
                if src is None or abs(target - cur_center) > self.retune_hz:
                    if src is not None and hasattr(src, "stop"):
                        try:
                            src.stop()
                        except Exception:
                            pass
                    cur_center = target
                    logger.info("tuning %s -> %.3f MHz",
                                self.radar, cur_center / 1e6)
                    src = self._source_factory(cur_center)
                    it = iter(src)
                try:
                    frame, utc = next(it)
                except StopIteration:
                    # Channel lifetime elapsed (one-shot/bounded source) — let
                    # the next loop re-provision (or exit if stopped).
                    src = None
                    if self.lifetime_frames is not None:
                        break
                    continue
                yield frame, utc, cur_center
        finally:
            if src is not None and hasattr(src, "stop"):
                try:
                    src.stop()
                except Exception:
                    pass
            self.stop()

    def stop(self) -> None:
        self._stop = True
        # Only tear down a VT client we own; a shared one is the daemon's.
        if self._owns_vt and self._vt is not None:
            try:
                self._vt.stop()
            except Exception:
                pass
