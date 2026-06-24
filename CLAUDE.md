# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

**superdarn-sounder** is a passive SuperDARN coherent-radar monitor for the
HamSCI sigmond suite. SuperDARN radars transmit multi-pulse sequences (bursts of
~300 µs pulses with non-redundant inter-pulse spacings) on HF, GPS-disciplined
and UT-scan-locked, with openly published rawACF ground-truth metadata. This
client receives those transmissions one-way by skywave and, in **v0.1**,
**detects and identifies** them (it does not yet measure Doppler).

Sibling to `codar-sounder` (same Pattern-A layout and contract surface) but the
DSP is different: SuperDARN is multi-pulse, not FMCW, so there is no dechirp —
the new work is pulse detection + multi-pulse-sequence correlation.

Part of the sigmond suite — see `/opt/git/sigmond/sigmond/CLAUDE.md`
(orchestrator) and `/opt/git/sigmond/CLAUDE.md` (umbrella).

## Architecture

```
radiod (ka9q-radio, iq preset)
  │   wide IQ channel over the SuperDARN sub-band (default 10–14 MHz),
  │   ensure_channel(low_edge,high_edge) so the band isn't clipped.
  ▼
core/stream.py   RadiodIQSource (ka9q-python) | SyntheticIQSource (tests/dev)
  ▼
core/daemon.py:process_frame  (pure; also backs `detect-scan`)
  ├─ core/pulse_detect.py   matched filter for ~300 µs pulses → pulse epochs
  ├─ core/sequence_match.py correlate inter-pulse spacings vs data/pulse_tables.toml
  │                         → sequence ID + τ estimate (or None = QRM)
  ├─ core/beam_phase.py     UT-locked beam-dwell index
  └─ core/radars.py         nearest audible radar (data/radars.toml geometry)
  ▼
core/output.py   daily JSONL + additive sigmond.hamsci_sink (superdarn.detections)
```

Timing: each frame's UTC is anchored off the RTP counter + hf-timestd's
published offset via `hamsci_dsp.timing.AuthorityReader` (the shared library) —
never the host clock. v0.1 detection needs only relative timing; absolute-epoch
work is Phase 2.

## Reuse

- **hamsci-dsp** (shared sibling lib): `AuthorityReader`. Phase 2 will add the
  carrier-phase / coherent-stack DSP there.
- **codar-sounder**: the cli/config/contract/output/systemd/deploy skeleton was
  mirrored from it.

## Quick reference

```bash
uv sync --extra dev
uv run pytest
superdarn-sounder detect-scan --config <cfg> --synthetic --seconds 0.2   # no radiod
superdarn-sounder inventory --json     # exit 0 even configless (contract §3)
sudo ./scripts/install.sh
```

## Vendored data (reconcile on update)

- `data/radars.toml` — from SuperDARN/hdw (hdw.dat.<abbr>).
- `data/pulse_tables.toml` — canonical 8-/7-pulse ptab patterns; exact per-cpid
  tables should be reconciled against SuperDARN/rst when tightening mode ID.

## Validation

`scripts/validate_against_rawacf.py` (offline, needs the `validate` extra /
pydarnio) cross-references detections against a downloaded rawACF — the
scientific-rigour hinge for the detection demo.

## Status / roadmap

- **v0.1 (this):** detection + identification, validated against rawACF.
- **Phase 2:** direct-path carrier Doppler / dTEC-dt; absolute group-delay.
- **Phase 3:** bistatic forward-scatter.

## Contract (v0.8)

`src/superdarn_sounder/contract.py` declares `CONTRACT_VERSION = "0.8"`. One
inventory instance per `[[radiod]]` block; publishes `frequencies_hz` +
`ka9q_channels` so sigmond's harmonize rules pass.

## Author

- Michael Hauan (AC0G) — https://github.com/mijahauan/superdarn-sounder
