# Kickoff — self-healing media, implementation from S0 (for a NEW session)

Written 2026-09-23 02:40Z by the Sprint25 session. Owner and approval gate: Nick. Everything below
was measured, read from code, or decided by Nick unless marked ASSUMPTION. **Give Nick your plan for
S0 before any code and wait for his go.** One stage at a time; never start a stage before the
previous gate is green.

## REQUIRED READING, in order
1. `CLAUDE.md` (manifesto; branching: never commit to `main`/`development`; sprint PRs target
   `development`). Backend repo `nereus-vision-dev`: **never touch `main`; branch from
   `origin/staging`, PR into `staging`; staging IS production for the BM cameras.**
2. The approved design: `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` v3 (PR #47 —
   §0a decisions, §0b signed decisions, §2 wire, §3 M0–M5, §6 delivery reality, §7 ladder, §8 gates,
   §9 stages) and `sprints/Sprint25_transmit_timing_resend/RESEND_DEVICE.md` v3.
3. `sprints/Sprint25_transmit_timing_resend/REVIEW_20260922.md` — three engineering reviews and
   where each finding was folded. Read the A findings before touching the parser, the B findings
   before touching the device, the C findings before any deploy.
4. `sprints/Sprint25_transmit_timing_resend/RESULTS.md` — why (the stall mechanism, the reset
   losses, the report/health-check timing).
5. `docs/bm_media_wire_contract.md` rev 3 (FROZEN — rev 5 is added, never edited in place; §13 is
   the sign-off record and needs Nick's line).
6. Backend code you will change first: `backend/app/services/bm_image_parser.py` (chunk regex
   `<I(\d+)>` at ~:706; grouping ~:527-716; `RE_TS_IN_NAME` ~:124), `backend/app/services/poll_once_ingest.py`
   (partial upsert ~:1030; idempotency keys ~:105-116; promotion ~:1593-1617), tests are
   module-level assert scripts run with `backend/.venv/bin/python backend/tests/<file>.py` from the
   repo root with a dummy `DATABASE_URL` (`postgresql+psycopg://nereus:x@127.0.0.1:1/x`); baseline:
   `test_s22_video_ingest.py`, `test_s22_legacy_image_parse_regression.py`, golden vectors under
   `backend/tests/fixtures/`.

## STATE YOU INHERIT
- bm_cam_legacy: branch `claude/sprint25-transmit-timing-backend-02b53c` (from `development`, PR
  open to `development` — the Sprint25 part-1 tool/results/design). Worktree:
  `.claude/worktrees/sprint25-transmit-timing-backend-02b53c`. **New worktrees may be cut from
  `main`: run `git rev-list --count HEAD..origin/development` first and fast-forward.**
- nereus-vision-dev: PRs #44/#45/#46/#48 merged (node identity Gate 2 closed; `BM_AUTO_PROVISION=1`
  in Nick's Render env group). PR #47 = the spec (ready for review). Worktree
  `nereus-vision-dev/.claude/worktrees/resolver-gap` (branch `feature/spec-resend-heal`). Migration
  0011 (duplicate binding rows) is NOT done — 0012 must chain after it; the paused duplicate row
  `a5b92b99-…` carries a `retired:` sentinel node id that 0011 must handle.
- Credentials exist in the Mac shell BY NAME ONLY, never print them: `NEREUS_ADMIN_TOKEN`,
  `STAGING_DATABASE_URL` (psql; reads worked, one UPDATE was refused by the auto-mode classifier —
  hand Nick the exact SQL when that happens), `SOFAR_API_TOKEN_BM_REEF`, `SOFAR_API_TOKEN_AOML`.
  `sofar-poll-once` POSTs worked from the session. `gh pr create` worked.
- Field units: bmcam003 (node `0x53171fa3d81a8e6f`, on SPOT-33507C) and bmcam004
  (`0xe6fe83ea6b4a2b7f`, on SPOT-31593C) are OUTDOORS on the Sprint25 test (another session owns
  them — do not touch). bmcam001/002 at the reef on SPOT-33361C run released rev 3 code: they are
  the no-regression control for every backend step. Bench dev unit for S3–S5: bmcam000 (ASSUMPTION:
  on the bench and reachable over Tailscale; verify with Nick).

## S0 — what to build (small PR to `staging`)
1. Contract: append the rev 5 section to `docs/bm_media_wire_contract.md` (bm_cam_legacy): key
   definition (6 base-36 chars, seconds since 2026-01-01Z, from Spotter UTC only, monotonic), START
   `key=` as a core key right after `length`, chunk form `<I{key}.{n}>`, END unchanged, 398 B chunk,
   the M0 rule, the "legacy after keyed START is flagged" rule. Add Nick's sign-off line in §13.
2. Backend M0: the parser accepts `<I([0-9a-z]{6})\.(\d+)>` and treats it as `<I{n}>` under today's
   open-group rule (lossless; a 3-char gid is rejected). No grouping change, no migration.
3. Vectors: `keyed_as_legacy_complete`, `keyed_as_legacy_partial`, `three_char_gid_rejected`,
   and the existing `legacy_unchanged` baseline green.
4. Gate: after the staging deploy, bmcam001/002 complete+partial rows/day and the CAM_0003 gallery
   unchanged for 48 h (query `media` by device and day; compare with the 7 days before).
Then S1 per the spec §9 — its own plan, its own go.

## STANDING RULES
- Backend first, cameras later; the device key island stays OFF until M0 is live.
- Render Postgres snapshot before any migration; 0011 before 0012.
- Report to Nick at each gate: What happened / What I learned / What's next, two bullets each,
  comparisons in tables. Trust artifacts, not exit codes.
