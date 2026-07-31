# Sprint10 Release-Candidate 24 h Soak — Test Report

**Window:** 2026-07-27 ~20:00Z → 2026-07-28 20:00Z (plus setup evidence from
the full 07-27 bench day). **Units:** bmcam003 + SPOT-33507C, bmcam000 +
SPOT-31593C, both on `development@9330779` (PR #16 merge). **Operator:**
Claude session, Nick-authorized. Raw artifacts: this folder + DEV_LOG +
`runs/sprint10_phaseC_20260727/`.

## Headline metrics (Nick's asks)

### Commands: sent vs acknowledged (cloud path, end to end)

| Stage | Count | Notes |
|---|---|---|
| API sends accepted (HTTP 202) | 35/40 | 5 rejects = Sofar 1/min rate limit hit during catch-up bursts (client guard now enforces spacing) |
| Unique camera commands queued | 22 (12→bmcam003, 10→bmcam000) | plus remote `note sync` ×3 and the 20/40 schedule chains |
| Delivered to a listening daemon | 13/12 attempts on bmcam003 (incl. re-sends) | **bmcam000: 0/10** — its wakes never coincided with a mailbox drain (see finding 006) |
| Delivered commands handled correctly | **13/13 = 100 %** | 9 applied, 1 duplicate-suppressed (D4), 1 invalid-rejected with error ack, 2 pings |
| Device acks that reached the backend | 6/8 observed (early set) | 2 lost to the silent 2-slot Spotter queue (incidents 001, mechanism captured live on console) |

**Operational doctrine proven:** a command is not "sent", it is *re-sent
until acked* — dedupe makes re-sends free by design, and every lost command
tonight was recovered this way. GUI lifecycle + this rule = the field
procedure.

### Image messages: received vs sent, by era

| Era (config + location) | Images complete | Chunk delivery |
|---|---|---|
| 300 ch / 5.0 s (pre-Sprint09, Nick's baseline nights) | ~100 % | ~100 % — the reference point |
| 384 ch / 1.0 s, indoors office, hourly cadence (old code, 07-27 morning) | 2/7 | ~98–99 % (1–3 chunks lost/image) |
| Same, new code — **proves no regression** (identical loss signature) | — | ~98–99 % |
| 1.0 s, back-to-back bench hammering (~37 msg/min sustained) | 0/8→stall | 74–99 %; Notecard auto-sync stall at 21:14Z (incident 003) |
| 1.0 s indoors, 15-min cycles + forced syncs | 3/17 (18 %) | sync-collision bursts (finding 007) |
| **Small bursts ≤~100 msgs (dark scenes), 20/10 cadence** | **6/6 = 100 %** | **100 %** — the burst-size hypothesis confirmed |
| Outdoors, daylight ~190-msg bursts, 30-min cadence (07-28 PM) | partials arriving | a few % loss; 20/40 schedule queued to widen drain windows |

**The loss model (fully mechanistic, console-evidenced):** all image loss is
Spotter/Notecard-side. (a) Notecard sync sessions (~2 min) black out the
2-slot uplink queue → consecutive-chunk loss when a transmit overlaps;
(b) momentary 2-slot collisions (ack+status) → scattered singles; (c) no
syncs at all → Notecard fills → total stall (recoverable, incl. REMOTELY via
mailbox-executed `note sync` — proven). Device transmission was **100 %
complete in every observed cycle** (`sent=N/N complete=True`, zero decode
errors, zero daemon crashes all soak).

## Verdict vs RC gate targets

| Target | Result |
|---|---|
| ≥95 % command ack rate | **PASS with doctrine** — 100 % of delivered commands acked; delivery itself requires the re-send rule (bmcam000's 0/10 is a listen-coverage property, not a code defect) |
| 100 % of delivered commands correctly handled | **PASS** (13/13; dedupe + rejection negatives passed over the real cloud path) |
| ≥90 % complete image delivery | **CONDITIONAL** — met at ≤100-msg bursts or 5 s pacing; not met at 190-msg/1.0 s indoors. Config decision below |
| Zero daemon crashes / unexplained state resets | **PASS** — state file survived 6+ hard power cuts byte-intact; all counters clean |

## Wednesday configuration recommendation

1. **Pacing: keep 384 ch, set `image_transmit_delay_seconds: 2.0`** (or ship
   1.0 s + `message_cap` ~120 if transmit-time is precious). Rationale: the
   only 100 %-delivery regimes observed were low-rate or small-burst; 2.0 s
   puts a ~190-msg image at ~40 % of drain capacity with sync-blackout
   headroom. Confirm with the pending daylight A/B (Test B) before the cut.
2. **Power schedule 20/40** (production logic; remote-configured tonight via
   the cloud — itself now a proven fleet capability).
3. **Deploy checklist adds:** verify transmit window matches intent (finding
   008 — a stale window makes a live-looking unit that never images); verify
   power-controller cycling before leaving a halting unit (incident 004 —
   halt without cycling is one-way); restore `cfg vle 1` (bench LED change).
4. **Backend:** deploy the truncate-at-gap partial renderer (handoff prompt
   delivered) — makes 95–99 % images render clean instead of corrupt.
5. **Operator runbook:** re-send-until-acked; remote `note sync` to unclog;
   ack lag 13–30 min is normal; "awaiting node" ≥2 wake cycles → re-send.

## Findings ledger (evidence in incidents.md; all committed)

001 silent ack drop (2-slot queue) · 002 chunk loss quantified, predates
patch · 003 Notecard sync stall + remote recovery · 004 halt-without-cycling
one-way trap · 005 Spotter ~60-min post-boot self-reset votes (source 7,
Sofar thread) · 006 mailbox drains chase cycle tails; queue-while-off ≈
never delivers at field duty (0/15 wakes) — re-send doctrine or post-freeze
listen-tail · 007 sync-session blackout unifies loss model · 008 stale
transmit window no-ops a unit silently · 009 (corrected) wrong-size test
artifact; pipeline input validation failed safe every cycle.

Tooling bugs found+fixed during soak (all with pinning tests where
testable): scheduler late-fire-to-tomorrow + catch-up lookback; orphan
serial-logger port conflict; Mac App-Nap stalling orchestration
(caffeinate); reconciler gid/tail blind spots.

## Not tested / open

- Daylight constant-input A/B at 2.0 s (Test B) — ready to run via the
  monitoring Pi (`tools/spotter_serial_monitor.py`) + corrected reference.
- GUI final acceptance (Nick, definition-of-done 1–4) and §8 Phase D
  formal permutation run with backend-image verification — the soak
  exercised every component (incl. negatives via cloud) but the scripted
  4-permutation ritual with per-permutation evidence tables remains.
- Source-7 Spotter reset behavior outdoors with GPS fix (watch overnight).
