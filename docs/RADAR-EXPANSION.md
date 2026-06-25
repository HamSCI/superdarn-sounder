# SuperDARN coverage — current build & multi-site radar expansion

How superdarn-sounder works today, and how it grows from "Fort Hays from central
Missouri" to **any sigmond site listening to whatever SuperDARN radars its
location and the ionosphere make reachable**. Written 2026-06-25; reconcile
against the code as it evolves.

The governing fact: sigmond deploys at many locations, and reception is a
function of **where you are** (which radars are in range and how their beams are
oriented relative to you), **what time/season it is** (HF propagation opens and
closes), and **whether you can learn a radar's live operating frequency** (it
hops every scan). Expansion is mostly about making the client *discover and adapt
to* those three things per site instead of being hand-configured for one.

---

## 1. Current build (v0.1 + tracking)

**What it is.** A passive monitor that treats SuperDARN HF transmissions as
signals of opportunity, received one-way by skywave, and **detects + identifies**
them (no Doppler yet — that's Phase 2). Per IQ frame:

```
detect_pulses (~300 µs matched filter)
  → sequence_match (correlate inter-pulse spacings vs data/pulse_tables.toml)
    → beam_phase (UT-locked beam-dwell index)
      → record  (JSONL + additive HamSCI sink)
```

**Instance model (important — see `CLAUDE.md`).** One **receiver = one instance =
one `reporter_id`**, bound to one radiod. A single instance hears **many radars**;
the radar is a per-record field (`candidate_radar`), *not* an instance key — the
same way a wspr-recorder instance reports many bands or a codar-sounder instance
reports many transmitters. You add a second instance only for a second *receiver*,
never per radar.

**Two capture modes** (`core/daemon.py`):

- **Blind** — capture a fixed `[[radiod.band]]` window; detect whatever is
  audible in it. Simple, no external dependency, but the radars clear-frequency
  search and hop ~every scan, so a fixed window loses them (sigma overnight: a
  Fort Hays catch at 11.1 MHz that vanished at dawn when it moved to 10.8 MHz).
- **Tracking** (`[tracking] enabled=true, radars=[...]`) — follow each radar's
  **live** operating frequency from the VT real-time feed, re-tuning a dedicated
  radiod channel as it hops. `core/tracking.py:TrackedSource` is the per-radar
  primitive (one channel, re-tune on hop, blind-fallback when the feed is down);
  the daemon runs **one per radar concurrently** under the single `reporter_id`,
  all sharing one VT socket.io connection and funnelling detections through one
  SQLite writer thread. Detections are attributed to the tracked radar
  (`candidate_via = "tracked-frequency"`).

**Radar attribution.** Tracking → the radar is known (we tuned to it). Blind →
nearest-audible *geometric guess* from `core/radars.py:audible_radars`
(great-circle range to each radar in `data/radars.toml`).

**Outputs.** Daily JSONL `/var/lib/superdarn-sounder/<reporter_id>/YYYY/MM/DD.jsonl`
is canonical; the additive HamSCI sink writes rows into
`/var/lib/sigmond/sink.db` (`pending_uploads`, `target_db='superdarn'`). Both
carry `reporter_id` + `candidate_radar`.

**Reference data.** `data/radars.toml` — site geometry from
[SuperDARN/hdw](https://github.com/SuperDARN/hdw) (**currently 8 US radars only**:
fhe, fhw, cve, cvw, bks, wal, ade, adw). `data/pulse_tables.toml` — 8-/7-pulse
sequences (Greenwald 1985, Ribeiro 2013, SuperDARN/rst).

**Validation.** `scripts/validate_against_rawacf.py` cross-checks detections
against published rawACF ground truth (exact tfreq/cp/bmnum per integration).

**Live deployment.** sigma → `superdarn-sounder@AC0G-SD-sigma`, tracking
`["fhe","fhw","bks"]`. Operational constraints that must hold at every site:
the service user in the `sigmond` group (sink write), `/var/lib/sigmond` in the
unit's `ReadWritePaths`, and the instance pinned **off radiod's cores**
(`AFFINITY_UNITS`) so detection bursts don't cost the RX888 USB packet drops.

---

## 2. What governs reception at a site

Three independent variables decide whether a given radar is worth listening to
from a given sigmond location:

1. **Range / path.** Great-circle distance sets the propagation mode (ground
   wave is irrelevant at HF here; it's all skywave/skip). ~600–2000 km one-hop is
   the sweet spot; nearer can be in the skip zone, farther needs multi-hop.
   `audible_radars` filters on `[radars] min_range_km / max_range_km`.

2. **Geometry (beam orientation).** SuperDARN radars are *directional* — a 16-beam
   fan about a fixed boresight. A receiver off the main lobe hears the radar only
   via side/back lobes (much weaker). US radars beam poleward; a mid-latitude US
   receiver sits off their main lobe (see `docs/OBSERVING.md` §4). **Today this is
   not modelled** — `audible_radars` ranks purely by distance and ignores the
   boresight-vs-bearing angle it already computes.

3. **Propagation (time/season).** The path MUF rises and falls diurnally and
   seasonally; a radar can be on-air and in range yet inaudible. sigma's first
   night showed this cleanly — Fort Hays detections peaked 04:00 UT, then died
   after ~07–08 UT as the path closed, while the radar kept transmitting.

A site's *useful* radar set is the intersection of "in range + favourably
oriented + currently propagating", and it changes through the day.

---

## 3. Knowing the live frequency — the gating constraint for "other radars"

A radar hops its operating frequency ~every scan, so tracking needs a live
frequency source. What's available depends on the radar:

- **VT real-time feed** (`core/vt_realtime.py`, socket.io, per-abbr events).
  Verified 2026-06-25 to carry the **entire North American sector**, not just the
  VT-operated radars: live data for fhe, fhw, bks **and** the Canadian chain
  (sas, kap, pgr, rkn, inv, cly) — 9 radars in one probe. So **any North American
  radar can be VT-tracked today.** (Our earlier "only fhe/fhw/bks" was wrong.)
- **Outside North America** (European, Australasian, Antarctic, SANAE, etc.) the
  VT feed returned nothing — those radars need a different frequency source:
  - **SuperDARN/schedules** (`.swg`) gives the *mode/cpid* per UT block but **not**
    the live clear-frequency (the radar still hops within its band).
  - **Self-tracking** — estimate the operating frequency from our *own* wideband
    capture (detect pulses across a wide slice, measure their carrier). Autonomous,
    works for **any** radar with no external feed. Not yet built.
  - **rawACF** gives exact tfreq but only after the fact (validation, not live).

So radar reach is tiered: **NA radars → VT-track now**; **rest of the network →
self-track (to build) or blind-wideband**.

---

## 4. Expansion roadmap

Phased, each independently shippable. Earlier phases unblock multi-site
deployment; later phases remove the North-America/VT dependency.

### Phase A — vendor the full network geometry
`data/radars.toml` has 8 US radars. Add the rest from SuperDARN/hdw — at minimum
the Canadian chain (sas/kap/pgr/rkn/inv/cly, already VT-trackable and in range of
many NA sites), then the global network (~35 radars, both hemispheres). Extend
each entry with metadata the selector and strategy need: `hemisphere`,
`operator`, and `freq_feed = "vt" | "none"` (whether it's on the VT live feed).

### Phase B — geometry-aware radar selection
Replace distance-only ranking with a **coupling estimate**: combine range, the
boresight-vs-bearing angle (main-lobe vs side/back-lobe), and an expected-path
weight. Surface a per-site ranked list so an operator (or auto-config) picks the
radars actually worth a channel, instead of "nearest 6 by distance".

### Phase C — per-radar capture strategy registry
A radar's `freq_feed` decides how it's captured: `vt` → VT-track (Phase A
metadata drives this); `none` → self-track or blind-wideband. The daemon already
supports a list of tracked radars; this adds the *routing* so one instance can
VT-track its NA radars and (later) self-track the rest, concurrently.

### Phase D — propagation-aware scheduling
Don't hold a channel open on a path that's shut. A diurnal/seasonal MUF model
(or simply learning each path's observed on-hours from the JSONL history) lets the
daemon **duty-cycle** radars — provision a channel when the path is likely open,
release it when it isn't — keeping channel/CPU/network cost bounded as the radar
count grows (see resource budget below).

### Phase E — self-tracking (remove the VT dependency)
Estimate a radar's live operating frequency from a wideband capture: detect
pulses across a multi-hundred-kHz slice, measure the carrier offset, and tune a
narrow channel onto it. This makes **any** radar trackable worldwide with no
external feed, and is the right long-term substrate even for NA (VT can be down —
it was, transiently, on the night of 2026-06-24). Watch the density-inflation
caveat in `docs/OBSERVING.md` §6 when multiple radars share the wide slice.

### Phase F — site auto-configuration
`config init` derives the receiver's audible+oriented radar set from its lat/lon
(Phase B), tags each with its capture strategy (Phase C), and writes a starting
`[tracking] radars = [...]`. The operator confirms rather than hand-picks. This
is what makes "deploy sigmond at a new location" a one-command operation.

---

## 5. Per-site configuration model

A site is defined by its **receiver location** (`[station] receiver_lat/lon`,
`grid_square`) and its **radiod** (`[[radiod]] status`). From those:

1. `audible_radars(lat, lon)` → radars in range (Phase B: + geometry ranking).
2. Split by `freq_feed`: VT-trackable (NA) vs not.
3. `[tracking] radars = [...]` = the VT-trackable, favourably-oriented subset the
   site can afford channels for; the rest stay blind-window or (Phase E)
   self-tracked.

Concrete examples of how location changes the answer:

| Site (example) | Likely in-range radars | Live-freq source |
|---|---|---|
| Central US (sigma) | fhe/fhw, bks/wal, Canadian chain | VT-track all |
| Pacific NW US | cve/cvw, ade/adw, Canadian west | VT-track all |
| Western Europe | han/pyk/sto/gbr (+ Iceland) | self-track / schedule |
| Australia / NZ | tig/bpk/ker + Antarctic | self-track / schedule |

The point: **the same client binary, adapted by location** — North-American sites
get full VT tracking today; other regions get blind/self-track until Phase E.

### Resource budget (scales with radar count)
Each tracked radar = one radiod IQ channel + one worker thread. At 100 kHz/channel
that's ~13 Mbps multicast and a slice of CPU each. sigma runs 3 comfortably at
~58 MB. A site auto-selecting ~9 NA radars is ~115 Mbps and 9× processing —
real cost. Mitigations: cap the tracked set (Phase B ranking picks the best N),
duty-cycle by propagation (Phase D), and raise the unit's `MemoryMax` with the
channel count. Keep the affinity pin (cores off radiod) regardless of N.

---

## 6. Invariants to preserve through expansion

- **Instance = receiver, not transmitter.** No matter how many radars a site
  tracks, it stays one instance / one `reporter_id` per receiver.
- **Blind-fallback never goes silent.** Any tracker degrades to blind capture
  when its frequency source is unavailable.
- **JSONL is canonical; the sink is additive.** Standalone hosts work file-only.
- **Off radiod's cores.** Every added channel is more numpy work — the
  `AFFINITY_UNITS` pin is what protects the RX888.
