#!/usr/bin/env python3
# filename: rc_still_storage.py
# description: Sprint26 S3c — the stills storage guard: keeps the SD under video.storage.* by pruning old stills.
"""
The stills storage guard (DESIGN_supervisor.md §4 Actions "storage guard";
PLAN_S3c.md J4 as amended by §5 C3–C5).

Why: every stills cycle leaves a 4608x2592 native (~4 MB off-device, measured
at the S3c gate on the Pi), its libcamera logs and metadata, the compressed
JPEG and its sidecar in images/. Before S3c nothing ever deleted them.

What it does (run by every supervisor stills action, before the capture):
  - reads the filesystem usage ONCE and applies each deletion arithmetically
    (as video_ring.ensure_room does: deterministic, immune to statvfs lag);
  - uses the SAME limit pair as the video ring (video.storage.max_used_pct,
    .min_free_gb, .ring_dry_run): one SD, one budget, so the two guards cannot
    fight over it;
  - prunes, oldest stem first, tier by tier, until both limits hold:
      0  debris: atomic-write temp files (.<name>.<rand>.tmp) older than 10 min
      1  natives (+ .stdout.log/.stderr.log/.metadata.json) of every stem that is
         NOT save_local: transmitted stems, pre-S3c history, capture-only natives
         (the transmitted crop stays; for save_local the native IS the record)
      2  whole save_local stems (native group + compressed JPEG + sidecar)
      3  transmitted compressed JPEG + sidecar pairs that no LIVE sent record
         names as its payload (media_key off, or the record aged out)
  - NEVER deletes a file a live sent record's `payload` names: for images the
    compressed JPEG on disk IS the heal payload (rc_media_key.py:24). "Live" =
    record mtime within media_key.retain_days (hard cap 30 d), computed here,
    because prune_sent only runs on a transmitting action;
  - reports `full` when the limits still cannot be met (the caller decides:
    a transmit action only WARNs, a save_local action refuses the capture).

Inputs: the images dir, the storage limits, the sent dir + retain_days.
Output: a result dict (see ensure_room). Never raises on a vanished file.

Coordinates/units: used_pct of the whole filesystem holding images/; free in
GiB (1024**3), the same units as video_ring.

Example:
  r = ensure_room("/home/pi/BM_Devel_Pi/images",
                  {"max_used_pct": 75.0, "min_free_gb": 10.0, "ring_dry_run": False},
                  sent_dir="/home/pi/BM_Devel_Pi/sent", retain_days=14)
  if r["full"]: ...

Known limitations: stems are ordered by name (ISO UTC, so oldest first when the
clock came from the Spotter); non-ISO stems (refsrc_*) sort last. A sidecar that
cannot be read counts as NOT save_local (its native is tier 1).
"""

import glob
import json
import os
import shutil
import time

GIB = 1024 ** 3
HARD_CAP_RETAIN_DAYS = 30.0          # rc_media_key.HARD_CAP_RETAIN_DAYS
STALE_TMP_S = 600                    # atomic_io.STALE_TMP_S: never a live writer's
NATIVE_SUFFIX = "_native_full.jpg"
NATIVE_SIDE = (".stdout.log", ".stderr.log", ".metadata.json")   # after "<stem>_native_full"
COMPRESSED_SUFFIX = "_compressed.jpg"
SIDECAR_SUFFIX = ".capture_metadata.json"                        # after "<stem>_compressed.jpg"


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _existing(paths):
    return [p for p in paths if os.path.exists(p)]


def stems(images_dir, list_fn=os.listdir):
    """{stem: {"native": [files], "compressed": [files]}} for every stills stem in
    images_dir, sorted by stem name (oldest first for ISO UTC stems)."""
    try:
        names = list_fn(images_dir)
    except OSError:
        return {}
    out = {}
    for name in names:
        if name.endswith(NATIVE_SUFFIX):
            stem = name[: -len(NATIVE_SUFFIX)]
            base = os.path.join(images_dir, stem + "_native_full")
            out.setdefault(stem, {"native": [], "compressed": []})["native"] = _existing(
                [base + ".jpg"] + [base + s for s in NATIVE_SIDE])
        elif name.endswith(COMPRESSED_SUFFIX):
            stem = name[: -len(COMPRESSED_SUFFIX)]
            jpg = os.path.join(images_dir, name)
            out.setdefault(stem, {"native": [], "compressed": []})["compressed"] = _existing(
                [jpg, jpg + SIDECAR_SUFFIX])
    return dict(sorted(out.items()))


def is_save_local(images_dir, stem):
    """True when the stem's sidecar says output=save_local. Unreadable/absent
    sidecar -> False (pre-S3c history and capture-only natives)."""
    path = os.path.join(images_dir, stem + COMPRESSED_SUFFIX + SIDECAR_SUFFIX)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("output") == "save_local"
    except (OSError, ValueError, AttributeError):
        return False


def live_payloads(sent_dir, retain_days, now_ts):
    """Real paths of every payload a live sent record names (heal payloads)."""
    retain_s = min(float(retain_days), HARD_CAP_RETAIN_DAYS) * 86400.0
    live = set()
    for rec in glob.glob(os.path.join(sent_dir or "", "*.sent.json")):
        try:
            if now_ts - os.path.getmtime(rec) > retain_s:
                continue
            with open(rec, "r", encoding="utf-8") as fh:
                payload = (json.load(fh) or {}).get("payload")
        except (OSError, ValueError, AttributeError):
            continue
        if payload:
            live.add(os.path.realpath(payload))
    return live


def _debris(images_dir, now_ts):
    out = []
    for path in glob.glob(os.path.join(images_dir, ".*.tmp")):
        try:
            if now_ts - os.path.getmtime(path) > STALE_TMP_S:
                out.append(path)
        except OSError:
            continue
    return sorted(out)


def plan_candidates(images_dir, sent_dir, retain_days, now_ts):
    """Deletion groups in prune order: [(tier, label, [files])]. Heal payloads
    (live sent records) are never in a group."""
    groups = [(0, os.path.basename(p), [p]) for p in _debris(images_dir, now_ts)]
    live = live_payloads(sent_dir, retain_days, now_ts)
    by_stem = stems(images_dir)
    saved = {s for s in by_stem if is_save_local(images_dir, s)}

    def protected(files):
        return any(os.path.realpath(f) in live for f in files)

    for stem, g in by_stem.items():                               # tier 1
        if stem not in saved and g["native"] and not protected(g["native"]):
            groups.append((1, stem, g["native"]))
    for stem, g in by_stem.items():                               # tier 2
        files = g["native"] + g["compressed"]
        if stem in saved and files and not protected(files):
            groups.append((2, stem, files))
    for stem, g in by_stem.items():                               # tier 3
        if stem not in saved and g["compressed"] and not protected(g["compressed"]):
            groups.append((3, stem, g["compressed"]))
    return groups


TIER_NAMES = {0: "debris", 1: "natives", 2: "save_local", 3: "unsent"}


def ensure_room(images_dir, storage_cfg, *, sent_dir=None, retain_days=HARD_CAP_RETAIN_DAYS,
                disk_usage_fn=None, remove_fn=None, now_fn=None, log_fn=print):
    """Prune (or dry-run report) until both limits hold. -> {
        used_pct, free_gb     : before pruning
        over                  : the limits were exceeded before pruning
        pruned                : {tier name: groups deleted}
        freed_bytes           : bytes deleted (0 in a dry run)
        would_free_bytes      : bytes a dry run would have deleted
        full                  : still over a limit after pruning (dry run: nothing freed)
        dry_run
    }. Never raises on file errors; a disk_usage failure raises (the caller
    logs it and goes on: a guard failure must not cost the capture).

    disk_usage_fn / remove_fn / now_fn default to the module attributes at CALL
    time (shutil.disk_usage, os.remove, time.time), so a harness can patch them."""
    disk_usage_fn = disk_usage_fn or DISK_USAGE_FN
    remove_fn = remove_fn or REMOVE_FN
    now_ts = (now_fn or NOW_FN)()
    max_used_pct = float(storage_cfg["max_used_pct"])
    min_free_gb = float(storage_cfg["min_free_gb"])
    dry_run = bool(storage_cfg.get("ring_dry_run", False))
    usage = disk_usage_fn(images_dir)
    used_pct0 = (100.0 * usage.used / usage.total) if usage.total else 0.0
    result = {"used_pct": round(used_pct0, 2), "free_gb": round(usage.free / GIB, 2),
              "over": False, "pruned": {}, "freed_bytes": 0, "would_free_bytes": 0,
              "full": False, "dry_run": dry_run}

    def over(freed):
        used_pct = (100.0 * (usage.used - freed) / usage.total) if usage.total else 0.0
        return used_pct > max_used_pct or (usage.free + freed) / GIB < min_free_gb

    if not over(0):
        return result
    result["over"] = True
    log_fn(f"[STORE] over limit: used={used_pct0:.1f}% (cap {max_used_pct:g}%) "
           f"free={usage.free / GIB:.2f}GiB (floor {min_free_gb:g}GiB) — pruning old stills"
           f"{' [DRY RUN]' if dry_run else ''}")
    freed = 0
    for tier, label, files in plan_candidates(images_dir, sent_dir, retain_days, now_ts):
        if not over(freed):
            break
        size = sum(_size(f) for f in files)
        freed += size
        name = TIER_NAMES[tier]
        if dry_run:
            result["would_free_bytes"] += size
            log_fn(f"[STORE][DRY] would delete {name} {label} ({size} B, {len(files)} files)")
            continue
        for path in files:
            try:
                remove_fn(path)
            except OSError as exc:
                log_fn(f"[STORE][WARN] failed to delete {path}: {exc}")
        result["pruned"][name] = result["pruned"].get(name, 0) + 1
        result["freed_bytes"] += size
    real_freed = result["freed_bytes"]
    if result["pruned"]:
        after = (100.0 * (usage.used - real_freed) / usage.total) if usage.total else 0.0
        log_fn(f"[STORE] pruned {result['pruned']}, freed {real_freed / 1e6:.1f} MB; "
               f"used {used_pct0:.1f}% -> {after:.1f}%")
    if over(real_freed):
        result["full"] = True
        log_fn(f"[STORE][FULL] limits still unmet after "
               f"{'a dry-run report' if dry_run else 'pruning every eligible still'} "
               f"(heal payloads and the video ring are not the stills guard's to delete)")
    return result


# Resolved at call time (see ensure_room): the golden harness pins the disk.
DISK_USAGE_FN = shutil.disk_usage
REMOVE_FN = os.remove
NOW_FN = time.time
