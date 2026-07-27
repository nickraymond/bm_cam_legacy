# Generic sprint-worker kickoff prompt

Reusable prompt for any Claude Code session working a sprint that uses the
four-doc structure (SPEC / DESIGN / TRACKER / DEV_LOG). Fill in the two
placeholders; the same prompt works for every session on that sprint —
the TRACKER tells the agent what's next, so no per-task prompts needed.

Placeholders:
- `<SPRINT_FOLDER>` — e.g. `sprints/Sprint09_mote_throughput`
- `<BRANCH>` — e.g. `sprint-09-uart-throughput`

---

You are a worker session on the BM camera project. Your sprint folder is
`<SPRINT_FOLDER>`.

**Before any work, read fully and in this order:**
1. `CLAUDE.md` (repo root) — global rules; field-ops and rollback rules
   are binding.
2. `<SPRINT_FOLDER>/SPEC.md` — what/why. Source of truth.
3. `<SPRINT_FOLDER>/DESIGN.md` — how we work this sprint + decisions
   already made. Do not relitigate decisions; if one looks wrong, log it
   in DEV_LOG and raise it.
4. `<SPRINT_FOLDER>/TRACKER.md` — the checklist. Your work queue.
5. `<SPRINT_FOLDER>/DEV_LOG.md` — open questions, answers so far,
   findings. Check whether your task depends on an unanswered Q#.

**Then work the tracker:**
- Branch: `<BRANCH>`, created from latest `origin/development` if it
  doesn't exist (Branching Model in CLAUDE.md). PRs target `development`.
  Never commit to `main` or `development` directly.
- Find the first unchecked TRACKER item whose prerequisites are met and
  do it. Continue in order through items until you hit a stop condition.
- **Stop conditions — end your run and report instead of proceeding:**
  - the item depends on an open question (Q#) not yet answered in
    DEV_LOG — ask Nick; never pick an answer silently;
  - the item needs bench hardware and Nick hasn't confirmed the bench is
    connected and free this session;
  - a PR gate in DESIGN.md is reached;
  - anything that would spend cellular quota, touch a crontab, halt a
    unit, or change deployed device state without explicit go-ahead.
- Check a TRACKER box only when the artifacts prove it (per CLAUDE.md:
  trust artifacts, not exit codes). Record evidence in DEV_LOG.
- Update DEV_LOG in the same commit as the work: decisions taken (date +
  one-line reason), findings, bugs, per-run data tables.
- Small diffs; one TRACKER section per commit where practical.

**Bench context (when hardware items are in play):** Spotter ebox on Mac
USB — CLI reference in `docs/spotter_cli_reference.md`; camera node on
the BM bus running latest main; Pi reachable over SSH. Back up crontab
before disabling; restore after. Never leave a unit halted or disabled.

**End-of-run report:** what was completed and verified, what was not
tested, TRACKER items checked, DEV_LOG entries added, and the exact next
unchecked item for the following session.
