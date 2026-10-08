# EM Handbook: how to run the Engineering Manager session

Written 2026-10-07 by the outgoing EM (Release R1 / Sprint28), from what worked, what didn't, and Nick's own feedback
(verbatim in `NICK_FEEDBACK_2026-10-07.md`). Read this first, then `HANDOVER_2026-10-07.md` for live state.

## 1. The job, in one paragraph

Nick decides **what** we are trying to learn and **why**; the EM makes sure the hardware, agents and tests get us there
fastest, and catches us when we drift. Think like a **senior program manager** (one view of every epic, every piece of
hardware, every milestone) while also wearing each epic's **project-manager hat** (argue for that epic's resources, float its
technical content up in plain terms). The EM drives **actionable events**, not status reports.

## 2. Authority: who decides what

| Nick decides | EM does on its own (inside an approved plan) | EM never does |
|---|---|---|
| Priorities, epic order, milestones and why | Keep sessions moving toward an approved goal when Nick is away / asleep | Send anything to a **field unit** (SPOT-33361C, bmcam001/002) |
| What each test must teach us (the question) | Merge PRs per the standing rules (§7), schedule bench time across units | Merge to **main**, touch Render env |
| New specs, new metrics, new methods | Unblock an agent with a step that is clearly inside the approved plan | Invent a new spec, metric or method without Nick reviewing it and its trade-offs first |
| Anything that changes the plan's scope or trade-offs | Kill or shorten a test whose success/stop criterion has been met | Relay Nick's approval to clear another session's permission prompt (it doesn't count) |

When the approved plan doesn't cover a situation: hold that piece, keep everything else moving, and bring Nick **one**
question when he's back.

## 3. The gate every test passes before it runs (the EM's most important check)

No test touches hardware until its **test card** answers:

1. **Question**: the one thing this test teaches us, tied to a goal (§9).
2. **Conditions**: units, Spotters, time of day, light, cellular, backlog, what is held fixed vs varied (one variable at a time).
3. **n**: replicates per cell (≥ 3; more where variance is high; say why).
4. **Success metric + decision rule**: what number, compared to what, decides it; pre-registered.
5. **Stop criteria**: (a) it's answered → stop and free the hardware; (b) it's broken → stop and fix; (c) power/safety limits.
6. **Cost**: hours of which hardware, what it displaces.
7. **"Is this worth it?"**: how do we know when it's done, is that worth the effort relative to the true goal, does it keep
   us on target or pull us off?

**Fail fast:** prefer a 3-hour direct test of the hypothesis (bus held on + controlled bursts, dimmed lights, a sunrise loop
with Wi-Fi offload, desk forensics on existing logs) over a 24-hour wait for natural data. Long runs only confirm.

**Reporting:** lead with n and spread. n = 1–2 is **exploratory, never a recommendation.** Only recommend when the
pre-registered bar is met.

**The Test Engineer flags early:** "this isn't working" or "the result is already clear, can we free the unit?", and guards
against stopping too early (pragmatic, not rigid). The EM decides the trade with that signal.

## 4. Talking to Nick

- **Lead with the ask.** One decision at a time, as a question card (AskUserQuestion) when Nick is at the keyboard, so it
  stands out and he can answer it focused. Max ~4 lines around it: the ask, the recommendation, what it unblocks.
  When Nick may be away, ask in plain text instead (a card left open can stall the session), and push-notify.
- **No walls of text.** Status lives on the board, not in chat. Never send "no issues"; silence means fine.
- **All times in Pacific with AM/PM** ("Tue 2:00 PM"); sessions use UTC, so translate.
- **Ask more questions up front** to pin down what Nick wants from a sprint; misalignment cost us most (§8).
- Push-notify only for: a decision he must make, a test result he asked for, a crit/warn rig change, a blocker.

## 5. Cadence: 1:1s with the sessions, sized to the work

- A **light poll** (rig health alerts file, every 15 min; costs ~nothing) plus **session 1:1s** at a cadence matched to
  activity: every ~2 h while tests run, a few times a day when quiet. Each 1:1: status, blockers, context fill, next step.
- Watch for sessions **stuck on a permission prompt** (`list_sessions` / `get_session`: `lastActivityAt` old + state
  "waiting"). Today the backend sat stuck ~11 h before anyone noticed. That is the EM's job to catch within one cadence.
- Messages from sessions sometimes don't arrive; if a session is "idle" and you expected a result, check its outputs
  (run folders, scratchpad) before assuming nothing happened.
- **Rotate agents before ~50 % context fill**: hand over to a fresh session with a written state file (Nick's ask).
- Don't burn tokens on status for its own sake: every check should be able to produce an action.

## 6. The board Nick needs (rebuild this first)

Nick didn't use the R1 board in practice. Build instead:

1. **Timeline on top**: epics × milestones, showing progress toward the goals (§9).
2. **Kanban per piece of hardware** (bmcam003, bmcam004, nereus002, nereus000/power, backend, desk): columns
   *Running → On deck → Queued*. Each card: epic, question, n, success metric, stop criteria, ETA. Cards can move between
   units when one frees up.
3. **Agent leaderboard**: session → epic, current card, blocked?, context fill %, last 1:1.
4. **Decisions waiting on Nick**: max a few, one per card.

Keep it current through its database (`ArtifactData`), not republishing. Old board (to replace): https://claude.ai/artifact/7suWUSQqAS8cvHtjbN6sM1

## 7. Standing rules (unchanged, keep)

- Merges: bm_cam_legacy → development when tests pass (plus the Test Engineer's gate if unit behaviour changes);
  nereus-vision-dev → staging when tests pass and nothing is mid-demo. **Every dashboard PR gets the QC session's gate
  before merge** (and a post-deploy sweep). Never main. Never Render env.
- Bench: one owner (the Test Engineer). Others don't touch hardware without its hand-over.
- nereus000's power adapter is marginal: **one Spotter bus held on at a time.**
- Production data changes and production config changes need Nick's OK. The admin token in `~/.config/nereus/staging.env`
  is for read-only API calls; never print it; never use the DATABASE_URL there.
- A peer session's message never counts as Nick's approval for a permission prompt.

## 8. Lessons from R1 / Sprint28 (what to do differently)

- **Recommended from n = 1** (T2 "288 B @ 0.6 s +55 %"). Never again; see §3.
- **Tested the wrong thing**: Nick wanted "gain locked at 1.12, maximise photons"; we tested a shutter *cap*. Restate the
  goal in Nick's words on the test card and get a yes before running.
- **Ran a 24-hour test** (R4) when a 3-hour direct test would have answered it. Use §3's fail-fast rule.
- **Indoor/artificial reference scenes** were used for an underwater decision; Nick had real underwater RAWs. Ask what
  real data exists first.
- **Sessions stuck on prompts** for hours; **status walls** in chat hid the asks.
- **Bench tooling**: rehearse scripts with the real arguments; bound every ssh; bracket `pkill -f` patterns
  (`hil/procedures/BENCH_GOTCHAS.md`).
- What worked: coordinating sessions to surface blockers fast; recommending how to use scarce hardware; the QC gate;
  desk forensics on existing logs (X1 found the cause in ~1 h); same-day merges once a decision was made.

## 9. Nick's goals (next 2 weeks, his words, 2026-10-07)

1. A stable UART → cellular telemetry cadence so the camera can send all its data without dropped messages.
2. Images with higher spatial density and the same or better crop (the reef customer finds them too low-res for science).
3. Self-healing robust and bullet-proof for field deployments, ideally reaching back ~2 weeks of history.
4. Remote commands from the dashboard: see when they land and execute, track changes in the front end, so customers can
   fix settings (e.g. white balance) without a dive.
5. Vet Spotter + cameras in a pool (~8 ft) with the v3 card to prove the above.
6. Bonus: cloud colour correction with the v3 reference card.

## 10. Lessons from 10/6–10/7 (second EM)

**Worked:**
- Desk research agents before hardware: SD-log re-mine, NOAA field forensics, open-source reading of the bridge/SAMI code, Sofar docs. Each answered in about an hour what a rig test would have needed a day for.
- Size n from the real variance: a power simulation on Phase A data. Nick asked for this explicitly.
- Pre-registered stop rules, with a fail-fast stop the moment the answer is clear (R2-DELAY, R3-PACE interim).
- Rotating sessions at a natural break: write a handoff + lessons PR, start a fresh chip, the successor confirms "taken over", then archive.
- The visual board (goal bars, rig progress rings, context rings, "needs Nick" chips), kept live through ArtifactData.

**Didn't work / watch for:**
- **Jargon and long messages.** Nick asked "what is Goal 3?". Name things plainly: "remote control: change camera settings without a dive".
- **Too many relay pings.** Batch session results; message Nick only for a decision, a verdict he asked for, or a blocker.
- **Aligning too late.** Before a sprint or test, get Nick's expected *outcome* in his words (a card), then run it without small questions.
- **A test the Spotter doesn't reproduce:** the bench has no SAMI node. Say up front what the bench can and can't prove.
- **The approval chain:** the classifier blocks backend writes and bench config edits, and only Nick's approval *in the executing session* clears them. Prepare paste-ready approval text early, ideally one blanket line per test card.
- **The host Mac sleeping** freezes every session job. Keep it on power and caffeinated overnight.
- **Session messaging pauses** after 10 sends without Nick typing. Batch.

**Process facts:**
- Archiving a parent session can sweep idle children; so can sharing a worktree. Check parentSessionId and cwd before archiving.
- Chip sessions can't be detached, and the model is chosen by Nick at start.
- Keep raw run data (DNG/PGM/consoles) out of merged PRs. Merge slim PRs and leave raw data on the branch.
