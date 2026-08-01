# Sprint12 — Remote config commands: power-halt + transmit window + trigger

Status: IN PROGRESS (started 2026-07-31). Written 2026-07-31 immediately after
the fleet standardization that motivated it. Nick-approved scope: originally
two features; Feature 3 (`trg`, incl. reference-image trigger) added
2026-07-31 in-session, Nick-approved. Decisions recorded in DESIGN.md.

## Why this sprint exists (the 2026-07-31 incident)

During the AOML fleet update, bmcam001 and bmcam002 (Florida, SPOT-33361C)
were deployed with `power_halt: enabled: false` — and when the production
decision flipped to "real halt everywhere," there was **no remote path to fix
it**. The halt setting and the daily transmit window live only in the Pi-side
YAML; the v2 command table (`roi foc awb exp win txd cap src ping`) has
neither, and the deployed field configs have `bm_commands.enabled: false`, so
the daemon isn't listening anyway. The only remedy was "someone drives to the
site with an iPhone hotspot" (see `.claude/skills/bmcam-hotspot-update`).

Two most-wanted settings that were SSH-only that day:

1. power_halt enable / dry-run / disable
2. the allowable image-capture/transmit window (`transmit_window` start/end)

This sprint adds both as commands, deliverable over **both** existing paths:

- **USB / bench**: Spotter console `bm pub bmcam/cmd <json> 1 1`
  (Sprint10 Phase B path; see `docs/spotter_cli_reference.md`)
- **Remote / field**: Sofar Command API cloud mailbox
  (`tools/sofar_send_command.py`; re-send-until-acked doctrine, 1/min/Spotter
  limit, `tools/bm_command_gui` retry engine)

## Feature 1 — `hlt`: power-halt override command

New settings command following the existing int-keyed table pattern
(`command_tables.py`):

```python
HLT_TABLE = {
    0: {"label": "yaml default (no override)", "override": None},
    1: {"label": "halt enabled (real)",  "enabled": True,  "dry_run": False},
    2: {"label": "halt dry-run (log only)", "enabled": True, "dry_run": True},
    3: {"label": "halt disabled",        "enabled": False, "dry_run": True},
}
```

Semantics:

- Persisted in `bm_command_state.json` like every settings command; the state
  overlay gains a `power_halt` section (today it covers only
  `progressive_jpeg.crop`, `camera_controls`, `max_run_time_min` — extending
  the overlay is a deliberate, reviewed change in `rc_command_hooks.py`).
- Applies from cached state on the **next** boot (Sprint11 D2 — commands never
  affect the in-flight cycle's behavior).
- `0` deletes the override so the YAML value governs again ("delete the state
  key to restore stock," same doctrine as the whole state file).

Safety requirements (these are the sprint's hard part, not the table):

- **Ack-before-halt**: the command ack MUST be transmitted on the uplink
  before the cycle's halt fires, or the sender's retry engine can never
  converge. Interacts with deferred acks (Sprint11 C3) — needs an explicit
  test: command arrives mid-cycle on a halting unit → ack observed at Sofar.
- **Stranding analysis, logged loudly**: `hlt 1` on a constant-power unit
  means it halts at next cycle end and stays dark until a physical power
  cycle; `hlt 3` on a battery unit means continuous ~0.6 W drain. The command
  is still allowed (that's the point), but the applied-state line in the boot
  log and the ack payload must state the new mode unambiguously.
- No same-cycle halt changes: the running cycle's halt behavior is whatever
  it booted with.

## Feature 2 — `twn`: transmit-window command

Window presets, int-keyed like everything else:

```python
TWN_TABLE = {
    0: {"label": "yaml default (no override)", "override": None},
    1: {"label": "field 10:00-15:00",  "start": "10:00", "end": "15:00"},
    2: {"label": "all day 24h (bench/diagnostic)", "start": "00:00", "end": "00:00"},  # v4/D-S12-9: was 00:01-23:59 (2 min dead time)
    3: {"label": "morning 08:00-12:00", "start": "08:00", "end": "12:00"},
    4: {"label": "midday 11:00-14:00",  "start": "11:00", "end": "14:00"},
}
```

- Times are interpreted in the unit's own configured `timezone` (the command
  does NOT change timezone — one variable per command).
- Same persistence/overlay/next-boot semantics as `hlt`; overlay gains
  `transmit_window`.
- Preset table, not arbitrary start/end: keeps the payload a single small int
  like every existing command, and a finite set of vetted windows is a
  feature in the field (no fat-fingered `25:00`). Arbitrary times are noted
  as a possible v4 (`DESIGN.md` open question O2) — do not build it here.
- Interaction to test: `twn 2` (wide) is the remote equivalent of the
  `--skip-time-window` bench flag — a unit that boots outside its window
  currently skips transmit; after `twn 2` it must transmit on next boot.
  This is also the remote un-brick for "window misconfigured, unit never
  transmits, can't reach it" — the twin of the halt incident.

## Feature 3 — `trg`: one-shot capture/send trigger (added 2026-07-31)

Nick: "a simple command to trigger an image, and trigger an image + send" —
plus triggering a REFERENCE image so link performance can be verified
independent of the camera (e.g. bench in a dim room).

```python
TRG_TABLE = {
    0: {"label": "cancel pending trigger", "action": None, "src": None},
    1: {"label": "capture only (to SD, no transmit)", "action": "capture", "src": None},
    2: {"label": "capture + send", "action": "capture_transmit", "src": None},
    3: {"label": "send reef reference (camera skipped)", "action": "capture_transmit", "src": 1},
    4: {"label": "send reference card (camera skipped)", "action": "capture_transmit", "src": 9},
}
```

Semantics (all decisions in DESIGN.md D-S12-3..5):

- A one-shot ACTION, not a setting: arms `pending_trigger` in the state
  file; the next boot consumes it exactly once (cleared-and-persisted
  BEFORE the cycle acts, so a crash cannot re-fire it).
- The trigger boot ALWAYS bypasses the transmit-window gate
  (Nick-confirmed 2026-07-31). One-shot, so it cannot strand a unit the
  way a bad persistent window could. Everything else is a stock cycle:
  normal budget, pacing, halt.
- `src` values index into SRC_TABLE (paths stay single-source-of-truth;
  finding-009 dimension checks apply).
- Ack means ARMED, not captured. Execution proof = the image (trg 2/3/4)
  or wake status + SD artifact (trg 1). Dedupe makes cloud re-sends safe.
- Realistic latency on a duty-cycled unit: command arms during one
  cycle's listen tail, fires on the next boot (~one duty cycle later).

## Prerequisite / enabling change — daemon on in production

Both commands are useless while production configs ship
`bm_commands.enabled: false`. This sprint flips it to `true` in the four
device profiles + rc_field_template, with eyes open about the known caveats:

- **Drain timing** (Sprint10/11 finding): the Spotter fetches inbound mail at
  its ~hourly [MS] sync. On duty-cycled/halting units most drains land while
  the bus is off; delivery relies on the re-send-until-acked doctrine, and
  latency is hours, not minutes. That is acceptable for `hlt`/`twn` (config
  changes, not real-time control) — say so in the docs rather than fighting it.
- Energy cost of the listener during cycles was already accepted in Sprint11
  (C4 bounded post-transmit listen tail).

## Not in scope

- Arbitrary window times (see O2), timezone commands, schedule/cron changes,
  new transport work, backend/GUI changes beyond adding the two commands to
  `tools/bm_command_gui`'s command list.

## Versioning, files touched

- `tables_version` 2 → 3 (`command_tables.py`); receiver tolerates unknown
  commands from older senders and vice versa — verify the version-mismatch
  warning path.
- Expected diff surface: `BM_Devel_Pi/command_tables.py`,
  `command_bindings.py`, `command_state.py` (schema additions),
  `rc_command_hooks.py` (overlay application for the two new sections),
  `rc_progressive_jpeg.py` (only if overlay hookup requires it),
  `device_profiles/*/camera_schedule.yaml` + template (daemon enable),
  unit tests alongside the Sprint10/11 command tests, `tools/bm_command_gui`.

## Acceptance gates (PR targets `development`)

1. Unit tests: table parsing, state persistence, overlay application,
   version mismatch, ack-before-halt ordering.
2. Bench validation on bmcam003 (USB path): `hlt 2` (dry-run) applied via
   Spotter console `bm pub`, observed in next-boot log; `twn 2` observed to
   open the window; `hlt 0`/`twn 0` restore YAML behavior.
3. Remote validation on bmcam000 (mailbox path): one command delivered via
   Sofar Command API, acked, applied next boot.
4. `docs/` + `bmcam-hotspot-update` skill updated: the halt/window sections
   change from "SSH-only, needs hotspot" to the command procedure.
5. TRACKER boxes ticked only with artifacts (logs/run folders), per house
   rules.
