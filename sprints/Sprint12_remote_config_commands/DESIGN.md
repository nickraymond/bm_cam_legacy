# Sprint12 — Design decisions

Numbered D-S12-N, dated. Sprint10/11 decisions (D2 next-boot application,
C3 deferred acks, D13 overlay model, D15 ack-on-persist) are inherited and
NOT re-litigated here.

---

**D-S12-1 — `hlt`/`twn` index 0 means "YAML governs", implemented
table-driven (2026-07-31).** For roi/foc/awb/exp, commanding index 0 IS an
override ("operator said auto, auto wins over YAML"). For hlt/twn the SPEC
wants 0 to *delete* the override. Rather than special-casing the state
machine, the table entry for index 0 carries `"override": None` and the
overlay skips any entry with no override payload. The state machinery
(touched/record/dedupe/ack) stays completely generic; `hlt 0` still
records, dedupes, and acks like any command — it just overlays nothing.

*Rejected alternative:* removing the key from `touched` on 0. Same
observable behaviour, but it would make `touched` semantics differ per
command and complicate the ack's "complete truth" story.

**D-S12-2 — Only `enabled`/`dry_run` are remotely commandable for
power_halt; `mode` and `script_path` stay YAML-owned.** The incident that
motivated the sprint needed exactly enable/dry-run/disable. mode/script are
provisioning concerns; a garbled index can at worst pick a tested
enable-state, never point the halt at a different script.

**D-S12-3 — `trg` is a new command class: one-shot action (2026-07-31,
Nick-approved).** Persisted in a dedicated `pending_trigger` slot of the
state file, NOT in `settings`: it must self-clear. `consume_trigger()`
clears-and-persists BEFORE the cycle acts on the trigger, so a crash during
the triggered cycle costs the trigger (operator re-sends) instead of
looping the unit into capturing every boot. If persisting the clear fails,
the trigger is NOT serviced that boot — one quiet boot beats a capture
loop. Re-arming replaces the previous pending trigger (last command wins).
`trg` never enters `settings`/`touched`; the ack means ARMED, not captured.

**D-S12-4 — The trigger boot bypasses the transmit-window gate, always
(Nick 2026-07-31).** In-window a trigger adds nothing; out-of-window it
would be a silent no-op. Bypassing once is safe precisely because it is
one-shot — it cannot strand a unit the way a bad persistent window could.
Everything else about the triggered cycle is stock (budget, pacing, halt).

**D-S12-5 — Reference triggers reuse SRC_TABLE indices (2026-07-31).**
`trg 3`/`trg 4` carry a `src` index into SRC_TABLE rather than their own
paths, so reference paths stay single-source-of-truth and the finding-009
pack-time dimension check covers them. The one-shot src does NOT touch the
persisted `src` setting — a `trg 3` on a unit whose `src` setting is 0
sends one reference image and returns to live camera. Composing
`src 1` + `trg 2` + `src 0` would need three acked commands and leave
persistent state to clean up; `trg 3` is one command, self-clearing.

**D-S12-6 — `twn` reaches the schedule gate via an explicit override
parameter, not by rewriting YAML reads.** `should_transmit_now_from_schedule`
loads the YAML itself; it gains an optional window override that run_cycle
passes ONLY when a commanded window is active, so the un-commanded path
stays byte-identical (D14 parity).

**D-S12-7 — Ack-before-halt is proven, not built.** The ordering already
exists structurally: run_cycle's `finally` runs `cmd_hooks.shutdown()`
(paced ack flush, 15 s budget) before `halt_fn`. Sprint12 adds the explicit
ordering unit test + a bench artifact. If the flush budget expires with
acks unsent, the existing doctrine (cloud re-send + dedupe) recovers —
unchanged.

**D-S12-8 — tables_version 2 → 3 in one bump.** hlt/twn/trg ship together;
nothing deployed with an intermediate version. Version-mismatch paths:
old receiver + new sender → normal ERR_CMD ack; new receiver + old state
file → missing keys load as defaults (tested both ways).

**D-S12-9 — start == end is a FULL-CIRCLE window = all day (2026-08-01,
Nick request: kill the 2-minute quiet gap).** Tested first: `"24:00"` is
rejected by `_parse_hhmm`, and an equal pair was previously an
UNSATISFIABLE empty window (`start <= end` branch) — a never-transmit trap
with no legitimate use (`enforce_time_window: false` is the off switch).
The gate now treats `start == end` as always-in. `twn 2` becomes
`00:00-00:00` "all day 24h" and tables_version bumps 3 → 4 (v3 only ever
existed on the sprint branch + bench bmcam003). Normal and overnight
windows are pinned unchanged by tests (TestFullCircleWindow).

## Open questions

**O2 — arbitrary window times.** Deferred (possible v4). The preset table
is deliberate: single-int payloads, no fat-fingered `25:00`, vetted
windows. Overnight (wrapped) windows are supported by the gate but not
offered as presets — add one deliberately if a deployment needs it.
