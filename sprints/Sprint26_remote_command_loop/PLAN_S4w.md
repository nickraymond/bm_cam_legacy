# Sprint26 S4w — W9: the chunk total `/M` on the device (plan)

Source of truth: DESIGN_supervisor.md §8.2 W9 + §10 O11 (RULED 2026-09-26), PLAN_S4.md G4,
PLAN_S6.md §1 A + §5 step 2. Backend half is LIVE on staging (nvd #65, wire contract §14.6).
Branch `feature/sprint26-s4w-chunk-total` from `origin/development` d956513. Device code only.

## 1. Wire (exact)

| message | today (rev 5) | W9 (rev 5a) |
|---|---|---|
| chunk | `<I{key}.{i}>{b64}\n` | `<I{key}.{i}/{M}>{b64}\n` |
| video keyframe repeat | `<I{key}.{i}>` | `<I{key}.{i}/{M}>` (its original `i/M`) |
| heal re-send | byte-identical to the original | byte-identical to the original (carries `/M` iff the original did) |
| legacy `<I{i}>`, START, END, `a=inc`, `<HL>`, `<WS>` | unchanged | unchanged |

`M` is decimal, no padding. Backend regex (nvd `origin/staging`
`backend/app/services/bm_image_parser.py:155`): `<I(?:([0-9a-z]{6})\.(\d+)(?:/(\d+))?|(\d+))>`.

## 2. Where M comes from

- **Stills** (`rc_transmit.transmit_progressive_image`): `M = planned = len(chunks)`, the value
  START already sends as `length` (P5: planned, never the bounded count). A bounded `a=inc`
  partial therefore carries the planned total too (ruling).
- **Video** (`rc_transmit.transmit_video_clip`): `M = planned`, same as START `length`; the
  keyframe repeat uses the same `send_chunk(i)` so it carries its original `i/M` and is never
  counted in M. `--bench-drop-chunks` is unaffected (dropped slots send nothing).
- **Heals** (`rc_heal.heal_lines`): `M = total`, computed from the sent record's payload and
  its own `chunk_b64_chars` (sha256-checked, so it equals the original `planned`). Whether to
  add `/M` comes from the record, not from the healing process: `prepare_keyed_send` writes
  `"chunk_total": true` into the sidecar only when W9 was on for that send. A record without it
  (every pre-W9 record, every legacy-runtime record) heals without `/M`, byte-identical to its
  original. This also covers a rollback: a W9 record healed by an older runtime would lose `/M`
  (backend: "a chunk without /M never contradicts"), never corrupt.
- One builder: `rc_media_key.chunk_prefix(i, key=None, total=None)`.

## 3. Gate (legacy stays byte-identical)

W9 rides W8b's gate exactly: `Supervised._install_wire_extras()` (the supervisor on a
migrated config v2 unit, `v9_replies` set) sets `rc_media_key.CHUNK_TOTAL = True`; `finish()`
clears it with `START_EXTRA_FN`. Default `False`. The hook is installed once per process in
`start_process`, before any action; `finish()` runs once per process (per_boot `finally`,
stay_on only at stop), so `/M` is consistent across every action of a stay_on process.
Decided ONCE per send (review MINOR 1): the caller reads the flag into a local, passes it to
`prepare_keyed_send(chunk_total=..)` (sidecar) and to the transmit function
(`chunk_total=..`), so the record and the wire can never disagree. So:

- legacy runtime (`vectors/`): unchanged.
- supervisor on a v1 profile (`vectors_supervisor/`, parity net): unchanged.
- `uplink.media_key.enabled: false` or no Spotter time (legacy `<I{i}>`): unchanged (no key → no `/M`).
- changes: `vectors_v9/` and `vectors_stay_on/`, only for media sent in the scenario (chunks +
  the sidecar's new `chunk_total` field). `vectors_save_local/` and `stay_on_idle_heal` do NOT
  change: their only keyed chunks heal the pre-seeded record `0dnwc0` (no `chunk_total`).
- bmcam001/002 run `main` (field profiles): unaffected.

Assumption (labelled): bmcam003/004 run the supervisor on config v2 (memory: S3c on bmcam003,
S3b on bmcam004; v9 needs S4). They get W9 only when the bench owner deploys this commit.

## 4. Budget

- Assumptions: `uplink.chunk_chars` = 384 (bench; the registry allows up to 1200,
  pre-existing, config_registry.py:345) and i, M ≤ 999.
- Worst case today: `<I{key}.{i}>` at i ≤ 999 = 13 B + 384 b64 + `\n` = 398 B.
  W9 adds `/` + up to 3 digits for M ≤ 999 → **402 B**. Message count and pacing unchanged.
- The Spotter uplink limit is 1000 B hard / ~430 B fast path (Sprint09, RESEND_DEVICE.md:45):
  402 fits. The chunk budget (`chunk_chars` 300/384) counts base64 chars only; the prefix
  was never inside it, so no chunk is re-cut.
- S5 F5 (256 B incl. newline) is the Spotter USB **console input** line (commands typed or
  `bm pub` on the console), not the uplink. Chunks never travel that way; heal `rsd` commands
  are unchanged by W9. Not affected.
- Sent record: +22 B per sidecar (`"chunk_total": true`).

## 5. Tests

- Golden: ONE W-commit, re-record, diff limited to §3's dirs; `vectors/` and
  `vectors_supervisor/` byte-identical (asserted by the harness: the legacy vector is compared,
  and `vectors_supervisor/` only holds SUPERVISOR_DIFFERS).
- New `tests/test_w9_chunk_total.py`:
  - encodes a keyed still (complete + bounded `a=inc`) and a keyed video (with keyframe repeat)
    through the real transmit functions with the flag on; every chunk matches the nvd regex
    (copied verbatim from `origin/staging`, source cited), key/i/M parse, M == START `length`.
  - flag off → no chunk has `/`; legacy `<I{i}>` never gets `/M` even with the flag on.
  - heal: a record written with the flag on heals `/M` byte-identical to the original chunk;
    a record without `chunk_total` heals without `/M`.
  - worst-case line length ≤ 402 B.
- Heal byte-identity uses the REAL path: `prepare_keyed_send` + a real transmit, then
  `heal_lines` compared with the bytes actually transmitted (no hand-built record).
- `tools/soak_reconcile.py` `CHUNK_RE` accepts `/M` after a key (review MINOR 2: it would
  classify every W9 chunk as "other"); unkeyed `<I{n}/{M}>` stays "other", like the backend.
- Full suite: `.venv-dev/bin/python -m pytest tests -q --ignore tests/test_reference_card_color_utils.py`.

## 6. Not in S4w

- Deploying to the rigs (bench owner, PR has the proof plan).
- nvd changes (none needed). The known limit stays: a START-lost VIDEO completed from `/M`
  has no `fps` → no mp4 transcode (PLAN_S6 H6, S6b).
- DESIGN §8.2 W9 row: marked "built (S4w)" in this commit.

## 7. Bench proof: forcing a lost START (second, non-W commit)

Both rigs are video units. Today `--bench-drop-chunks N[,N...]` (BENCH ONLY, video, S5) can
drop chunks but not START, and a real Spotter queue-full loss cannot be scheduled. Smallest
addition: the same flag accepts the token `start` (`--bench-drop-chunks start,5,17`): START
is NOT put on the wire, its slot is still paced (same timing as a real queue-full drop).
Default None = no change; golden untouched (separate commit, plain unit test). The dropped
START is logged loudly (`[VTX][BENCH] NOT sending START`) and recorded
(`result["start_dropped"]`); on a stills action the flag is refused with a warning (it is
video-only today); it applies to every clip of the process, so the proof runs as one manual
per_boot, never under cron or stay_on. Rejected
alternatives: a new flag (more CLI surface); a backend-side replay (not a device proof, and
staging is hands-off).

Precondition (read-only check by the bench owner): staging has `BM_KEYED_GROUPING=on`; with
grouping off the backend ignores `/M` (parser :646-650, contract §14.6).

Proof (bench owner, bmcam003 first, then bmcam004): one triggered clip with
`--bench-drop-chunks start,5,17` → backend row is chunk-born with `expected_chunks = M`
(not `length_unknown`), missing = {5, 17} → heal `rsd` → the unit re-sends `<I{key}.5/{M}>`
and `<I{key}.17/{M}>` byte-identical → row complete, sha256 matches the sent record. Known
limit (PLAN_S6 H6): a START-lost clip has no `fps`, so no mp4 transcode; the H.264 original
is the proof. Full steps in the PR.

## 8. Review record (independent, fresh context, 2026-09-29)

No BLOCKER, no MAJOR. Confirmed: M = planned on every path (stills incl. `a=inc`, video incl.
keyframe repeat); the W8b gate fires only on supervisor + v2, before `prepare_keyed_send`, once
per process (per_boot, stay_on, crashloop fallback); legacy `vectors/` and `vectors_supervisor/`
carry keyed chunks but no `cfg=`, so they stay byte-identical; `heal_lines`' total is
sha256-checked = planned; backend regexes (parser :155, :641, probe :30) accept exactly this;
no length assert on chunk lines in bm_serial (only test_s4_media_key.py:184, flag off).

| # | finding | resolution |
|---|---|---|
| MINOR 1 | flag read twice (sidecar vs wire); global leaks across tests | decided once per send, passed explicitly; tests reset `CHUNK_TOTAL` |
| MINOR 2 | tools/soak_reconcile.py `CHUNK_RE` misses `/M` | fixed in the W-commit + test |
| MINOR 3 | `/M` needs `BM_KEYED_GROUPING=on` | proof precondition (§7) |
| MINOR 4 | `start` token: every clip of the process, `started` stays True, stills silently ignore, parse | loud log + `start_dropped`, refused on stills, manual per_boot only, mixed parse |
| MINOR 5 | save_local does not change; test must use the real send | §3 corrected; §5 real-path heal test |
| NIT | 402 B assumes 384 chars and i, M ≤ 999 | stated in §4 |
