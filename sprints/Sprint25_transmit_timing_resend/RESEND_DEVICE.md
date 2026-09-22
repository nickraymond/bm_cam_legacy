# Sprint25 part 2 — self-healing media, DEVICE side (design v3, 2026-09-22, post engineering review)

Companion to `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` v3 (§0a decisions, §0b open
questions). Review record: `REVIEW_20260922.md` in this folder. No code until §0b is signed.
v2 → v3 changes marked **[Rn]** (A = wire/backend review, B = device review, C = rollout review).

## 1. Daemon lifecycle (D3) — created in `main()`, stopped inside each cycle [R-B1, R-B2, R-C11]

Today `rc_progressive_jpeg.main()` dispatches `capture_mode: video` to `rc_video_tx` (`:929`)
before the stills cycle starts the daemon (`run_cycle`, `:517-525`), so `video_tx` wakes never
listen. The continuous recorder (`video_recorder.run_video_mode`) already runs its own daemon.

Change (scope: stills + `video_tx` wakes; continuous video keeps its daemon):
- `main()` keeps the D11 predicate (`bm_commands.enabled` AND (transmit OR bench_commands);
  `--capture-only` / `--print-config` never open the port) [R-B9], creates the daemon after settings
  load, and passes it into `run_cycle(...)` and `run_video_tx_cycle(...)`.
- **Ordering is owned by the cycle's `finally`: `cmd_hooks.shutdown(daemon)` → close the shared port
  → halt.** Never in `main()`'s `finally` (that runs after `systemctl halt` against a closed UART and
  loses the final ack flush and any `<HL>`). Extend `test_hlt_mid_transmit_ack_precedes_halt` to
  the video cycle.
- The `video_tx` port, item by item (Sprint24 step 2): the time gate uses `gate_kwargs_for(daemon)`
  (today `_default_gate` opens `/dev/ttyAMA0` privately and would race the daemon's reader);
  `transmit_video_clip` gains two optional callables (`pending_pump_fn`, `ack_drain_fn`, default
  None → wire unchanged); **video runs pump-only during the burst; acks and `<HL>` go out only after
  END via `flush_acks`, i.e. `defer=True` forced for video regardless of YAML** (an ack in a pacing
  slot during a 4–5 s stall costs one chunk) [R-B8]; `post_transmit_listen` after the burst;
  `_default_tx_open` already reuses the shared port.

**Benchmark, redefined [R-B3].** The Sprint23 ~38 s was stills, where the daemon already starts
before the gate; daemon-first moves ~0–2 s there. Report four `/proc/uptime` segments — `[RC-CRON]
uptime_s`, `main()` entry, `[CMD] subscribed`, `spotter UTC decoded` — plus one Spotter-clock anchor
(`Bridge bus power: 1` → the Pi's decoded UTC), 10 cold boots per mode on the unit that ships it.
Targets: video_tx = "subscribed exists at all" (today never); stills = no regression. The ~20 s
between cron and subscribe is `rc_run_capture_cycle.sh` (`py_compile` of 20 modules) + Python
imports — a separate, optional optimisation, not part of this change.

## 2. Keyed messages (D6, rev 5) [R-B5, R-B6, R-B12, R-C8]

- `key` = 6 lowercase base-36 chars, seconds since 2026-01-01Z, **from the Spotter UTC read of this
  wake** (`daemon.wait_for_spotter_utc` / the gate). No Spotter time this wake → send the legacy
  form (no key). Persist `last_key` in `bm_command_state.json`; a computed key ≤ `last_key` → legacy
  form + a log line (stale clock). Out-of-range → legacy form.
- Chunks `<I{key}.{n}>` (398 B); START `key=` right after `length` as a core key; **END unchanged**
  (P4). Same builder for images (`rc_transmit` image path) and video (`transmit_video_clip`). The
  Spotter limit is 1000 B hard / ~430 B fast path (Sprint09) → no payload shrink.
- Behind a YAML island (`media_key.enabled`, default OFF until the backend M0 is live). With it OFF
  every rev 3 golden-wire test stays byte-identical; with it ON a **separate rev 5 golden set**.
  `rc_media_id` (3-char gid) is retired.

## 3. Persisted sent payload (D7) [R-B4, R-C9]

- Video: the fitted payload exists only in tmpfs and is deleted in `fit`'s `finally` → write
  `<stamp>_<name>.sent` + `.sent.json` before START (tmp + fsync + rename, as `video_recorder` does).
- Images: the compressed JPEG on disk IS the wire bytes (`rc_transmit` chunks the file) → sidecar
  only (`.sent.json`: key, fmt, chunk_b64_chars, msgs, sha256, sent_utc).
- Resend chunks with the **sidecar's** `chunk_b64_chars`, never the live YAML.
- Retention is NOT covered by `video_ring` (it prunes only `.mp4` triples) and images have none:
  add one `sent/` prune — age ≤ heal window, hard cap 30 d (~120 MB) — run before START in both
  modes. The backend is the sole source of truth for "what is missing"; the device only validates
  `index < msgs`.

## 4. `rsd` (tables v8) and the pending list [R-B7, R-B10, R-C6, R-C7]

`{"id":N,"c":"rsd","h":[["<key>","17,40-42"],…]}` (≤ 8 heals) or `{"id":N,"c":"rsd","x":1}` = cancel
all. Ids are allocated by the backend. `parse_command`/`CommandState.record/save` are extended
explicitly (schema note) to carry `h` and `pending_heals`. Validation per heal: key
`^[0-9a-z]{6}$` + `.sent` exists + in window; ranges expand → dedupe → reject reversed / ≥ `msgs`;
≤ 60 chunks per **wake** across all heals. Refused heals are recorded so a re-sent command is
acked-duplicate, not re-refused with a fresh `<HL>`. Pending list: dedupe by key (newest id wins),
≤ 8, newest first, `wakes_left` 3 → `<HL a=dropped>`.

**Heal slot (Q1 for Nick).** Recommended: when the pending list is non-empty, send heals **before**
the new START (3–5 messages at +57 s, in the clean lane, capture delayed by seconds); anything left
goes after the burst only if it ends by **+240 s of the window** (`seconds_to_next_5min_boundary −
15`), else waits. `budget.has_time_for` is not boundary-aware — the boundary rule is new. `v=1`
whole-clip and `v=2` keyframe-only resends are cut from MVP.

## 5. `<HL>` line
`<HL v=1 key=<key> a=<requested|sent|refused|dropped> n=<n> r=<one-token> id=<id> w=<wake key>>`
built with `compact_kv_message`, sent through the cycle's `tx` (video: `cellular_only`), one per
key per wake, only after END.

## 6. Where it fails, honestly

| Failure | Effect | Mitigation |
|---|---|---|
| command held while the bus is off | never heard | re-send until acked; re-commit the grid after a Spotter reset; BM-017 |
| report minute outside the ~190 s listening span | never heard | same; §6 of the backend spec states the numbers |
| `.sent` pruned | `ok:0 no_such_clip`, `<HL refused>` | backend offers only in-window candidates |
| backend still rev 3 | keyed chunks would be dropped | **M0 lossless fallback deploys first**; key island OFF until then |
| stall rejects the resend again | still partial | idempotent; backend asks again; heal-chunk rejections counted separately |
| heals + burst cross the boundary | HDR/report collision | +240 s rule; ≤ 60 chunks/wake |
| stale clock | key reuse | Spotter-UTC-only keys, monotonic `last_key`, backend flags collisions |
| Spotter reset before Notecard sync | heal lost like any clip | ops rule; `<HL sent>` proves it was tried |

## 7. Test ladder (device half)
1. Unit: lifecycle ordering for both modes (fakes, zero sleep); `rsd` list validation; planner
   byte-identity vs `.sent`; key derivation (no Spotter time → legacy; monotonic); rev 3 golden
   wires unchanged with the island OFF; rev 5 golden set with it ON.
2. USB console: keyed burst; START dropped by hand; publish `rsd` on the Spotter console; ack; heal
   before START; `<HL>` after END; reassembly with `spotter-usb-console-capture`.
3. Rig B outdoors ≥ 24 h on cellular (backend spec §7.3); then release; live fire bmcam003/004.

## 8. Effort (estimates)
Lifecycle + video port + benchmark ~1.5 days; keyed chunks + island + payload/sidecar + prune ~1
day; `rsd` list + pending list + `<HL>` + tests ~1 day; ladder 2 ~0.5 day; ladder 3 = 1 day of
rig time. Backend (spec §3) ~3 days. Nothing starts before §0b is signed.
