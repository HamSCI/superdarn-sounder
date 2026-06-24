# Observing SuperDARN passively — sources, frequencies, geometry

Field notes for finding a SuperDARN signal to detect. Compiled 2026-06-24 from
the live network; reconcile against the sources as they evolve.

## 1. Schedule — what mode is running, when

Monthly schedule files: **https://github.com/SuperDARN/schedules** (`YYYYMM.swg`,
e.g. `2026/202606.swg`). Format: UT time blocks tagged Common / Special /
Discretionary Time. Nearly all common-time modes are 1-minute, UT-locked scans
(`normalscan`, `themisscan`, `rbspscan`, interleave), so the beam-scan cadence
`beam_phase.py` assumes holds during common time.

- Rendered calendar: https://superdarn.ca/radar-schedule (historical/planning).
- Control-program IDs (cpid): https://superdarn.ca/cpid-info
- Scheduling WG: https://superdarn.thayer.dartmouth.edu/WG-sched/issues.html

Example (June 2026): common-time `normalscan` 06:00–15:00 UT, a `normalsound`
frequency-sweep block 03:00–06:00 UT, then discretionary after 15:00 UT on the
24th.

## 2. Live operating frequency — the radars hop

SuperDARN does a clear-frequency search and **re-tunes roughly every scan
(~1 min)**, so the operating frequency moves by tens of kHz to a different band
between captures. You must read the *current* frequency and scan immediately.

**VT real-time feed** (VT operates Fort Hays + Blackstone). The UI at
`http://vt.superdarn.org/plot/real-time/echoes` shows Status / Beam / Op-mode /
Frequency(kHz) per radar. Under the hood (reverse-engineered):

- REST echo counts: `GET https://vt.superdarn.org/echoes?site_name=<abbr>`
  → JSON `{timestamp[], total_echoes[], ionospheric_echoes[], ground_scatter_echoes[]}`
  (a recent `timestamp` = the radar is online).
- Live status via **socket.io** (origin `https://vt.superdarn.org` over 443, or
  `http://vt.superdarn.org:81` over http): event named `"<abbr>"` pushes
  `{freq: <kHz>, beam: <n>, ...}`; event `"<abbr>/echoes"` pushes echo arrays.
  A minimal `python-socketio` client that registers `sio.on("fhe", ...)` etc.
  reads the live frequency. (`superdarn-sounder` could grow an auto-tune mode
  that drives `detect-scan`'s centre from this feed — see §5.)

Observed live values 2026-06-24 ~12:1x UT (illustrative — they hop):
`fhe 10.8–11.1`, `fhw 11.0–11.1`, `bks 11.6–11.7 MHz`; Canadian radars
(kap/sas/pgr/rkn/inv/cly) clustered 10.4–10.9 MHz.

Other frequency sources: 24-h **summary plots** (`tfreq` panel) at
https://superdarn.ca/summary-plots; **rawACF/fitACF** downloads
(https://superdarn.ca/data-download) carry `tfreq`/`cp`/`bmnum` per integration
(the ground truth `scripts/validate_against_rawacf.py` consumes). Network range:
8–20 MHz, most radars 10–14 MHz.

## 3. Which radars to try from central Missouri (EM38ww)

`radars.py` ranks by distance: Fort Hays (FHE/FHW, ~600 km) closest, then
Blackstone/Wallops (~1100 km), Christmas Valley (~2200 km), Adak far.

## 4. The hard part: beam geometry

**US SuperDARN radars beam poleward (north).** Boresights (from `data/radars.toml`):
FHE +45°, FHW −25°, BKS −40°, WAL +36° — all point N/NE toward the auroral zone.
A mid-latitude central-US receiver sits **off the main lobe** of these radars, so
the direct path arrives mainly via antenna side/back-lobes — much weaker than a
main-beam illumination. Combined with HF skip geometry (~600–1100 km can be near
the skip zone depending on band/time) and the ~1-minute frequency hopping, a
*blind* fixed-frequency capture rarely lands a clean burst.

Best odds: read the live frequency (§2), scan that exact centre with a ±75 kHz
window, and dwell across at least one full 1-minute scan so the beam sweeps
through the geometry that best couples to us. Lower bands (10–11 MHz) and
night/terminator hours generally favour the closer paths.

## 5. Status of live attempts (2026-06-24)

Validated end-to-end against sigma's RX-888 Mk2: live-frequency read → targeted
`detect-scan` → pipeline → channel cleanup all work. One **marginal, unconfirmed**
candidate at Blackstone (8 pulses, partial 7-pulse fit, score 0.57, ~8 dB over
noise) did **not** reproduce on a 30 s dwell after the radar hopped frequency —
consistent with a brief side-lobe burst or QRM, not a confirmed detection.

**Next step to actually confirm:** an auto-tracking mode — poll the VT real-time
socket for a target radar's frequency and continuously re-tune the scan to it,
dwelling through full scans — so we're always on-frequency when its beam/geometry
favours us. That removes the frequency-hop coincidence, which is currently the
dominant reason blind captures miss.
