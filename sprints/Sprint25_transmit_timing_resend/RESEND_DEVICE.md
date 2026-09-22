# Sprint25 part 2 — self-healing media, DEVICE side (design v2, 2026-09-22, under engineering review)

Companion to `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` v2 (decisions D1–D9 live there).
No code until the review closes. Applies to images AND video (D7).

## 1. Daemon first (D3) — the structural fix

Today `rc_progressive_jpeg.main()` loads settings, then **dispatches video cycles to
`rc_video_tx` before the point where the stills cycle starts the command daemon**
(`run_cycle` → `daemon_factory` → `daemon.start()`, `rc_progressive_jpeg.py:520-525`; the video
dispatch is at `:929`). So the daemon is a feature of the stills cycle, not of the wake.

Change: in `main()`, right after the YAML is loaded and the serial port is available, start the
daemon (`bm_commands.enabled`), pass it into BOTH `run_cycle(...)` and
`rc_video_tx.run_video_tx_cycle(...)` as an argument, stop it in one `finally`. The cycles keep
their existing hooks (`make_pending_pump_fn` / `make_ack_drain_fn` in the pacing slots,
`flush_acks` + `post_transmit_listen` after the burst) — the Sprint24 step 2 recipe, now owned by
`main()`. Golden-wire test byte-identical for both modes.

**Benchmark (required, before and after):** console timestamp of `Bridge bus power: 1` → the Pi's
subscribe frame on `bmcam/cmd`; 10 cold boots per unit; report median and worst. Baseline
(Sprint23): ~38 s. Expected after: 22–28 s (Pi boot ~18 s + Python + serial open) — a guess until
measured. The Spotter replays held commands 10 s after bus power, so this number is what the
mote-side cache (TODO-BM-017) must cover; re-send-until-acked remains the doctrine.

## 2. Keyed messages (D6, wire rev 5)

`key` = capture instant as 6 base-36 chars (seconds since 2026-01-01Z). Chunks become
`<I{key}.{n}>…` — the Sprint10 `<I{gid}.{n}>` form with `gid := key`; START and END carry `key=`.
Same code path for images (`rc_transmit.transmit_image`) and video (`transmit_video_clip`). The
message-size ceiling is measured first (backend spec §2): if there is no headroom, chunk payload
drops from 384 to 376 base64 chars (`pacing_chunk_b64_chars`), +2 messages per clip.

## 3. Persisted sent payload (D7, images and video)

After a successful fit/encode, before START, write next to the media in the ring buffer
(`video.dir`, default `/home/pi/BM_Devel_Pi/videos`; images: the existing final-JPEG folder):
`<stamp>_<name>.sent` (the exact bytes chunked onto the wire) and `<stamp>_<name>.sent.json`
`{key, fmt, chunk_b64_chars, raw_bytes_per_msg, msgs, keyframe_chunks, sha256, sent_utc, res, crop, br, fps, dur}`.
~40 KB per clip, ~4 MB/day at 4 clips/h; `video_ring.ensure_room` bounds it. Retention follows the
heal window (24 h now, up to 30 d later, D8): the ring keeps `.sent` files at least that long.
Chunking is deterministic (`split_base64_chunks`), so a resend is a file read + slice.

## 4. `rsd` command (D2, tables v8) and the pending list (D5)

`{"id":N,"c":"rsd","v":<k>,"f":"<key>","r":"17,40-42"}` — validation in the backend spec §5.
State: `bm_command_state.json` gains `pending_heals: [{key, ranges, id, wakes_left: 3}]`, ≤ 8
entries, **newest key first**; `v=3` clears one/all. Ack immediately on receipt.

Execution, each wake: run the normal capture + burst; then while the budget allows
(`budget.has_time_for(n + 1)`), pop the newest pending heal and send its chunks as ordinary keyed
chunks at the normal pacing, then one `<HL v=1 key=… a=sent n=…>` line. A heal that does not fit
decrements `wakes_left`; at 0 it is dropped with `<HL a=dropped>`. Refusals (`no such clip`, key
outside the retention window, bad ranges) are acked `ok:0` and reported with `<HL a=refused>`.

## 5. Heal status line `<HL …>`

`<HL v=1 key=<key> a=<requested|sent|refused|dropped> n=<n> r=<reason> id=<cmd id>>`, sent on the
same topic as `<WS …>`; ~60 B; the backend parses it into telemetry (spec §4).

## 6. Where it fails, honestly

| Failure | Effect | Mitigation |
|---|---|---|
| command held while the bus is off (today's normal) | never heard | re-send until acked; BM-017 |
| `.sent` rotated out | `ok:0 no_such_clip`, `<HL refused>` | backend offers only in-window candidates |
| backend still on rev 3 | keyed chunks silently dropped (contract §5 today) | **backend deploys first**, accepts both forms |
| resend chunks rejected again by the Spotter stall | still partial | idempotent; the backend asks again |
| burst + heals exceed the budget | heal deferred | ≤ 62 msgs per heal; 3-wake limit |
| Spotter reset before the Notecard syncs | heal lost like any clip | operating rule; `<HL sent>` tells the backend it was tried |

## 7. Test ladder (device half)

1. Unit: daemon-first ordering (fakes, zero sleep); `parse_command` rejects; planner byte-identity
   vs the `.sent` file; keyed framing golden vectors for image and video.
2. USB console: keyed burst from the bench unit; drop START by hand; publish `rsd` on the Spotter
   console; ack; keyed resend; `<HL>`; reassembly with `spotter-usb-console-capture`.
3. One rig on cellular (bench), then release, then bmcam001 (reef) images.

## 8. Effort (estimates)

Daemon-first + benchmark ~1 day; keyed chunks + persisted payload (both modes) ~1 day; `rsd` +
pending list + `<HL>` + tests ~1 day; ladder 2–3 ~1 day. Backend (spec §3) ~2 days. Nothing starts
before the engineering review closes.
