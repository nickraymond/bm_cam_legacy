# Sprint19 backlog — live controls, mode presets, stills ring, app shell

Raised by Nick 2026-08-19 at the close of Sprint18. Not specced yet:
these are the four things to spec first, with the context each needs so
the next session does not start from zero.

---

## 1. Live controls — manual capture and live view

**What Nick asked for:** a button to take an image and save it, and a
live view that can either just show what the camera sees or record.
**Why:** so a customer can set field of view and mounting *before*
deployment. This is a setup tool, not a capture feature.

**The central design problem — one camera, one owner.** `/dev/video*`
is held by whatever is running: the video runtime holds it continuously
in video mode, and the RC cycle holds it during a stills cycle. A live
view cannot simply open the camera alongside them ("Pipeline handler in
use by another process" — see CLAUDE.md, Lessons Learned). So the sprint
has to decide the ownership model up front:
  - pause/hand-off: the runtime yields the camera for the duration of a
    live session and resumes after (needs a clean resume contract, and
    the ring/boot-sweep must tolerate the gap); or
  - the runtime itself serves the preview from the stream it already
    has (no second owner, but couples preview to the recorder); or
  - live view only in a dedicated "setup mode" where the runtime is not
    recording at all — simplest, and arguably the honest scope, since
    this is a pre-deployment tool.
Decide this in the SPEC session; it drives everything else.

**Older code to reference:**
- `BM_Devel_Pi/rc_progressive_jpeg.py` — has a real CLI (`argparse`,
  `--output-dir`) and `_default_capture()`; the existing single-shot
  path, including the quality/budget selector.
- `.claude/skills/bmcam-photo-check/SKILL.md` — the known-good *safe*
  manual capture (`rpicam-still -n --timeout 2000 ...`) and, more
  importantly, the documented hazard list for grabbing the camera on an
  armed unit. Read this before designing the button.
- `BM_Devel_Pi/video_recorder.py` `_build_argv()` — the rpicam-vid argv
  builder; a preview stream would reuse the same controls builder
  (`_camera_controls_from_settings`) so the preview matches what the
  camera will actually record.
- `tools/capture_native_full_reference.sh`.

**UI note:** the Sprint18 detail view already plays MP4 with Range
support; a live view is a different beast (MJPEG or a short-segment
stream) and must not be bolted onto the gallery player without saying so.

---

## 2. Development vs deployment mode presets in YAML

**What Nick asked for:** two presets that switch camera behaviour as a
set, rather than the customer (or us) hand-matching a dozen keys.

Likely axes: cron arming and power-halt, transmit on/off, session
length, log verbosity, network default (`ap` vs `nereus_hq` — Sprint16
already has the ship-vs-bench split), ring dry-run, and whether the
live-view tooling from item 1 is reachable at all.

**Constraint that bites:** the settings GUI *edits, it does not author*
(`video_settings.patch_yaml` refuses keys absent from the file). So a
mode preset cannot invent keys at save time — every key a preset touches
must already exist in every unit's YAML and in `rc_field_template`.
Decide whether a preset is (a) a named block the runtime reads, or
(b) a macro that rewrites individual keys. (a) avoids the
fleet-alignment chore; (b) keeps one source of truth per setting. This
is the real spec decision.

---

## 3. Ring buffer for still images

Already filed as **TODO-BM-014** (see TODO.md) with the full problem
statement: the ring is video-only, `images/` is never pruned, storage
limits need to become per-mode, and the ~3.5 MB native full-resolution
JPEG is the actual space hog versus ~50 KB for the transmitted copy.

Sprint18 states the risk in the settings page rather than implying the
ring covers stills. Sprint19 should make it true and then delete that
warning.

---

## 4. App shell — branded landing, firmware build, real navigation

**What Nick asked for:** a loading screen with a Nereus-branded image,
showing details like firmware build, and navigation between manual
controls, saved videos/images, and settings.

**Why now:** Sprint18 grew the app from one gallery to gallery +
images + settings, and item 1 adds a whole controls surface. The
current navigation is a single top-right button that toggles between
two pages; that does not survive a fourth destination.

**Notes:**
- Firmware build: the stills sidecar already carries `software_sha`
  (`rc_progressive_jpeg` writes it). Find or add the equivalent for the
  video runtime so the shell can show one honest build string.
- Keep the Sprint18 constraints: stdlib `http.server` only, no
  framework, no CDN, no webfont — the page is served by a Pi Zero 2W
  over an offline hotspot. A "loading screen" must not become a reason
  to pull in a bundler.
- The Nereus palette and components are already lifted from
  `nereus-vision-dev/backend/dashboard/gallery.html`; reuse, don't
  re-derive.
- A branded image has to be embedded (data URI) or served from the
  camera; it cannot be a remote asset.

---

## Sprint18 leftovers to fold in

- Open-AP banner is **mock-verified only** — bmcam003 has sat on
  `nereus_hq` all sprint, so the banner path has never rendered on
  hardware. Verify during the next AP session.
- Sprint18 tracker document was never written.
