# Remote command delivery diagnosis — 2026-07-31

**Mission:** find why Sofar remote commands (Command API → cloud mailbox → Spotter →
`bm pub` → Pi daemon) stopped being delivered, on both SPOT-33507C (bmcam003) and
SPOT-31593C (bmcam000). Bench setup: SPOT-33507C on Mac USB, serial monitor logging to
`~/spotter_logs/SPOT-33507C/console_*.log`; bmcam003 armed, duty-cycling.

**Status: diagnosis complete — failure isolated to the Sofar cloud-mailbox → Spotter
delivery leg. Clean-queue experiment in progress (see §5).**

## 1. Direct observation: the Spotter polls its mailbox and gets nothing

2026-07-31T03:24:49–03:25:00Z (console, live session): successful cellular transmit
(`[ORC] Message 0 transmitted successfully!` → `[MS] All messages sent successfully!`)
followed by `[MS] Checking for Rx Messages` → `Messages sent/received successfully.`
At that moment ~12 never-expiring commands (ids 1010–1015 + 07-28 stragglers) were
queued cloud-side. Nothing was handed over.

## 2. Zero mailbox deliveries ever observed on console (both spotters)

Method: every `bm pub` / `bm cfg` / `bridge cfg` / `note sync` / `cfg commit` line in
all console logs (SPOT-33507C and SPOT-31593C, 2026-07-29T00:28Z → 2026-07-31) was
cross-referenced against the bench-injection record (`~/spotter_logs/events.log`
`>>> SENT:` lines, ±30 s). Result: **zero orphans** — every command execution ever
seen on either console was bench-injected via cmd.txt. Mailbox Rx checks in the same
period: 75+26+2 (33507C) and 197+26 (31593C) ≈ 326.

Kills the "delivered while BM bus off" theory: a mailbox delivery executes as a
console line on the Spotter regardless of bus power, so it would appear in the log.

## 3. The Sofar API's own ack record re-dates the breakage to ~19:15Z 2026-07-27

Queried `GET /api/sensor-data` (1-day windows, hex-decoded acks) for both spotters,
07-27 through 07-29.

Complete end-to-end remote deliveries (the only ones ever):

| id | cmd | API send (202, logged) | ack in Sofar record | E2E |
|---|---|---|---|---|
| 801 | ping | 07-27 17:33:12Z | 18:35:54Z | 62.7 min |
| 804 | roi=2 | 07-27 18:46:07Z | 19:35:49Z | ~50 min |
| 805 | exp=4 | 07-27 18:47:13Z | own ack missing; 806's `st.exp=4` proves it applied | — |
| 806 | ping | 07-27 18:48:18Z | 19:37:20Z | ~49 min |
| 816 | foc=0 | 07-28 00:02:00Z | 01:07:45Z | ~66 min |

(The rapid ack bursts 07-27 06:33–07:02Z, ids 201–716, were Phase B **console**
injections, not remote — they pre-date the send log and match the soak bench phase.)

Everything else — 28+ commands enqueued after 2026-07-27T19:15Z across both spotters —
was never delivered, with one anomaly (816):

- SPOT-33507C fresh ids never delivered: 812, 813, 814, 815 (sent 4×), 817, 818, 819,
  820, `note sync`, the chained `bm cfg …\nbm cfg commit 0 s` block, 1010–1016.
- SPOT-31593C: **never delivered a single remote command, ever** (900–915, 0/16, from
  its very first attempt 07-27T19:15:41Z). Ack visibility for 31593C is proven (12
  console-injected acks on 07-29 retrieved via the same query path).
- FIFO order visibly violated at the degradation: 815 (queued 00:00:00Z + re-sent
  00:35Z) never delivered, while 816 (queued 00:02:00Z, behind it) delivered at
  01:07:45Z. Strict FIFO one-at-a-time would deliver 815 first. The cloud queue was
  misbehaving, not merely slow.

## 4. Exonerated

- **Device stack** — 07-29 rotation soak: dozens of acks on both units, via console
  injection over the identical Spotter→BM-bus→Pi→ack path.
- **Uplink/cellular** — image bursts reached Sofar continuously throughout.
- **Token/auth** — same env token produced the 07-27 deliveries, still gets 202s and
  reads sensor-data. (Enqueue-auth ≠ delivery association, but the per-spotter
  asymmetry — one worked, one never did, same account — argues against an
  account-level association break.)
- **Payload format** — byte-identical `bm pub bmcam/cmd {"id":N,"c":"roi","v":2} 1 1`
  delivered on 07-27 (id 804) and never delivered from 07-30 (ids 1010+).
- **Spotter "source 7" reboot requests** — present since 07-27 morning (before
  breakage) and fire ~30 s after boot, before any mailbox poll. Background noise.
- **Suspicious mailbox contents** (`note sync` 07-28 04:27Z; chained
  `bm cfg … commit` to both spotters 07-28 19:15/19:16Z) — neither ever executed on
  any logged console; they are casualties in the queue, not culprits. (The chained
  block ends in `bm cfg commit 0 s` and the doc's own example `cfg save` reboots the
  Spotter — flagged as a future guard: never mailbox-send reboot-triggering commands.)

## 5. Experiments (2026-07-31, Nick-approved)

1. 03:47Z — fresh `ping` id 1016 enqueued to SPOT-33507C (202) behind the stale
   backlog; console watched. (Superseded by the queue clear below.)
2. 04:05Z — **queue clear + back-to-basics test** (Nick's call: simplest possible
   payload, cellular only, success = console echo, Pi out of scope):
   `clear_command_queue:true` + message `uptime` in one call → 202
   "successfully enqueued clearing cellular queue, sending message uptime".
   SPOT-33507C's cellular mailbox now contains exactly one benign command.
   SPOT-31593C left untouched as control.
   - If `uptime` echoes on console at a sync → delivery leg works with a clean queue;
     next step A/B the `bm pub` JSON payload, then Pi listen-timing.
   - If `uptime` never arrives on an empty queue → minimal reproduction that the
     delivery leg is broken independent of our payloads/queue state → Sofar ticket.
   Historical E2E for delivered commands was ~50–66 min; syncs ≈ hourly.

Result: (pending — watcher running against
`~/spotter_logs/SPOT-33507C/console_20260731.log`, baseline: 0 "uptime" mentions,
2 Rx checks at watch start.)

## 6. Sources

- Send audit: `runs/sofar_command_sends.jsonl` (main checkout; includes the 04:05Z
  clear+uptime record) and `runs/sprint10_soak_20260727/command_plan.json.results.jsonl`.
- Ack queries: `tools/sofar_poll_acks.py` `fetch_acks()` against
  `api.sofarocean.com/api/sensor-data`, 1-day windows.
- Console: `~/spotter_logs/SPOT-{33507C,31593C}/console_2026072[9]-31.log`,
  `~/spotter_logs/events.log`; archived Phase E copies in
  `runs/sprint10_phaseE_20260728/console/`.
- Prior evidence package: `runs/roi_remote_test_20260730/REPORT.md` (its "broken since
  ~07-28, both simultaneously" timeline is corrected by §3 above).
- API semantics: `docs/sofar_command_api_reference.md`.
- Command endpoint has no readable queue state (GET variants → 404), so the mailbox is
  observable only via delivery behavior.
