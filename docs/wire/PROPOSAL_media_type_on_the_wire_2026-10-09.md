# PROPOSAL: carry the media type on the wire (rev 7, typed chunks)

Date 2026-10-09. Desk-only, read from `origin/development` (bm) and `origin/staging` (nvd). For Nick, before the pool test.

**Trigger (nvd #136, label fix #137 merged 8ede078).** A Spotter queue stall ate START and END; the backend received only `<I0ehg91.n/181>` chunks and filed the media as `heic` by default. For a still, START is the ONLY message that names the format (still END carries filename but no `fmt`; video END does). We are about to run pjpg, nrjxl v1/v2, h264 and later DNG side by side, so a guess is not good enough.

## What is on the wire today (quoted from code)

| Message | Exact form today | Source |
|---|---|---|
| key | `[0-9a-z]{6}` = seconds since 2026-01-01Z, base36, Spotter UTC only | `rc_media_key.py`, nvd `RE_MEDIA_KEY` |
| still START | `<START IMG> filename: F, timestamp: T, length: 75, key=0ehg91, fmt=pjpg, q=13, att=1, cmp=1[, rsn=budget][, rfb=cap]...\n` (<= 285 B; `fmt` in `pjpg|nrjxl`) | `rc_uplink_messages.build_rc_start_message`, `tests/test_rc_uplink_messages.py` |
| video START | `... length: 126, key=0dhnso, fmt=h264, fps=10, dur=5.0, res=480x270, crop=na, crf=40, cmp=1, ...\n` | `tests/vectors/bm_media_h264_rev5/wire_keyed_complete.txt` |
| chunk | `<I{key}.{n}/{M}>{384 base64 chars}\n` (rev 5a, W9; 288 B data; worst case 402 B at n,M <= 999). Legacy `<I{n}>`, rev 5 `<I{key}.{n}>` | `rc_media_key.chunk_prefix`, `rc_transmit.py` L185/L338 |
| still END | `<END IMG> filename: F, uart_duration_sec: 632.4, sent_buffers: 75, cpu_temp_c: 51.2[, metadata...]\n` (<= 295 B, no fmt) | `rc_telemetry._build_end_image_message` |
| heal re-send | byte-identical to the original chunk (so it carries `/M`) | `rc_heal.heal_lines` |

Backend chunk regex (nvd `bm_image_parser.py` L158/L647): `<I(?:([0-9a-z]{6})\.(\d+)(?:/(\d+))?|(\d+))>`. A line matching neither keyed nor legacy form falls through to the legacy bucket as a non-chunk (L885): **any new chunk header is silently dropped by the current backend**. Per key the backend knows: `(device_id, key)`, chunk map, `M` (length), filename only if START arrived, `format` from START `fmt` -> else sniffed chunk 0 -> else #137 inference -> else `heic` (`poll_once_ingest._media_format_for_image` L605-623). Heal code (`heal_commands.MissingReport`) carries `fmt` but never puts it on the wire.

## Options (costs at 195 chunks/image, 1.3 s pacing)

| | Change | Bytes / msgs per image | Backward compat | Verdict |
|---|---|---|---|---|
| (a) type as a 7th key char | key `0ehg91j` everywhere | +195 B, +0 msgs. NOT zero: both sides pin the key at exactly 6 chars; 5 time chars wrap 2027-12-01 | the key is an identity: `rsd` JSON, `<HL key=>`, `media.media_key`, idempotency keys, `sent/*.json` all change | reject: biggest blast radius for the same byte cost |
| **(b) 1-char type in the chunk header** | `<I{key}.{n}/{M}{T}>`, T in `[A-Z]` | +195 B (+0.25 % of ~78.4 KB), +0 msgs; worst line 402 -> 403 B (Spotter <= 1000 B, fast path drains ~430 B) | key unchanged; old-backend drops typed chunks -> **backend first**, as W9 was | **pick** |
| (c) START twice (before burst, after END) | +1 START msg | +285 B, +1 paced slot (~1.3 s) | needs keyed grouping (a 2nd START on legacy wire opens an empty group) | complement at best: at 195 x 1.3 s the repeat lands ~4.2 min later, near the same phase of the Spotter's 5-min schedule where the queue-full stalls were measured (contract §5) |
| (d) config-snapshot + previous-still inference, byte sniff on completion (#137, live) | none | 0 | n/a | keep forever as the fallback layer; it is a guess (stills only, wrong across a format switch, no DNG sniff) |

## Recommendation: (b), wire contract rev 7 "typed chunks"

- `T` = one uppercase letter after `M`: **J** pjpg, **H** heic, **X** nrjxl (any profile; the `NR` method byte says v1/v2), **V** h264, **D** dng (future). Uppercase is outside the key alphabet and digits, so it is unambiguous to parse and to read. Unknown letter -> image + flag (same rule as an unknown START `fmt`, §2).
- Sent only on the W9 wire (with `/M`); END and START unchanged; heal re-sends stay byte-identical (so a fully START-lost media labels itself when healed).
- Backend precedence for `media.format`: sniffed bytes (complete or chunk 0 present) > START `fmt` > chunk `T` > #137 inference > heic. START vs `T` disagreement -> `format_disagreement` flag, START wins (fifth witness in §8). `T` also fixes `_keyed_fallback_filename`'s extension and the display path on the first poll.

**Camera (~1 day):** `rc_media_key.py` (`chunk_prefix(i, key, total, type_code)`, module flag `CHUNK_TYPE` next to `CHUNK_TOTAL`, `chunk_type` in the sent record), `rc_transmit.py` (two call sites, `T` from the final `send["fmt"]`, the same variable START uses, after `_raw_choose` may swap nrjxl -> pjpg), `rc_heal.py` (`heal_lines` re-emits `T` iff the record has it), `rc_supervisor._install_wire_extras` (set the flag), tests `test_rc_media_key/transmit/heal`, rev 7 golden set via `tools/bm_video_tx_loopback.py --golden`, rev 3/5/5a goldens byte-identical with the flag off.

**Backend (~1 day):** `bm_image_parser.py` (`RE_CHUNK_MARKER`/`RE_KEYED_CHUNK` + `([A-Z])?` before `>`, `parse_chunk_marker` 4-tuple, per-group `types`, mixed letters -> `type_mismatch` flag, `ParsedBmImage.chunk_format`), `poll_once_ingest.py` (`_media_format_for_image`, `_is_nrjxl`, `_is_video`, `_extension_for_image`, skip `_infer_still_fmt` when `T` is present, `format_inferred = chunk:<T>`), no migration, tests: `test_s6a_wire_regression` unchanged + `test_rev7_chunk_type.py` (parse) + `_db.py` (ingest). Mirror the contract section into `backend/docs/`.

**Contract note for Nick to sign (new §17, rev 7):** chunk `<I{key}.{n}/{M}{T}>`, `T` registry J/H/X/V/D, unknown = image + flag, only after `/M`, START/END/heal unchanged, precedence as above, backend first. Same PR also catches the bm copy up: it stops at §14.5 while nvd already has §14.6 (rev 5a), §15 (rev 5b), §16 (rev 6), although the header says bm is the source of truth.

**Test plan.** Unit: above. One bench proof on bmcam003 (bench owner runs it): backend deployed, camera flag on, drop START with the existing bench hook (`transmit_video_clip` "NOT sending START", L369; add the same one-liner for stills), one pjpg and one nrjxl wake. PASS = after the first poll the row exists with `format` = jpeg / nrjxl, filename ext `.jpg` / `.nrjxl`, `format_inferred = chunk:J|X`, no `heic` anywhere; the heal completes it and the sniff agrees. Then one rev 5a unit (bmcam004) against the new backend: rows unchanged.

**Rollout.** 1 backend PR, merged + verified on staging (replay a typed vector through `poll_once`). 2 camera PR; flag rides `_install_wire_extras` (config v2 supervisor units only): bmcam003, then bmcam004; field units after the REEF-RC decision; bmcam001/002 stay rev 3. Both sides tolerant: new backend + old camera = today; new camera + old backend = **whole media lost** (never do this).

**Risks.** Deploy-order inversion (silent loss; gate the camera flag on a staging parse test). `T` taken from a stale fmt on the nrjxl -> pjpg fallback path (test it). Pre-rev-7 `sent/` records re-send without `T` (fine, tolerated). 403 B leaves 27 B to the ~430 B fast-path figure (Sprint09 DEV_LOG), same class as W9's +4 B; the keyframe repeat and the `a=inc` partial are unaffected (no extra messages). Not covered: a START-lost media whose chunks all predate rev 7 (stays on #137).
