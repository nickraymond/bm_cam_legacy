# HIL — hardware-in-the-loop test format (Release R1 onward)

Owner: the Test Engineer session (Nick approved the role 2026-10-01). This folder holds
everything self-contained, so it can be copied as-is into the future `nereus_HIL_tooling`
repo. Nothing here imports from `BM_Devel_Pi/`: device-side code runs on the unit
over ssh/CLI.

```text
hil/
  README.md        this file: the HIL format (read it to make a test HIL-ready)
  hil.env.example  every host / path / id the tools read (copy to hil.env, never commit secrets)
  gates/           one file per release gate (criteria table, steps, restore)
  procedures/      bench procedures that are not gates (TAKEOVER.md: deploy, #97 host fix, P0)
  tools/           standalone scripts, each with a header (purpose, inputs, outputs, example, limits)
  templates/       RESULTS.md and run_manifest.json skeletons
```

Evidence does NOT live here. It goes to `runs/<gate>_<YYYYMMDD>/` (the existing repo
convention), so this folder stays code + specs only.

---

## 1. What a test needs to run here

A dev session hands the Test Engineer a test. It is **HIL-ready** when it has all six:

| # | item | what it is | example |
|---|---|---|---|
| 1 | **Spec** | one markdown file: purpose, units, preconditions, steps, restore | `sprints/Sprint27_remote_config/LADDER.md` |
| 2 | **Code ref** | a merged commit on `development` (or a named branch tip) the unit must run, plus a read-only check that proves it is deployed | `grep -n _rule_video_geometry ~/BM_Devel_Pi/config_validate.py` |
| 3 | **Criteria table** | one row per criterion: id, what is measured, PASS threshold, evidence source. No row may say "works" or "looks OK" | see §2 |
| 4 | **Inputs** | every command / config / file it sends, written out (JSON lines, kv, files), with the lane (console or Sofar) | `{"id":51001,"c":"ping"}` console lane |
| 5 | **Restore** | how the unit goes back to its pre-test state, and how that is checked (config hash, crontab, bus setting) | `reset` keys → `get` hash == baseline |
| 6 | **Budget** | wall time, cellular messages, Nick's hands (box, power, Render) | ~1 h, 0 cellular, none |

Missing any of the six = the Test Engineer sends it back with the missing row named.

## 2. Criteria table (the gate contract)

Every gate and test spec carries a table in this shape. RESULTS.md repeats it with the
measured value filled in.

| id | criterion | PASS when | evidence |
|---|---|---|---|
| G3.1 | bad values refused before send | 100 % of the negative cases get a backend refusal (`/plan` 422) and no command row | `plan_check.csv` |
| G3.2 | … | … | … |

Rules:
- A criterion is a **measurement with a threshold**, not an activity ("ran N1–N5" is not a
  criterion; "N1–N5 each `ok:0 e:xk` and fps unchanged in `get`" is).
- The evidence column names a **file in the run folder**. An exit code is never evidence
  (CLAUDE.md: trust the artifacts).
- Verdicts: `PASS`, `FAIL`, `BLOCKED` (could not be measured; says why), `N/A` (with a reason).
  A gate PASSes only when every row is PASS or an agreed N/A.

## 3. Run folder layout

`runs/<gate-or-test>_<YYYYMMDD>/` — created by `hil/tools/hil_new_run.sh`.

```text
runs/g3_hardmode_20261003/
  RESULTS.md          verdict first line, criteria table, per-step table, findings, restore, not tested
  run_manifest.json   gate, start/end (UTC + PDT), operator session, repo sha, unit runtimes + config
                      hashes before/after, tools used (path + sha256), env (no secrets)
  commands.log        every console / API command sent, with UTC time and step tag (append-only)
  gate.log            operator timeline: what was done, when, the decision at each step
  snapshots/          hil_unit_snapshot.sh output per unit (before/after), one file each
  console/            console excerpts pulled from the monitor
  api/                raw API responses (JSON), one file per call
  pulled/             files copied off the units (logs, sidecars, manifests, small media)
  analysis/           CSV / JSON derived from the raw files, plus the script that made them
```

`*.log` and `*.csv` are gitignored in this repo: **force-add them** (`git add -f runs/<run>/`).
Large media stay off git: keep the sidecar + a small poster/thumbnail, and record the full
file's path and sha256 in `run_manifest.json`.

## 4. RESULTS.md

Template: `hil/templates/RESULTS.md`. Required sections, in order:

1. **Verdict line** (bold, first paragraph): `G3: PASS` / `FAIL (G3.4, G3.7)` + one sentence.
2. **Setup**: units, runtime sha, config hash, bus state, cron state, who held the bench.
3. **Criteria**: the §2 table with a `measured` and a `verdict` column added.
4. **Steps**: one row per step (sent at, ack at, answer, evidence file).
5. **Findings**: `F1…`: what was seen, the effect, owner, issue/PR link. A finding is not a verdict.
6. **Restore**: what was put back, the read-back that proves it (hash, crontab, bus).
7. **Not tested**: everything in the spec that was not exercised, and why.

## 5. run_manifest.json

Template: `hil/templates/run_manifest.json`. `hil_new_run.sh` writes the start fields;
the operator (or `hil_finish_run.py`) fills `end_*`, `units[].after`, and `verdict`.
Secrets never go in it (tokens stay on nereus000 in `~/.config/nereus/*.env`).

## 6. Bench rules (binding for every HIL session)

- **One bench owner.** Only the owner touches nereus000, the bench Spotters and the bench
  units. Others ask the owner first.
- **Field-ops (CLAUDE.md §15/16):** back up crontab and config before any change, write the
  restore command before running the change, never leave a unit disarmed or mid-surgery,
  prefer reversible changes. Record every host/boot/cron change in `gate.log`.
- **Console sends** go through the monitor host's `cmd.txt` (`hil_console.sh`), ≤ 256 B per
  line (Spotter USB limit), no single quotes in the payload.
- **One heal sender.** When the backend auto-sends heals (`BM_HEAL_AUTOSEND`), never start
  `bm-heal-driver`, and never run the conductor without `--no-heal`.
- **Never send to a field Spotter** (SPOT-33361C, bmcam001/002) from a HIL session.
- **Your own ssh logout can wipe `/dev/shm`** on a unit without the RemoveIPC host fix (logind
  removes pi's IPC when pi's last session ends; `runs/render_dir_vanish_20261001/`). Even a
  read-only snapshot counts. Units with the fix (`KEY removeipc RemoveIPC=no`) are safe.
- **pgrep/pkill over ssh:** bracket patterns (`'[r]c_progressive_jp[e]g'`), or they match
  your own remote shell.
- **Cellular spend** beyond the gate's budget, any hardware Nick must touch, and any Render
  change go to Nick (via the EM) before they happen.

## 7. Configuration and the rig guard (no hard-coded hosts or tokens)

Every wrapper sources `hil/tools/hil_common.sh`, which loads `hil/hil.env` (gitignored copy of
`hil.env.example`) and the current run folder (`hil/.current_run`, written by `hil_new_run.sh` /
`hil_use_run.sh`). Calls are therefore plain `hil/tools/hil_x.sh ...`, never `VAR=... hil/tools/...`
(Nick's standing allow rules match commands from their start).

**Rig guard:** the only Spotters / devices / hosts a tool may reach are those in `HIL_RIG_A` /
`HIL_RIG_B`. Anything else (field Spotter SPOT-33361C, bmcam001/002) exits 5 before any ssh or API
call. `hil/tools/test_hil_guards.sh` proves it offline (fake ssh/scp/curl; must print 0 failures).
Never add a field unit to `HIL_RIG_*`.

Tokens are never read on the Mac: tools that need the staging admin token run their `curl` ON the
monitor host and read its env file there.

## 8. Tools

| tool | does |
|---|---|
| `tools/hil_common.sh` | sourced by every wrapper: hil.env, current run, rig allow-list guard |
| `tools/test_hil_guards.sh` | offline proof that every wrapper refuses non-rig Spotters/devices/hosts (exit 5, no network) |
| `tools/hil_new_run.sh` | create `runs/<name>_<date>/` with the layout above + a started `run_manifest.json`; makes it the current run |
| `tools/hil_use_run.sh` | switch the current run folder |
| `tools/hil_step.sh` | one ladder step: command → trigger → wait for the action → pull its evidence |
| `tools/hil_change.sh` | one change through the backend on the console lane (record → publish → mark sent) |
| `tools/hil_sofar_change.sh` | one change through the backend on the cellular lane (record → send); the G5 sender |
| `tools/hil_unit_snapshot.sh` | read-only unit snapshot: runtime sha, config hash, crontab, processes, /dev/shm, logind, CMA, disk, last cycle log errors |
| `tools/hil_console.sh` | send ONE Spotter console line via the monitor's `cmd.txt`, print what followed |
| `tools/hil_cmd.sh` | publish ONE bmcam command (`bm pub bmcam/cmd …`) and decode the answer + cellular payloads |
| `tools/hil_con_decode.py` | filter a console excerpt; decode the Spotter's cellular hex dumps |
| `tools/hil_pistate.sh` | sha256 of the unit's command state / config / journal + overlay keys |
| `tools/hil_p0_probe.sh` | Sprint27 P0 rpicam limits probe (LADDER PART 1), outputs saved per probe |
| `tools/hil_hardmode_cases.py` | generate the G3 edge-case table from `docs/bmcam_config_catalog.json` |
| `tools/hil_plan_check.py` | run each case against `/remote-config/plan` (writes nothing) and score refused/accepted vs expected |

Origins: `hil_console.sh`, `hil_cmd.sh`, `hil_con_decode.py`, `hil_pistate.sh` are adapted
copies of `runs/s5_console_20260928/*` (originals left in place).
