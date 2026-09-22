# Sprint25 part 2 — self-healing resend, DEVICE side (design only, not approved)

Companion to `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` (decisions D1–D5 live
there). Written 2026-09-22. Nothing here is built; no `command_tables.py` change, no
TABLES_VERSION bump, no wire change until Nick signs off.

## 1. What the camera must keep — the SENT payload, byte-identical

`rc_video_tx.py` fits the clip into `WORK_DIR = /tmp/rc_video_tx` (tmpfs) and the fitted
H.264 payload is gone at halt. A re-fit is NOT byte-identical (x264 2-pass to a message
budget; the budget itself moves with the cycle clock), so a resend needs the bytes that
went on the wire:

- After a successful fit, before START: write `<video.dir>/<stamp>_video_<d>s.h264.sent`
  (the fitted payload, ~36 KB at 125 messages) and `….sent.json`:
  `{chunk_b64_chars, raw_bytes_per_msg, msgs, keyframe_chunks, sha256, sent_utc, res, crop, br, fps, dur}`.
  `video.dir` defaults to `/home/pi/BM_Devel_Pi/videos` (the ring buffer;
  `video_ring.ensure_room` already bounds it; the two files ride with the clip's mp4).
- Chunking is deterministic: `split_base64_chunks(payload, chunk_b64_chars)` — the same
  call the burst used, so `<I{n}>` for any `n` is reproducible from the file.
- Nothing else changes in the normal cycle (golden-wire test stays byte-identical).

## 2. Prerequisite — the command daemon must run inside `video_tx` (Sprint24 step 2)

Today `video_tx` cycles run **without** the command daemon, so no `rsd` (or any command) is
heard in video mode. The port is the recipe already written in
`sprints/Sprint24_outdoor_hil_baseline/PLAN.md` step 2: `daemon_factory` + `daemon.start()`
at cycle start, `cmd_hooks.gate_kwargs_for` for the shared port, `make_pending_pump_fn` /
`make_ack_drain_fn` in the pacing slots of `rc_transmit.transmit_video_clip` (optional
callables, default None), `flush_acks` + `post_transmit_listen` after the burst,
`cmd_hooks.shutdown` in `finally`. Tests with injected fakes, zero sleep. Known limit it
does not fix: commands HELD while the bus is off are replayed 10 s after bus power, the Pi
subscribes at ~38 s → lost (TODO-BM-017, mote-side cache with Sofar).

## 3. The command — `rsd` (TABLES_VERSION 8, needs D2)

```
{"id":4101,"c":"rsd","v":0,"f":"20260922T162031","r":"17,40-42"}
```

| field | meaning | validation (parser rejects → ack `ok:0` with an error code) |
|---|---|---|
| `v` | 0 = the ranges in `r`; 1 = the whole clip; 2 = keyframe chunks only; 3 = cancel a pending resend | index into `RSD_TABLE` (normal D3 rule) |
| `f` | clip stamp, `YYYYMMDDTHHMMSS` of the capture (the filename prefix without punctuation) | `^\d{8}T\d{6}$` AND a matching `.sent` file exists |
| `r` | chunk index ranges, 0-based, inclusive | `^\d+(-\d+)?(,\d+(-\d+)?)*$`, ≤ 120 chars, every index < `msgs`, ≤ 60 chunks total; required for `v=0`, ignored otherwise |

The D3 exception in one sentence: a garbled `r` can only select a bounded set of chunks
of a clip the camera already sent — the worst case is a few wasted messages. Payload stays
far under the Sofar 270-byte limit. `help`/`cfg` text is generated from the table (D9).

State: the request is persisted in `bm_command_state.json` (`pending_resend`, one slot;
a newer `rsd` replaces it; `v=3` clears it), so it survives the halt between hearing the
command and the next wake. Ack immediately on receipt (`ok:1`), as every command does.

## 4. Executing the resend in a cycle (needs D5)

Default order in a `video_tx` cycle: new clip's burst first, then the resend, if the
budget allows (`budget.has_time_for(n_chunks + 2 envelope msgs)`); otherwise the resend
waits for the next cycle (it is small: 3–5 messages typical, ≤ 62 by construction). The
resend is one ordinary group (wire rev 4):

```
<START IMG> filename: <stamp>_video_<d>s.h264, timestamp: …, length: <msgs>, fmt=h264, a=rsd, n=<k>, …
<I17>…  <I40>…  <I41>…  <I42>…            (paced exactly like a burst, 1 msg/s)
<END IMG> filename: …, fmt: h264, a=rsd, sent_buffers: <k>, …
```

`length` is the ORIGINAL clip's message count (the backend needs it to judge completeness),
`n` is how many chunks follow. The keyframe repeat is not applied to a resend. The
`pending_resend` slot is cleared when END is sent; a resend that could not fit stays
pending (bounded: dropped after 3 wakes, logged).

## 5. Where it fails, honestly

| Failure | Effect | Mitigation |
|---|---|---|
| command held while the bus is off (today's normal) | never heard | re-send until acked; BM-017 |
| `.sent` file rotated out by the ring | ack `ok:0 err:no_such_clip` | backend only offers candidates ≤ 24 h old |
| backend still on rev 3 | the resend opens a junk partial | deploy backend first (D-S22 rule) |
| resend chunks rejected by the Spotter queue again | still partial | the backend simply asks again (idempotent) |
| a fresh burst + resend exceeds the cycle budget | resend deferred | it is ≤ 62 msgs; the time budget guard already exists |

## 6. Test ladder (device half)

1. Unit: `parse_command` for `rsd` (all rejects), planner `resend_chunks_for(stamp, ranges)`
   returns byte-identical chunks vs `split_base64_chunks` of the `.sent` file; golden-wire
   test unchanged for normal bursts.
2. USB console (zero cellular): camera awake on the bench, publish the command on the
   Spotter's console (`bm pub bmcam/cmd {...} 1 1`), ack within seconds, resend group on
   the console; `spotter-usb-console-capture` reassembly proves the bytes match the `.sent`
   file; feed the captured group to the backend parser fixture.
3. Cellular, one rig: real partial on staging → `GET …/missing` → `tools/sofar_send_command.py`
   during a bus window → ack → next cycle → clip complete on staging.

## 7. Effort (estimate, not measured)

Daemon-in-video_tx port ~1 day (the recipe exists). Persist `.sent` + `rsd` table/parser/
planner/executor + tests ~1 day. Backend M1–M3 + rev-4 merge + vectors ~1 day. Ladder
steps 2–3 half a day each. Nothing starts before the §0 decisions in the backend spec.
