# Sprint13 — Design decisions

Numbered D-S13-N, dated. Sprint10/11/12 doctrine (D2 next-boot, D9
generate-from-tables, D13 overlay, D-S12-1 index-0-no-payload, D-S12-6
explicit gate overrides) is inherited and not re-litigated.

---

**D-S13-1 — help/cfg are a new command class: QUERY (2026-08-01).**
`QUERY_COMMANDS = ("help", "cfg")`. They ride the normal command topic
(`bm pub bmcam/cmd {"id":N,"c":"help"} 1 1`, `v` optional like ping),
record ONLY their dedupe id (command_state.record falls through both the
settings and action branches), ack normally, and their "result" is
console output. A duplicate id acks without re-printing — the blanket
re-send doctrine must not print help ten times.

**D-S13-2 — All console text renders from command_tables.py
(2026-08-01).** New module `command_help.py` holds layout only; every
fact (names, indices, labels, notes, example lines) comes from the
tables + a new `COMMAND_INFO` metadata block beside them. Enforced by
test: every command and every index must appear in `render_help()`, and
every printed example line must round-trip `parse_command()` as valid —
help can never show a line the daemon rejects (D9's strongest form).

**D-S13-3 — cfg renders the SAME resolved-settings dict as
--print-config (2026-08-01).** `render_cfg(settings, state, controls)`
takes the post-overlay dict, so cfg and --print-config cannot disagree.
Source column derives from `state.touched` + the index-0-no-payload rule
(hlt/twn/tmz at index 0 show `config file`, not `command x=0`).

**D-S13-4 — tmz: timezone promoted from audit category C to a preset
command (2026-08-01, Nick request).** REVISES REMOTE_CONFIG_AUDIT.md.
The audit's fear was schedule confusion; with three vetted IANA zones
(America/Los_Angeles, America/New_York, UTC — DST via tzdata) the worst
failure is a window shifted by hours, recoverable with `twn 2`, and
`tmz 0` hands control back to the file. The clock SOURCE
(spotter/rtc/system) stays provisioning-only. Gate plumbing mirrors
D-S12-6 exactly: `timezone_override` explicit parameter, passed by
gate_kwargs_for only when `timezone_source` starts with "command", so
the un-commanded path stays byte-identical (D14).

**D-S13-5 — win index 0 is 12 min; production YAML moved with it
(2026-08-01, Nick: "12 min is the production spec now").** Table order
12/5/8/16. All five device profiles + repo default YAML changed 16 → 12
in the same commit — the factory-reset doctrine (index 0 == shipped
YAML behaviour) is why table and profiles must move together. txd stays
1.0 s (confirmed already true in every profile).

**D-S13-6 — awb custom preset dropped, not relabeled (2026-08-01,
Nick: "not shipping if I have not tested").** AWB_TABLE is 0-2
(libcamera presets only). The custom-gains code path in bindings stays
(schema keeps `gains`) for a future tank-tuned entry; tests cover it via
a patched temp entry. Old senders' `awb=3` rejects as a normal ERR_VAL
ack (version-mismatch path).

**D-S13-7 — roi reef-test crops clamp output width, never upsample
(2026-08-01).** roi 5 (800x450) / roi 6 (640x360) are narrower than the
fixed 1000 px output; the overlay computes
`out_w = min(output_width, crop_w)` so they transmit at native crop
size. `output_size_for_crop` still raises on any upsample attempt —
the clamp is the only sanctioned path around it. Rect values to be
exercised in the next ROI sweep sprint.

**D-S13-8 — Console output is pure ASCII, <= 72 chars/line
(2026-08-01).** Serial terminals + unknown transport prefixes; boxed
tables use +-= only. Width and ASCII are test-enforced; cfg columns clip
overlong values (alignment beats completeness — full wording lives in
help). Final layout gets re-checked against a fresh Spotter help capture
in the Phase 0 bench pass before the formatting gate is ticked.

**D-S13-9 — Transport is the console write (`spotter/printf`), per the
Sofar SDK's own bm_serial (Nick 2026-08-01, overnight session).** Nick
rejected the SD+`cat` fallback outright and pointed at the SDK's console
write: `bm_serial.py` now carries the clean trio — `spotter_tx`
(cellular), `spotter_log` (SD), `spotter_print` (console). Implementation
note (honesty): no local SDK copy existed on this Mac and the session had
no network, so `spotter_print` mirrors our own `spotter_log` frame
(itself SDK-derived; bm_core print header: 8B target node, 2B fname_len,
2B data_len) with topic `spotter/printf` and fname_len=0 — byte layout is
DERIVED, not copied. Bench echo test on v2.16.6 is the proof gate; diff
against real SDK source when network returns. Console lines are queued in
the daemon and drained at idle points / the listen window / before halt —
never mid-transmit, never into the cellular queue. Per-line delay 0.05 s
PROVISIONAL until bench measures.

**D-S13-10 — Duplicate query ids ack but do NOT re-print (2026-08-01).**
The blanket re-send doctrine (spam_cmd.sh, mailbox re-sends) must not
print help ten times; dedupe answers a re-send with the ack alone.

## Open

**T1 remainder (bench, pre-demo):** verify spotter/printf actually echoes
on v2.16.6 + measure line pacing; fresh Spotter `help`/`post` capture
(format cross-check, D-S13-8); optional SD probes (`sd err`,
`log dest|level`, `sd usb` tree) now demoted to curiosity — no feature
depends on the SD path anymore.

**O1 — cfg over cellular on request:** still open, still deferred.
