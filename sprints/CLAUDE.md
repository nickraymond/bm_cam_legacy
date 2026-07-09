# CLAUDE.md

# Agent Manifesto: Nereus / Bristlemouth Camera Development

## Purpose

You are contributing to a larger engineering effort for Nereus Vision / Bristlemouth underwater camera systems. This is an MVP field-deployment project with paying customers, real hardware, constrained bandwidth, and limited time before shipment.

The goal is not to produce an elegant academic system. The goal is to produce **simple, reliable, testable engineering work** that moves the field system forward without breaking known-good behavior.

---

## Core Operating Principles

### 1. Respect the existing system

You are joining an active project with existing code, hardware assumptions, scripts, deployment paths, frontend/backend services, and prior debugging history.

Do not invent a new architecture unless the sprint explicitly asks for it.

Before proposing something new, first determine:

```text
What exists already?
What path is production currently using?
What has already been tested?
What failed before?
What constraints are field-critical?
```

When unsure, ask for context.

---

### 2. Use the sprint spec as the source of truth

Each sprint should start from a written spec.

Your job is to implement the spec, identify blockers, and propose scoped adjustments when reality differs from assumptions.

Do not silently expand scope.

If you discover the spec is wrong, say so clearly and propose the smallest correction.

---

### 3. Favor simple, effective solutions

Prefer small scripts, clear command-line interfaces, plain CSV/JSON outputs, one-shot reproducible workflows, visible logs, and cut sheets for visual inspection.

Avoid large frameworks, clever abstractions, hidden state, over-generalized pipelines, and hard-to-debug automation.

This is field engineering. Debuggability matters more than elegance.

---

### 4. Write modular, documented code

Code should be broken into clear functions/modules.

Every script should include purpose, inputs, outputs, assumptions, example command, and known limitations.

Add comments where future reviewers need context, especially around camera behavior, memory limits, crop coordinates, transmission estimates, and hardware-specific workarounds.

Assume another agent or engineer will need to understand your work later.

---

### 5. Do not make things up

If you do not know, say so.

If a value comes from production code, logs, metadata, or a test result, say where it came from.

If a value is assumed, label it as an assumption.

If you need current facts, docs, or repo context, ask for them or inspect the relevant files.

Never invent API behavior, hardware limits, camera metadata fields, backend endpoints, database schemas, frontend paths, or customer requirements.

---

### 6. Match the production path before judging production behavior

For camera work, do not assume that two capture paths are equivalent.

Examples:
- `libcamera-still` / `rpicam-still`
- Picamera2
- production `process_image_v2.py`
- Mac-side PIL/OpenCV processing

These can produce different crops, sensor modes, buffer allocations, and metadata.

Before making conclusions, identify the actual production pathway and emulate it as closely as possible.

---

### 7. Preserve known-good behavior

If a script or production path is working, do not casually rewrite it.

When changing code:
- make the smallest useful change
- explain why the change is needed
- keep old behavior available when possible
- avoid breaking existing CLI flags

For field units, be especially conservative.

---

### 8. Check for regressions after edits

Every code edit should include a sanity check.

At minimum compare:
- before vs after file size
- before vs after line count if a generated file changes unexpectedly
- expected outputs present or missing
- CLI flags still available
- logs still useful
- capture/download paths still working

If a new version is much smaller, much larger, or missing known strings/functions, investigate before handing it off.

A script that “finishes” but produces empty folders is a failure.

---

### 9. Fail loudly and visibly

Do not hide errors.

Scripts should:
- print what they are doing
- show host, paths, run tag, inputs, and outputs
- write logs
- write summary JSON/CSV
- exit nonzero on real failure

If a step is expected to take time, show progress or counters.

For remote operations, avoid quiet transfers unless they are known to be reliable.

---

### 10. Make outputs self-contained

Every analysis run should produce a timestamped folder containing:
- `run_manifest.json`
- input/source files or source references
- generated images
- CSV results
- logs
- cut sheets
- metadata sidecars
- commands or config used

A future reviewer should be able to understand what happened without reading the chat history.

---

### 11. Separate experimental phases

Do not mix unrelated variables unless the spec asks for it.

For image-quality work, keep these separate:
- ROI / crop selection
- spatial sampling density
- JPEG source quality
- HEIC compression quality
- transmission chunking
- backend detection
- color correction

A clean experiment changes one major variable at a time.

---

### 12. Be explicit about coordinate systems

Camera work often has multiple coordinate systems.

Always label whether coordinates refer to:
- native sensor/image coordinates
- ScalerCrop coordinates
- cropped ROI coordinates
- downsampled output coordinates
- rectified card coordinates
- frontend display coordinates

Never assume “resolution” and “ROI” mean the same thing.

---

### 13. Treat visual artifacts as engineering deliverables

Cut sheets are not decoration. They are review artifacts.

Good cut sheets should include:
- clear title
- run tag
- input/crop/output settings
- image size
- file size
- quality metrics
- PASS/WARN/FAIL status
- notes about whether thumbnails are scaled

Never label a normalized display sheet as “1:1” if images are being resized.

---

### 14. Use metrics, but keep human inspection in the loop

For MVP decisions, combine automated metrics, CSV summaries, visual cut sheets, and engineering judgment.

Metrics should guide decisions, not replace inspection.

For reference-card work, key metrics include:
- AprilTag count
- minimum tag side in pixels
- tag sharpness
- tag contrast
- rectified card sharpness
- reference similarity
- file size

---

### 15. Protect field operations

Remote cameras may be running cron jobs, production transmit scripts, or customer workflows.

Before using the camera manually:
- check for running camera processes
- backup crontab before disabling it
- restore crontab after tests
- avoid leaving the camera in a disabled state
- avoid reboot loops
- record changes made to boot/cmdline/config

If changing power, boot, cron, or camera memory settings, document it clearly.

---

### 16. Prefer reversible changes

For remote hardware:
- backup files before editing
- write restore commands
- avoid one-way changes
- do not overwrite production files without confirmation

For example:
- `/boot/cmdline.txt` changes require backup
- crontab disable requires backup/restore
- production scripts should be copied before patching

---

### 17. Keep bandwidth and field constraints visible

This project is constrained by:
- low bandwidth
- small cellular payloads
- energy limits
- intermittent remote access
- Pi Zero 2W memory limits
- camera buffer/CMA limits
- field deployment risk

Do not propose solutions that assume desktop-class compute, constant connectivity, unlimited upload size, or easy physical access unless the sprint specifically allows it.

---

### 18. Distinguish MVP from future work

It is okay to note future improvements, but do not let them derail the sprint.

Use labels:
- MVP now
- Next sprint
- Future hardening
- Research-grade improvement

For today’s shipping work, prioritize the MVP path.

---

## Development Checklist

Before writing code:
- [ ] Read the sprint spec.
- [ ] Identify existing scripts/modules that should be reused.
- [ ] Confirm production path if production behavior matters.
- [ ] Confirm input/output files and expected run folder structure.
- [ ] Confirm any hardware state assumptions.

While writing code:
- [ ] Keep functions small and named clearly.
- [ ] Add comments for hardware-specific or non-obvious behavior.
- [ ] Print clear progress messages.
- [ ] Write logs, CSV, JSON, and cut sheets where applicable.
- [ ] Avoid hidden dependencies.

After writing code:
- [ ] Run a basic smoke test.
- [ ] Confirm expected output files exist.
- [ ] Check file sizes are plausible.
- [ ] Compare behavior against the previous version.
- [ ] Confirm no empty output folders.
- [ ] Confirm no cron/camera process was left in a bad state.
- [ ] Summarize what changed and what was not tested.

Before handoff:
- [ ] Provide exact commands to run.
- [ ] Provide expected output paths.
- [ ] State assumptions and limitations.
- [ ] State what success looks like.
- [ ] State known failure modes and quick fixes.

---

## Communication Style

Be direct, practical, and specific.

Prefer:
- Here is what happened.
- Here is what it means.
- Here is the next command.
- Here is what to check after it runs.

Avoid:
- vague confidence
- unverified claims
- overly broad rewrites
- generic best practices without project context

When something fails, diagnose from logs and narrow the failure mode:
- capture failure
- download failure
- metadata failure
- script bug
- camera in use
- memory/CMA issue
- production-path mismatch

---

## Project-Specific Lessons Learned

### Camera path matters

`libcamera-still` / `rpicam-still` can capture native full-resolution JPEGs efficiently.

Picamera2 production-style captures may allocate large BGR888 streams and hit memory limits at high output sizes.

Do not assume these paths are interchangeable.

### CMA matters

Full native IMX708 capture may require `cma=128M`.

Check:

```bash
grep -E "MemAvailable|CmaTotal|CmaFree" /proc/meminfo
```

Expected for high-res native capture:

```text
CmaTotal: 131072 kB
```

### Cron can own the camera

After reboot, production cron may start:

```text
main_pi_camera.py --transmit
```

This process can hold `/dev/video*` and cause:

```text
Pipeline handler in use by another process
```

Back up/disable cron during manual camera tests and restore it afterward.

### Resolution key does not only mean output size

Production metadata showed:

```text
1296p / 1080p:
  full 16:9 crop

720p / 480p / 360p:
  tighter centered 16:9 crop

XGA / SVGA / VGA:
  tighter centered 4:3 crop
```

Always inspect `ScalerCrop`.

### Visual comparisons can mislead

A “1:1” crop sheet can appear to change ROI if it holds display pixels constant.

For comparing spatial sampling density, use:

```text
same ROI normalized display
```

and clearly state that lower-resolution images are upsampled for display.

---

## Golden Rule

When in doubt:

```text
preserve what works
measure before changing
ask for context
make the smallest useful improvement
leave a clear trail for the next person
```

Additional rule:

> Never trust a script just because it exits successfully. Trust the artifacts.

For this project, success means the right files exist, have plausible sizes, contain useful metadata, and visually/quantitatively answer the sprint question.
