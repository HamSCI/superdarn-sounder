# superdarn-sounder

A passive **SuperDARN** coherent-radar monitor for the [HamSCI](https://hamsci.org/)
**sigmond** SDR suite. Sibling to `codar-sounder`: it treats SuperDARN's HF
transmissions as a signal of opportunity, received one-way by skywave.

SuperDARN radars transmit a burst of short coherent pulses with deliberately
**non-redundant** inter-pulse spacings (a multi-pulse sequence). They are
GPS-disciplined at the source, scan on a **UT-locked** schedule, and — uniquely
among HF signals of opportunity — publish **openly available ground-truth
metadata** (rawACF: operating frequency, timestamp, control-program id, scan
parameters for every integration period). That makes passive detection both
tractable and *validatable*.

## v0.1 — detection & identification

This release proves reception and rejects interference before any Doppler DSP:

1. Capture a **wideband** IQ slice of the SuperDARN sub-band (default 10–14 MHz)
   from radiod via `ka9q-python` — detect downstream rather than chase the
   radars' clear-frequency hopping.
2. **Detect pulses** with a matched filter keyed to the ~300 µs pulse width
   (`core/pulse_detect.py`).
3. **Identify the sequence** by correlating the inter-pulse spacings against the
   known multi-pulse tables (`core/sequence_match.py`). The non-redundant lag
   structure makes QRM rejection clean.
4. **Tag the beam** from the UT-locked scan cadence (`core/beam_phase.py`) and
   attribute to the nearest audible radar (`core/radars.py`, Fort Hays from
   central Missouri).
5. Write one record per detection to daily JSONL + the additive HamSCI sink.
6. **Validate** against published rawACF with `scripts/validate_against_rawacf.py`.

Direct-path Doppler / dTEC-dt (the eventual primary science product) and
absolute group-delay are Phase 2.

## Quick reference

```bash
# Development
uv sync --extra dev
uv run pytest

# Try the pipeline with no radiod (synthetic 8-pulse sequence):
superdarn-sounder detect-scan --config <cfg> --synthetic --seconds 0.2

# Contract surface
superdarn-sounder inventory --json     # per-instance resource view (exit 0 always)
superdarn-sounder validate --json      # config validation
superdarn-sounder version --json

# Production install (sigmond-suite uv helper; clones hamsci-dsp + ka9q-python siblings)
sudo ./scripts/install.sh
```

## Multi-instance (one reporter per signal source)

Like the sibling clients, superdarn-sounder runs **one systemd instance per
signal source, each recorded and reported under a unique reporter id** — e.g. a
host with two receivers runs `superdarn-sounder@AC0G-SD-bee1` and
`superdarn-sounder@AC0G-SD-bee2`. The instance name (`%i`) is the reporter id;
it stamps every detection row (`reporter_id`) and keys the spool dir
`/var/lib/superdarn-sounder/<reporter-id>/`.

- **Per-instance config** `/etc/superdarn-sounder/<reporter-id>.toml` carries an
  `[instance]` block (`reporter_id = "…"`) plus the one `[[radiod]]` block
  naming that source's radiod. Sigmond's `smd instance migrate` creates these;
  `inventory --json` surfaces `reporter_id` per instance.
- **Legacy single-instance** hosts keep one shared
  `superdarn-sounder-config.toml` with no `[instance]` block — the reporter id
  and spool key fall back to the radiod status, so existing deployments are
  unchanged.

## Vendored reference data

- `data/radars.toml` — site geometry from [SuperDARN/hdw](https://github.com/SuperDARN/hdw).
- `data/pulse_tables.toml` — multi-pulse `ptab` sequences (canonical SuperDARN
  8-/7-pulse patterns; reconcile exact per-cpid tables against
  [SuperDARN/rst](https://github.com/SuperDARN/rst) when tightening mode ID).

## Reusable DSP

The timing-authority client (RTP↔UTC offset) comes from the shared
[`hamsci-dsp`](../hamsci-dsp) library; Phase 2 will add the carrier-phase /
coherent-stack modules there too.

## Author

- Michael Hauan (AC0G) — https://github.com/mijahauan/superdarn-sounder
- Part of [HamSCI](https://hamsci.org/).
