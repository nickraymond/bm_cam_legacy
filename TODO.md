# Nereus / BM Camera TODOs

This file tracks follow-up work from the Bristlemouth camera Sprint 04 release-candidate effort. IDs are stable and should be referenced in commits, PRs, and sprint notes.

## Open

### TODO-BM-001 — Backend media rows for partial BM image attempts

**Status:** Open  
**Priority:** High after shipment  
**Area:** Backend / ingestion / gallery data model

Create a real gallery-visible media/capture-attempt record for every BM `<START IMG>` image transmission, even when image reconstruction fails.

Desired behavior:

- Complete BM image with all chunks → save real image media row as today.
- `partial_missing` / `partial_open` / reconstruct failure → create a media/capture-attempt row with a unique black placeholder image and explanatory telemetry.
- Do not reuse a shared placeholder `r2_key`, because media creation is usually idempotent by object key.
- Use unique placeholder keys per capture, for example:
  - `BMCAM_000/2026/07/09/2026-07-09T14-31-50Z_placeholder_partial_missing.jpg`
- Preserve true capture/transmission metadata:
  - START filename / timestamp
  - external node ID
  - expected chunks
  - received chunks
  - missing chunk list/count
  - `partial_open` vs `partial_missing` vs `reconstruct_error`
  - BM status
  - image byte estimate if available

Why this matters:

The gallery should truthfully show that the camera captured/transmitted on cadence, even when downstream chunk completion or reconstruction failed. This avoids confusing “missing gallery image” with “camera did not capture.”

---

### TODO-BM-002 — First-class BM gallery events endpoint

**Status:** Open  
**Priority:** Medium/high  
**Area:** Backend / frontend contract

Create a normalized endpoint for BM gallery/capture events so `gallery.html` does not need to call the admin Sofar message probe directly.

Suggested endpoint shape:

```text
/devices/{device_id}/bm-gallery-events?start_utc=...&end_utc=...&page_size=...&cursor=...
```

Each event should include:

- capture timestamp from START filename
- received/start/end timestamps
- status: `complete`, `partial_missing`, `partial_open`, `reconstruct_error`
- media ID if a real media row exists
- placeholder media ID if a placeholder row exists
- expected/received/missing chunks
- image size bytes
- format
- duration
- delta from previous capture
- selected camera metadata fields

---

### TODO-BM-003 — Exact UTC range and pagination for BM probe

**Status:** Open  
**Priority:** Medium  
**Area:** Backend / admin debug tools

Update `/admin/ingest/sofar-message-probe` to support exact UTC windows and true pagination.

Current RC workaround:

- Frontend sends `hours` and raises `max_images_per_node` to 50.

Desired behavior:

- Accept `start_utc` and `end_utc` explicitly.
- Accept `image_offset` / `cursor` and `max_images_per_node`.
- Return `has_more_images`, `next_image_offset`, and total count before limit.
- Avoid “custom UTC range” in UI silently becoming “look back N hours from now.”

---

### TODO-BM-004 — Replace RC gallery overlay with backend-backed placeholder media

**Status:** Open  
**Priority:** Medium  
**Area:** Frontend cleanup

The RC `gallery.html` BM overlay calls the admin Sofar probe directly and renders black placeholders for partial/open BM transmissions. This is useful for release-candidate review but should be replaced once TODO-BM-001 and TODO-BM-002 are complete.

Keep the useful UI behavior:

- show every BM capture attempt as a card
- show black placeholder when no rendered image exists
- show status badge
- show chunks received/expected
- show delta from previous capture
- detail view should explain why an image did not render

Remove or reduce:

- direct dependency on `/admin/ingest/sofar-message-probe` from production gallery
- frontend-only matching between BM START timestamps and media rows

---

### TODO-BM-005 — BM frontend metadata polish

**Status:** Open  
**Priority:** Medium  
**Area:** Frontend / UX

Clean up BM metadata display after shipment.

Include:

- requested vs actual camera controls grouped together
- START storage metadata labels:
  - `st` = SD total MiB
  - `su` = SD used MiB
  - `sf` = SD free MiB
  - `sp` = SD used percent
  - `im` = images dir MiB
  - `bf` = buffer dir KiB
  - `lg` = cron_logs dir KiB
  - `zh` = zero-byte HEIC count
- END camera metadata labels:
  - `rfm`, `rlp`, `rwb`, `rcg`, `rem`, `rsh`, `rag`
  - `et_us`, `ag`, `dg`, `cg`, `cct`, `lp`, `afs`, `ffom`, `lux`, `fd_us`, `stemp`
- avoid dumping raw debug-only fields by default
- keep raw START/END messages accessible for debugging

---

### TODO-BM-006 — Transmission completeness dashboard

**Status:** Open  
**Priority:** Medium  
**Area:** Diagnostics / frontend

Add a compact diagnostic summary for BM capture/transmission health over a selected window.

Useful aggregate metrics:

- expected cadence
- total capture attempts
- complete count
- partial/open count
- missing chunk count distribution
- largest HEIC size
- max buffer count
- max UART duration
- image cadence delta min/max/mean
- first and last capture timestamp

This should make it easy to prove whether failures are camera-side, BM/Sofar transport-side, or backend parsing-side.

---

### TODO-BM-007 — Spotter/power/GPS correlation sprint

**Status:** Open  
**Priority:** High after shipment / next diagnostics sprint  
**Area:** Spotter / power / backend diagnostics

Add a way to correlate expected camera power windows with actual Spotter status.

Motivation:

If Spotter cannot get GPS/time or does not power the camera at a scheduled interval, the Pi will not boot and cannot capture. Current BM image logs prove camera cadence only after the Pi boots and START messages arrive.

Desired data:

- Spotter GPS/time validity per interval
- camera power on/off command times
- Pi boot observations
- first BM message after boot
- reason for skipped power interval if available

---

### TODO-BM-008 — SD-card ring buffer

**Status:** Open  
**Priority:** Low/medium  
**Area:** Pi runtime storage management

Implement a local storage ring buffer after shipment.

Current state:

- SD reporting exists and is transmitted in START metadata.
- Ring buffer is intentionally not implemented yet.

Requirements:

- dry-run mode first
- never delete current active capture/transmit artifacts
- delete oldest safe artifacts first
- preserve recent logs and metadata sidecars where practical
- protect against deleting OS/system files
- include telemetry when cleanup happens


---

## RC follow-up items added 2026-07-09

### TODO-COLOR-001 — Local color-correction smoke test before/after shipment
**Status:** open  
**Priority:** high  
**Context:** Before building the full backend color-correction pipeline, create a small local smoke-test app/script that can take a backend-rendered BM image with the reef reference card visible, detect/use the card, apply a basic color correction, and write a corrected derivative for visual QA.  
**Acceptance criteria:**
- Input: one downloaded/rendered BM image.
- Output: corrected preview image plus a short metrics/QA summary.
- Does not block camera shipment unless the image/reference-card data is unusable.
- Later backend work can reuse the approach for batch/backfill processing.

### TODO-BM-005 — Investigate BM gallery completed-but-not-rendered images
**Status:** open  
**Priority:** medium  
**Context:** The RC BM gallery overlay shows some `complete` BM transmissions as black placeholders with “No gallery image rendered.” These should have enough chunks to reconstruct, so either the media row, display derivative, R2 upload, or gallery/media matching logic is incomplete.  
**Acceptance criteria:**
- For each complete BM transmission, determine whether reconstructed bytes exist.
- Determine whether a media row was created.
- Determine whether `display_key` / thumbnail derivative exists.
- Fix the pipeline so complete BM transmissions render as real gallery images when possible.

### TODO-BM-006 — Fix BM gallery overlay pagination and image count controls
**Status:** open  
**Priority:** medium  
**Context:** The RC BM gallery overlay is useful for debugging but broke or bypassed normal gallery pagination behavior. The page-size selector and navigation buttons need to work predictably for BM overlay mode.  
**Acceptance criteria:**
- Page size selector controls the number of BM cards shown.
- Previous/Next work in BM overlay mode.
- The count pill clearly distinguishes BM transmissions loaded vs renderable media rows.
- No regression to normal `/devices/{device_id}/media` gallery mode.

### TODO-BM-007 — Replace RC BM gallery overlay with backend-backed placeholder media rows
**Status:** open  
**Priority:** medium/long-term  
**Context:** The RC overlay proves the UX need: show every BM capture/transmission attempt, even if image reconstruction fails. Long term, the backend should create a real media/capture-attempt row for every BM START, with either a real image or a unique placeholder image plus telemetry explaining the failure.  
**Acceptance criteria:**
- One gallery-visible row per BM image START.
- Partial/missing/open/reconstruct-failed attempts show unique placeholder images.
- Telemetry records missing chunks, expected/received chunks, START/END metadata, and reconstruct status.
- Gallery no longer needs to call the admin BM probe endpoint for normal display.


---

## RC follow-up items added after BM display-derivative backend repair

### TODO-BM-008 — Fix BM gallery overlay matching to real media rows
**Status:** open  
**Priority:** medium  
**Context:** Backend display derivative repair is now working: complete BM HEIC media rows can have JPEG `display_key` values and normal gallery mode renders them. The remaining issue is the RC BM overlay/debug gallery can still show black placeholders because it matches BM transmission attempts to media rows too narrowly. BM transmission START filename time can differ from media `captured_at_utc`/END time.  
**Observed example:**
- BM START filename: `2026-07-09T17:45:41Z_image_compressed.heic`
- Media captured/end time: `2026-07-09T17:56:52Z`
- Media row: `30697`
- Normal gallery renders after display derivative repair.
- BM overlay may still show placeholder if matching by START timestamp only.

**Acceptance criteria:**
- BM overlay matches completed transmissions to existing media rows using telemetry fields:
  - `bm_start_filename`
  - `bm_end_filename`
  - `external_node_id`
  - `expected_chunks`
  - `received_chunks`
  - `image_size_bytes`
- If a matching media row has a valid `image_url`, show the real image instead of a black placeholder.
- If no media row exists, continue showing the black placeholder with reason.

### TODO-BM-009 — Confirm background Sofar poll worker repairs missing BM display derivatives
**Status:** open  
**Priority:** high for backend validation, low for Pi firmware  
**Context:** A backend patch was added in `nereus-vision-dev` to repair existing HEIC media rows missing `display_key`. The manual admin backfill endpoint succeeded for BMCAM_000 and normal gallery mode now renders recent BM images. Need confirm the scheduled/background Sofar poll worker path also creates or repairs JPEG display derivatives for future complete BM images without manual intervention.

**Acceptance criteria:**
- New complete BM image arrives.
- `/devices/BMCAM_000/media` row exists.
- `image_url` points to `/display/...jpg`.
- `image_url_has_display === true`.
- Normal gallery renders image without manual backfill.
- Worker logs show no display derivative errors.

### TODO-BM-010 — Add first-class backend capture-attempt rows or placeholder media for partial BM images
**Status:** open  
**Priority:** medium / post-shipment  
**Context:** The RC BM overlay proved the value of showing every capture/transmission attempt, including partial/open transmissions. Long term, the backend should represent partial BM captures directly instead of relying on a frontend-only overlay.

**Acceptance criteria:**
- One backend-visible record exists per BM START image attempt.
- Complete images point to real display JPEGs.
- Partial/missing/open attempts show unique placeholder cards/images.
- Telemetry records missing chunks, received/expected chunks, START/END metadata, and reconstruction status.
- Normal gallery can show the full capture timeline without relying on the admin BM probe endpoint.

---

### TODO-BM-011 — bmcam000 recovery: Tailscale re-join + update to development

**Status:** Open (logged 2026-08-17, Nick priority #1)
**Area:** Fleet ops / bmcam000

bmcam000 (LAN 192.168.86.23) is not reachable over Tailscale. Recover it,
then bring it current with `development`.

Steps:

1. **Before touching the unit:** check the SPOT-31593C Sofar cloud
   mailbox for the stale `twn 2` (id 3001) queued during Sprint12 §5 —
   it may still deliver on the next successful drain (hazard carried in
   the Sprint13 tracker).
2. Re-join Tailscale (LAN SSH via 192.168.86.23; pi-tailscale-setup
   skill if a reinstall/re-auth is needed). Mind the 15/45 duty cycle +
   real halt: the unit is only up ~15 min/hr, and prior SSH attempts
   failed on the DERP relay with short connect timeouts.
3. Update via `tools/rc_field_update.sh --ref development` (disarm →
   sync → deploy runtime → patch bm_serial values → UART gate →
   validate → re-arm). bmcam-field-update skill is the runbook.
4. Optionally complete the orphaned Sprint12 §5 remote validation
   (`twn 2` + `trg 2` via Sofar Command API) while the unit is healthy —
   record the decision either way.

**Acceptance:** unit on the tailnet, running development tip, armed,
completing a normal transmit cycle; command state file clean.


---

### TODO-BM-012 — NetworkManager WiFi control over Bristlemouth (AP / HQ / customer WiFi)

**Status:** Open (logged 2026-08-18; expanded same day — Nick spec, next work item)
**Area:** Fleet ops / command daemon / networking / settings GUI
**Priority:** Next sprint. Customer-WiFi entry over BM is test-when-convenient, NOT a blocker (Nick).

All units are (or are becoming) Trixie/NetworkManager. Rewrite
`network_ap.sh` around `nmcli` and give the fleet three switchable
network behaviors, BM-commandable and YAML-defaulted:

1. **Nereus HQ WiFi** — join the office network using credentials
   stored on the unit (provisioned, persistent NM profile).
2. **AP mode** — open hotspot (NO password — confirmed possible with
   NM), SSID = the unit hostname (e.g. `bmcam000`), so field techs just
   tap the unit's name in their WiFi list. Gallery/settings at the AP
   address. CAVEAT (accepted, documented): open AP means anyone in
   radio range can reach the gallery + settings UI while AP is up.
3. **Customer WiFi (ephemeral)** — user-supplied SSID + password,
   valid for the CURRENT power cycle only, forgotten on reboot
   (NM in-memory connection — `nmcli connection add save no`; verify
   flag support on the Trixie NM version). Entry paths:
   - Settings GUI page (primary — customer joins the AP, opens
     /settings, types their WiFi creds; no BM message-size limits)
   - BM command (remote path; must solve SSID+PSK vs message size cap
     and log redaction on the Pi)

**Boot default (the ship switch):** a `network:` YAML island +
settings-GUI control choosing what the unit does at power-on:
`default: nereus_hq` (bench/office fleet) or `default: ap` (customer
ship config — Nick ships units defaulting to AP so customers can
always reach the download UI). Reboot = return to the default, which
also makes the power cycle the universal un-brick for behavior 3.

**Safety doctrine carried over from D-S15-10:** any REMOTE flip away
from the current working network arms a verified auto-revert timer
BEFORE flipping; nothing a garbled command can do strands the unit
past a timer + power cycle.

**Acceptance (bench, attended):** each of the three behaviors
demonstrated on a Trixie unit; boot-default honored for both YAML
values; ephemeral customer creds gone after power cycle; auto-revert
proven with a bogus SSID; command reference + skills updated (replaces
the wap NOT-FIELD-READY entry).

---

### TODO-BM-013 — Video-only resolution ladder + hidden H.264 quality knobs (1080p underwater)

**Status:** Open (logged 2026-08-18 evening, Nick — tomorrow's work)
**Area:** video pipeline / settings GUI / SPEC change

Nick wants crisp underwater HD: more VIDEO-ONLY resolution options
(e.g. real 1080p) plus access to the H.264 quality levers that are
currently hidden (encoder runs on rpicam-vid defaults for everything
except bitrate/fps).

**Design tension to resolve first (deliberate SPEC change):** Sprint15
constraint 4 says video inherits the STILLS geometry — today that is a
1600x900 native crop → 1600 output max. True 1920x1080 needs a wider
native crop, so this sprint must introduce video-only geometry
(e.g. `video.resolution: 1080p` presets mapping to their own
crop+output), leaving stills geometry untouched. Upscaling past the
crop stays forbidden (no fake resolution).

**Hidden knobs to consider exposing (from build_encoder_command — we
pass only width/height/fps/bitrate/roi today, everything else is
rpicam-vid defaults):**
- `--qp` — constant quantizer, the true "JPEG quality" analog
  (constant quality, variable size) vs today's `--bitrate` targeting
- `--profile high` / `--level` — H.264 profile (compression
  efficiency at the same bitrate)
- `--intra` — keyframe interval (GOP)
- `--denoise cdn_hq|cdn_fast|cdn_off` — colour-denoise mode, likely a
  big lever for underwater particulate scenes
- `--sharpness` / `--contrast` / `--saturation` — ISP tuning
- sensor mode: binned 2304x1296 vs full 4608x2592 readout (detail vs
  CPU/power; Zero 2W H.264 encoder is comfortable to ~1080p30)

**Approach sketch:** `video.resolution` preset table (video-only crop +
output + the encoder extras per preset), GUI dropdown, bench A/B on
bmcam000 with side-by-side clips in the gallery; power/thermal check at
1080p (encode time must stay < clip wall time; watch CPU temp in
sidecars).

**Acceptance:** 1080p clips ffprobe-verified at 1920x1080 from a
≥1920x1080 native crop; A/B cut of denoise/qp/profile variants for
Nick's eyeball; suite green; stills path byte-identical.
