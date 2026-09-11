# superdarn-sounder — Requirements Specification

**Status:** v0.1 baseline (retroactive). **Owner:** Michael Hauan (AC0G).
**Last reconciled against code:** superdarn-sounder `0.1.0` (2026-06-25).
**Prefix:** `SDS`.

> Pilot #2 of [sigmond/docs/REQUIREMENTS-TEMPLATE.md](https://github.com/HamSCI/sigmond/blob/main/docs/REQUIREMENTS-TEMPLATE.md),
> chosen to validate the template at the **Early** end of the maturity range
> (cf. the mature hf-timestd pilot). Expect a high proportion of `🟡`/`⬜` and
> `[NEW]` — that is the honest picture of a v0.1 component, and the point of the
> exercise. Interface requirements are referenced from the
> [client contract](https://github.com/HamSCI/sigmond/blob/main/docs/CLIENT-CONTRACT.md)
> (v0.8), not restated (§8.3). Tags: `[DOC]`/`[CODE]`/`[NEW]`; `✅`/`🟡`/`⬜`.

## 1. Context & problem statement

SuperDARN coherent-scatter radars are GPS-disciplined, UT-scan-locked HF
transmitters whose operating parameters (frequency, timestamp, control program,
beam) are openly published as rawACF metadata. That makes them ideal **signals
of opportunity**: a station can passively receive them by skywave and, because
ground truth is published, *validate* its own detections. superdarn-sounder
turns those transmissions into an HF ionospheric-science data source — without
requiring radar time or any cooperation from the transmitter.

v0.1 deliberately scopes to **detection and identification only** (is a
SuperDARN sequence present, from which radar, on which beam?), establishing a
validated foundation before the harder Phase 2 science (carrier-phase Doppler /
dTEC-rate) is layered on. The defining v0.1 principle: *prove you can find and
correctly attribute the signal, checked against rawACF, before claiming any
derived geophysical product.*

## 2. Goals & objectives

- Passively **detect** SuperDARN multi-pulse sequences and **identify** the
  sequence type, candidate radar, and beam, from skywave alone.
- Be **validatable** against published rawACF ground truth (frequency,
  timestamp, control program).
- Emit detections as durable per-station JSONL + additive shared-sink rows.
- Run as a well-behaved suite client (multi-instance, off radiod cores,
  timing-authority aware) *and* standalone.
- Lay the data foundation (per-pulse carrier phasor, UT-locked timing) for the
  Phase 2 science products without yet claiming them.

## 3. Non-goals / out of scope

- **Phase 2 science in v0.1** — carrier-phase Doppler, dTEC/dt, oblique virtual
  height / MUF are explicitly *deferred* (stubs exist; not wired to output).
- **Bistatic forward-scatter** (Phase 3) — needs per-receiver geometry model.
- **Transmitting / interrogating** the radars — strictly one-way passive.
- **Absolute timing discipline** — it *consumes* the suite timing authority; it
  does not produce one (that is hf-timestd).

## 4. Stakeholders & actors

Station operator · `radiod` (wideband HF IQ source, required) · `hf-timestd`
(timing-authority producer, optional) · the VirginiaTech (VT) real-time
frequency feed (optional, tracking mode) · the shared SQLite sink + downstream
science consumers · sigmond (multi-instance lifecycle, CPU affinity, status) ·
the SuperDARN/hdw + SuperDARN/rst reference data (radar geometry, pulse tables) ·
rawACF archives (validation ground truth).

## 5. Assumptions & constraints

- `SDS-C-001` `[DOC]` ✅ `radiod` SHALL provide a **wideband** IQ channel
  (default 10–14 MHz, ≥50 kHz sample rate, F32LE) provisioned via ka9q-python
  `ensure_channel` — not the ±5 kHz audio-filtered path.
- `SDS-C-002` `[CODE]` ✅ v0.1 detection SHALL require only **relative** timing
  within a frame; absolute-epoch alignment is a Phase 2 concern.
- `SDS-C-003` `[DOC]` ✅ One systemd instance SHALL run **per receiver**
  (reporter_id), not per radar; exactly one `[[radiod]]` block per instance.
- `SDS-C-004` `[CODE]` ✅ Reference data (`data/radars.toml`,
  `data/pulse_tables.toml`) SHALL be vendored from SuperDARN/hdw and
  SuperDARN/rst and reconciled on upstream change.

## 6. Functional requirements

### 6.1 Acquisition
- `SDS-F-001` `[DOC]` ✅ SHALL capture wideband IQ frames (default 1 s,
  100 ksps) from radiod via `RadiodIQSource`, with a `SyntheticIQSource` for
  test/dev (`--synthetic`).
- `SDS-F-002` `[CODE]` 🟡 Config SHALL accept multiple `[[radiod.band]]` blocks,
  but the v0.1 daemon SHALL monitor **only the first** (multi-band via ka9q
  MultiStream is scaffolded, not wired). *(gap — `SDS-F-090`.)*

### 6.2 Detection & identification (the v0.1 core)
- `SDS-F-010` `[DOC]` ✅ SHALL detect pulses by matched filter (~300 µs boxcar),
  emitting pulse epochs + per-pulse SNR above noise.
- `SDS-F-011` `[DOC]` ✅ SHALL identify the multi-pulse sequence by correlating
  inter-pulse spacings against canonical 7-/8-pulse tables, returning
  `{sequence_name, tau_us_est, score, modes[]}` or `None` (QRM) below
  `match_score_threshold`.
- `SDS-F-012` `[DOC]` ✅ SHALL estimate the beam index from the UT-locked scan
  cadence (default 60 s scan, 16 beams, 3.75 s integration).
- `SDS-F-013` `[DOC]` ✅ SHALL attribute a candidate radar by great-circle range
  from the receiver (`data/radars.toml`), and list all audible candidates.
- `SDS-F-014` `[CODE]` ✅ SHALL store the strongest pulse's coherent **carrier
  phasor** in each record (the substrate for Phase 2 Doppler), even though v0.1
  does not analyse it.
- `SDS-F-015` `[NEW]` 🟡 Sequence-match scoring SHALL be robust on busy frames;
  v0.1 is known to **density-inflate** the score when many sequences overlap
  (needs PRI-windowing + a purity term). *(gap — open.)*
- `SDS-F-016` `[NEW]` ⬜ Radar selection SHALL account for boresight-vs-bearing
  main-lobe coupling; v0.1 ranks by **distance only** (US radars beam poleward;
  a mid-latitude receiver sits off the main lobe). *(Phase B.)*

### 6.3 Tracking (optional)
- `SDS-F-020` `[DOC]` ✅ With `[tracking].enabled`, SHALL follow a radar's live
  transmit frequency via the VT real-time feed (one tracked source per radar,
  `track` extra), retuning when the frequency moves past `retune_threshold_hz`.
- `SDS-F-021` `[DOC]` ✅ SHALL fall back to blind fixed-band capture when the VT
  feed is unavailable.

### 6.4 Output
- `SDS-F-030` `[DOC]` ✅ SHALL write daily JSONL to
  `/var/lib/superdarn-sounder/<reporter_id>/YYYY/MM/DD.jsonl` (one row per
  identified sequence), rotated at UTC midnight.
- `SDS-F-031` `[DOC]` ✅ SHALL additively write a projected row to the shared
  sink table `superdarn.detections` (schema `superdarn:1`), no-op if the sink is
  absent (JSONL remains the canonical artefact).
- `SDS-F-032` `[CODE]` ✅ Each record SHALL carry a `timing_authority` provenance
  block (source = `hf-timestd` | `rtp-default` | `unavailable`).

### 6.5 Self-description & config (contract surface)
- `SDS-F-040` `[CODE]` ✅ SHALL implement `inventory --json` / `validate --json`
  / `config init|edit|show|apply` per contract v0.8 (see §8.3).
- `SDS-F-041` `[CODE]` ✅ `validate` SHALL fail on missing receiver lat/lon, no
  `[[radiod]]` block, a radiod without `status`, a band missing
  `id`/`center_freq_hz`/`sample_rate_hz`, or tracking-enabled with no radars;
  and warn when no radar is audible at the receiver location.

### 6.6 Validation against ground truth
- `SDS-F-050` `[DOC]` 🟡 SHALL be checkable against rawACF ground truth
  (`scripts/validate_against_rawacf.py`, `validate` extra / pydarnio). This is an
  **offline script**, not yet wired into the daemon or CI. *(gap — `SDS-F-091`.)*

## 7. Quality / non-functional requirements

- `SDS-Q-001` `[DOC]` ✅ The detector SHALL run off radiod's CPU cores (via
  sigmond `AFFINITY_UNITS`) so burst processing cannot induce RX888 USB drops.
- `SDS-Q-002` `[CODE]` ✅ The service SHALL be `Type=notify` with `WatchdogSec=180`
  and always-restart (RestartSec=5, burst-capped).
- `SDS-Q-003` `[CODE]` ✅ Sink writes SHALL degrade to a graceful no-op when the
  shared DB is unavailable; JSONL output SHALL be unaffected.
- `SDS-Q-004` `[CODE]` ✅ The core SHALL be a **pure** `process_frame` function
  backing both the daemon and the `detect-scan` CLI (one code path, testable
  with synthetic IQ).
- `SDS-Q-005` `[CODE]` 🟡 Memory SHALL be bounded (512 M unit limit) as tracked
  radars scale; propagation-aware duty-cycling to enforce this is **Phase D**,
  not yet built. *(gap.)*

## 8. External interfaces

### 8.1 Inputs
- radiod wideband IQ via ka9q-python (`status` mDNS name; band `id` /
  `center_freq_hz` / `sample_rate_hz`).
- `/etc/superdarn-sounder/<instance>.toml` (or shared
  `superdarn-sounder-config.toml`). Operator MUST set: `[station].receiver_lat`
  / `receiver_lon`; ≥1 `[[radiod]]` + `[[radiod.band]]`. Optional: `[detection]`
  thresholds, `[radars]` range/only, `[beam_scan]`, `[tracking]`.
- Optional VT real-time feed (socket.io). Optional rawACF (validation).
- `hf-timestd` authority (see §8.3) and `/etc/sigmond/coordination.env` identity.

### 8.2 Outputs
- Daily JSONL under `/var/lib/superdarn-sounder/<reporter_id>/`.
- Shared sink `superdarn.detections` (`schema superdarn:1`): `time`,
  `host_call`, `host_grid`, `radiod_id`, `reporter_id`, `processing_version`,
  `center_freq_hz`, `snr_db`, `candidate_radar`, `sequence_name`, `mode_guess`,
  `tau_us_est`, `sequence_match_score`, `n_pulses`, `beam_index_est`,
  `carrier_phase_rad`, `carrier_amp`.
- Per-instance process log `/var/log/superdarn-sounder/<reporter_id>.log`.

### 8.3 Contracts / APIs (reference, not restated)
- `SDS-I-001` `[CODE]` ✅ Conforms to **client contract v0.8** (multi-instance);
  `deploy.toml` declares `templated_units=["superdarn-sounder@.service"]`,
  `requires=[ka9q-python, ka9q-radio]`, `uses=[ka9q-python, hamsci-dsp]`,
  `start_priority=200`, and a §15 `radiod.fragment`. `inventory` declares
  `data_sinks=[file: superdarn-sounder:1]`, `data_path` radiod-ka9q-python.
- `SDS-I-002` `[CODE]` ✅ **Timing-authority subscriber (§18).** Every IQ
  source anchors its frames through `hamsci_dsp.timing.acquire_anchor_utc`.
  That helper applies hf-timestd's published RTP→UTC offset whenever
  `/run/hf-timestd/authority.json` reads fresh, so the frame labels ride the
  authority. Each record also carries a `timing_authority` provenance block,
  with `standalone_timing_authority` as the marker when hf-timestd is absent.
  `inventory` reports `uses_timing_calibration=true` (the capability) and reads
  `timing_authority_applied` from the block the running daemon writes once a
  minute to `<output_dir>/<inst_key>/timing-authority.json`
  (`core/applied_state.py`). The CLIENT-CONTRACT §18.5 amendment of
  2026-09-04 defines that field as a description of the labels the client
  writes. A stale or absent file reads as null. Subscriber obligations remain
  the contract's.

## 9. Data requirements

JSONL record (canonical) carries the full detection incl. `sequence{...}`,
`audible_candidates`, `beam_phase{...}`, `carrier_phasor [re,im]`, and the
`timing_authority` block. Sink row is a flat projection (above). Retention:
`data_sinks.retention_days=365`, ~5 MB/day. Reference data: `radars.toml`
(8 US radars in v0.1; global expansion is Phase A), `pulse_tables.toml`
(canonical 7-/8-pulse ptabs).

## 10. Dependencies & development sequence

**Deps:** `radiod` (required), `ka9q-python ≥3.18` + `hamsci-dsp ≥0.8` (editable
siblings), `numpy`, `tomli`. Optional extras: `track` (python-socketio),
`validate` (pydarnio), `dev` (pytest). Hardware: RX888 via radiod (GPSDO-locked);
`hf-timestd` optional.

**Development sequence (intended):**
- **v0.1 (current, live-validated 2026-06-24):** detection + identification;
  fhe @ score 1.00, bks @ 0.86.
- **Phase 2:** carrier-phase Doppler / dTEC-rate (the primary science product),
  oblique virtual height / MUF — bridge absolute group delay from rawACF;
  requires absolute code-epoch PRN alignment (`SDS-F-092`) and **Phase A**
  radar expansion. Code stubs exist in `core/propagation.py`, unwired.
- **Phases A–F (radar/coverage):** A global radar geometry → B geometry-aware
  selection → C capture-strategy registry → D propagation-aware duty-cycling →
  E self-tracking (drop VT dependency) → F site auto-config.
- **Phase 3:** bistatic forward-scatter.

## 11. Acceptance criteria & verification

- Contract conformance → `superdarn-sounder validate --json` (exit 0) via
  `smd status`.
- Detection correctness → `detect-scan --synthetic` (deterministic 8-pulse) +
  live rawACF cross-check (`SDS-F-050`, currently manual/offline).
- Sink/JSONL integrity → record schema stability + graceful no-op when sink
  absent.
- Instance isolation → one `superdarn-sounder@<id>` per receiver, off radiod
  cores, watchdog healthy.

## 12. Risks & open questions

- `SDS-F-090` `[NEW]` 🟡 **Multi-band not wired:** config accepts N bands; daemon
  reads only the first. Either wire ka9q MultiStream or document the single-band
  limit. *(candidate #18 Clients issue.)*
- `SDS-F-091` `[NEW]` ⬜ **rawACF validation not in CI:** ground-truth check is an
  offline script; promote to an automated regression so detection accuracy can't
  silently regress.
- `SDS-F-092` `[NEW]` 🟡 **Absolute code-epoch PRN alignment.** The label
  half of this item closed with `acquire_anchor_utc` and the applied-state
  report (`SDS-I-002`): frame labels carry hf-timestd's offset, and
  `inventory` says so truthfully. The remaining half stays open. The detector
  still measures pulse timing relative to the frame; no code aligns a detected
  sequence to an absolute code epoch, and no product yet depends on the
  anchor's absolute accuracy. Phase 2 must close that half before any
  Doppler/dTEC claim that needs absolute group delay.
- `SDS-F-015` density inflation and `SDS-F-016` geometry-naive selection are the
  two known v0.1 detection-quality limits.
- Radar coverage is 8 US radars (blind) though the VT feed already exposes the
  whole NA sector — Phase A expansion gates real multi-site science.

## 13. Traceability

| Requirement | #18 issue | Verification | PSWS #6 |
|---|---|---|---|
| SDS (overall Phase 2) | Clients: superdarn-sounder — Phase 2 | — | #6:19 (Doppler API) |
| SDS-F-092 (§18 consumption) | superdarn: absolute code-epoch PRN alignment | timing test | #6:50 |
| SDS-F-090 (multi-band) | *(new — file)* | multistream test | — |
| SDS-F-091 (rawACF CI) | *(new — file)* | CI regression | — |
| SDS-F-015 (density inflation) | *(new — file)* | busy-frame fixture | — |
| SDS-F-031 (sink detections) | Clients: superdarn-sounder | sink schema test | #6:31 (sensor integ.) |

*New rows (SDS-F-090/091, SDS-F-015) are this review's surfaced gaps; promote to
the #18 superdarn Phase-2 epic.*
