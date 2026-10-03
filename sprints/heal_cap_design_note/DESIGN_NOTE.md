# Design note: heal size as one source of truth (NOT merged; parked for Sprint28)

Status: parked (Nick 2026-10-02 ~23:00, via the EM). The mechanism is being proven with config
first (Option A: `video.send.message_cap` 184 → 126, applied by the TE if heals don't improve).
The one-source heal cap is folded into Sprint28 (JPEG-XL retunes all the message limits). This
branch (`claude/heal-cap-one-source`, bm) and nvd `feature/heal-cap-one-source` are the reference
implementation. Their PRs (bm #119, nvd #82) were closed unmerged.

## What the branches do
- **Camera (bm):** registry `heal.max_chunks_per_wake` (default 40, range 1..120, `next_boot`)
  replaces three hard-coded 40s.
  - The render writes it into `bm_commands`.
  - The daemon config loader reads it; the daemon carries `heal_cap`; `rc_heal.begin_wake` uses it.
  - The `rsd` parse ceiling = the range max, so an rsd above the unit's per-wake value is accepted
    and the excess deferred (no `e:val`).
  - Goldens: hash-only change. Full suite: 1629 passed.
- **Backend (nvd):** `heal_commands.max_chunks_for(device)` = the unit's reported value, else the
  catalog default, clamped to the ceiling.
  - `heal_autosend` is untouched (the defaults follow the device).
  - `admin_heal`: 422 above the ceiling.

## Sizing (2026-10-02, tonight's numbers)
- **Window:** a 10-min bus window − burst end 505 s (184 msgs at 1.54 s/msg, ends ~:08:25) − a 30 s
  margin = 65 s ≈ **42** heal chunks. N ≈ 100 needs a 12-min window + budget ≈ 11 min, **or** the
  video message cap at ~126 (frees ~58 msgs ≈ 89 s): Option A.
- **Clip safety:** heals never cut the new clip. `rc_heal` reserves the whole burst before each heal
  chunk, so excess heals are deferred.
- **Command size:** the rsd JSON is ≤ 234 B, about 190 chars of ranges for one clip. Tail losses
  (a contiguous run) compress to a few chars; scattered 3-digit singles cap at ~47.
- **Starvation:** heal chunks cross the same lossy link (~22 %), so effective repair ≈ 0.78 × N
  per hour. At N = 40 that is ~31/h against 35–45/h lost, i.e. a backlog by construction. Break-even
  N ≥ ~58.

## TE mechanism (runs/g4_outdoor12h_20261002/analysis/heal_mechanism_notes.md)
- **One rsd per wake, newest clip first, truncated at 40.** Older clips end up "left_out:
  chunks > 40".
- **Losses are the clip TAIL** (e.g. chunks 145–184 of 188): Spotter queue overflow late in the
  burst (F1).
- **For Sprint28:**
  - pack older remainders into the same rsd after the newest (up to the cap);
  - cut the tail loss itself (a smaller video message cap / shorter bursts, or slower pacing near
    the tail);
  - size so the per-wake capacity > the hourly loss + the backlog drain.
