"""The §3 ``timing_authority_applied`` report for one sounder instance.

CLIENT-CONTRACT §18.5 (amendment 2026-09-04): the field describes the LABELS
a client writes, not what it reads.  A sounder instance runs one IQ source per
tracked radar (or one blind source), and each source pins its own
:class:`hamsci_dsp.timing.AnchorUTC` through ``acquire_anchor_utc``.  The
instance's labels ride the authority only when every anchored source's do.
A mixed state stays legal (§18.7) but must stay visible, so it reports null,
never an average.  The channel counts travel with the block so a reader can
see how many labels the report stands for.

The daemon writes the result once a minute to
``<output_dir>/<inst_key>/timing-authority.json`` through
``hamsci_dsp.timing.write_applied_state``.  ``superdarn-sounder inventory
--json`` reads it back through ``read_applied_state``.  Both sides take the
path from :func:`applied_state_path` and the instance key from
:func:`instance_key`, so one rule names the file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from hamsci_dsp.timing import AnchorUTC, applied_state_for_anchors

from superdarn_sounder.config import radiod_blocks

APPLIED_STATE_FILENAME = "timing-authority.json"


def instance_key(config: dict, block: dict) -> str:
    """The spool / instance key for ``block`` under ``config``.

    The ``[instance]`` reporter id keys a one-reporter-per-config instance.
    A legacy config without that block, or a config with several ``[[radiod]]``
    blocks (one reporter id cannot disambiguate them), falls back to the radiod
    status name.  ``contract.build_inventory`` applies the same rule.
    """
    reporter_id = (config.get("instance") or {}).get("reporter_id") or None
    single_block = len(radiod_blocks(config)) == 1
    radiod_id = str(block.get("status", ""))
    return reporter_id if (reporter_id and single_block) else radiod_id


def applied_state_path(output_root, inst_key: str) -> Path:
    """Where the daemon leaves its applied block and inventory finds it."""
    return Path(output_root) / inst_key / APPLIED_STATE_FILENAME


def applied_state_for(
    anchors: Iterable[Optional[AnchorUTC]],
    client_radiod: Optional[str],
    now_fn: Optional[Callable[[], float]] = None,
) -> Optional[dict]:
    """Aggregate the sources' anchors into one §3 block, or None.

    Thin wrapper over the suite-shared rule in
    ``hamsci_dsp.timing.applied_state_for_anchors``: populated iff at least
    one source has anchored and every anchored source carries the offset;
    ``channels`` counts attached.
    """
    return applied_state_for_anchors(anchors, client_radiod=client_radiod,
                                     now_fn=now_fn)
