# Sprint13 — Console `help` + config readback (`cfg`)

Status: SPECCED (2026-08-01, from Nick's Sprint12 close-out feedback).
Prerequisite: Sprint12 merged. Sprint14 (overnight command soak) waits on
this sprint being complete AND Nick having personally tested `help`.

## Why

The camera is customer-facing at the Spotter USB console. A customer (or
a future us) talking to the camera over the BM terminal has no on-device
reference: the command shorthand (`roi`, `twn`, `trg`…) is opaque without
the repo docs. Nick's ask, verbatim intent:

> I want my customer to have reference details at the spotter console to
> help them adjust the values or trigger an action as needed. Example
> code is helpful for commands that force an image capture so that they
> can just copy and paste.

Two commands, both queries (no settings change):

## Feature 1 — `help`

**Name is `help`, not `hlp`** (Nick feedback 2026-08-01 — verbose names
for customer-facing commands; 4-char names are contract-legal, `ping`
already is one).

- Verbose output to the console, **in the style of the Spotter's own
  `help`** (reference: docs/spotter_cli_reference.md and a fresh capture
  of real `help` output for format details).
- Must explain: every command name and its meaning (the shorthand
  expansion), every value table with index + label, next-boot semantics,
  the one-shot trg contract, and how to edit the config file vs. send a
  command.
- **Copy-paste example lines** for the common actions, at minimum:
  - force an image capture + send: `bm pub bmcam/cmd {"id":123,"c":"trg","v":2} 1 1`
  - send the reef reference (camera test): `... "c":"trg","v":3 ...`
  - open the window all day: `... "c":"twn","v":2 ...`
- Generated from command_tables.py (D9 doctrine: the help can never
  describe a value the daemon can't apply). Labels may need a
  `description` field added to table entries for customer-grade wording.

## Feature 2 — `cfg` (post-style config dump)

Separate command, modeled on the Spotter's `post` output: one line per
setting —

```
<human-friendly name>  <variable syntax>          <value>   <source>
Transmit window        transmit_window.start/end  10:00-15:00  yaml
Power halt             power_halt.enabled/dry_run real         command hlt=1
```

- Dumps the **effective RESOLVED config** (YAML + command overlay — what
  the next cycle will actually do), with the source column (yaml vs
  command N) so an operator can tell a field override from stock.
- Covers at least every command-controllable setting + the audit's
  category-A keys; full list decided in DESIGN.

## Transport — the design question (T1)

Responses must reach the **console** and must NOT ride the cellular
transmit queue by default (quota + the 2-slot collision problem). Leading
candidate: the `spotter/fprintf` topic (already used for SD logging —
Sprint10 audit). BENCH EXPERIMENT FIRST: publish test lines via fprintf
during a bench cycle and observe whether the Spotter echoes them on the
USB console. If it does: zero-quota console output + a durable SD copy.
If not: fallback options are (a) monitor-side rendering (the serial
monitor recognizes a tagged response and pretty-prints it), (b) SD-log
retrieval instructions. Measure fprintf size/pacing limits — `help` is
long and will need line pacing.

Remote (mailbox) use of help/cfg is a non-goal for the console-print
path; `cfg` output additionally reaching the cellular uplink on request
is an open question (O1) — it would close the audit's remote-readback
gap, but costs quota and needs chunking.

## Not in scope

The audit's category-B preset commands (qly/owd/chk/phs/tal) unless Nick
picks any at kickoff; arbitrary window times (O2, still deferred);
Sprint14's soak.

## Acceptance gates (PR → development)

1. Unit tests: help/cfg generation from tables (content-complete: every
   command + every index appears), query semantics (no state change,
   dedupe applies), transport framing.
2. Bench bmcam003 (USB): `help` prints the full reference readable in
   the terminal; `cfg` matches --print-config resolved values incl. an
   active command override with correct source column.
3. **Nick reads the output and signs off on customer-readability** (this
   gate is explicitly his).
4. Docs: bmcam_command_reference.md points to on-console help; hotspot
   skill note.
5. TRACKER with artifacts.
