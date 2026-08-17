#!/usr/bin/env python3
# filename: video_ring.py
# description: Sprint15 video storage ring guard (D-S15-5; TODO-BM-008 made real).
"""
Sprint15 video ring buffer — the unbrickable-storage guarantee.

Before each clip starts, the recorder calls ensure_room(). If EITHER
trigger fires (stricter wins) —
    filesystem used  > video.storage.max_used_pct   (primary knob, 75)
    filesystem free  < video.storage.min_free_gb    (absolute backstop, 10)
— the OLDEST completed clip triples (mp4 + poster thumb + sidecar JSON)
are deleted, oldest-first by timestamp filename (lexicographic ==
chronological, D-S15-4), until both limits are satisfied.

TODO-BM-008 rules, all enforced here:
  - Only COMPLETED clip triples in the video directory are candidates.
    Never .part/.tmp crash debris (that is boot-sweep territory), never
    stills artifacts, never logs, never anything outside the directory.
  - ring_dry_run: true reports what WOULD be deleted and deletes nothing.
  - Every deletion is logged and counted (the `rd` status field).
  - If the limits cannot be met even after the ring is empty (disk eaten
    by something else — or dry_run withheld the deletions), the caller
    must PAUSE recording rather than write the disk toward 0. That is the
    paused=True flag; pausing, not bricking, is the contract
    (SPEC constraint 5).

Units: free space floor is GiB (1024^3 bytes) — the conservative reading
of the spec's "GB" (a 10 GiB floor > 10 GB floor).
"""

import os
import shutil

GIB = 1024 ** 3

# Suffixes that mark a COMPLETED clip triple. Anything else in the video
# dir (crash debris, logs, strangers) is never a prune candidate.
_MP4_SUFFIX = ".mp4"
_THUMB_SUFFIX = "_thumb.jpg"
_SIDECAR_SUFFIX = ".json"

# manifest.json (D-S15-9) lives in the video dir but is NOT a clip triple.
_PROTECTED_NAMES = {"manifest.json"}


def completed_clip_triples(video_dir):
    """Oldest-first list of completed clip triples.

    Returns [{"stem": str, "files": [paths...], "bytes": int}, ...] sorted
    by mp4 filename (timestamps sort chronologically). Only finals count:
    a `.mp4` present in the directory, plus its `_thumb.jpg` / `.json`
    companions when they exist. `.part`/`.tmp` are invisible here.
    """
    triples = []
    try:
        names = sorted(os.listdir(video_dir))
    except FileNotFoundError:
        return triples
    for name in names:
        if not name.endswith(_MP4_SUFFIX) or name in _PROTECTED_NAMES:
            continue
        mp4_path = os.path.join(video_dir, name)
        if not os.path.isfile(mp4_path):
            continue
        stem = name[: -len(_MP4_SUFFIX)]
        files = [mp4_path]
        for companion in (stem + _THUMB_SUFFIX, stem + _SIDECAR_SUFFIX):
            path = os.path.join(video_dir, companion)
            if os.path.isfile(path):
                files.append(path)
        total = 0
        for path in files:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        triples.append({"stem": stem, "files": files, "bytes": total})
    return triples


def _usage(video_dir, disk_usage_fn):
    u = disk_usage_fn(video_dir)
    used_pct = (100.0 * u.used / u.total) if u.total else 0.0
    return used_pct, u.free / GIB, u


def ensure_room(video_dir, storage_cfg, *, disk_usage_fn=shutil.disk_usage,
                remove_fn=os.remove, log_fn=print):
    """Prune (or dry-run report) until both storage limits are satisfied.

    Disk usage is read ONCE and the effect of each deletion is applied
    arithmetically — deterministic for tests, and immune to statvfs lag.
    Returns a dict the status message and logs feed from:
      used_pct/free_gb   : state BEFORE pruning
      deleted            : list of stems actually deleted
      deleted_count      : len(deleted)  (the `rd` status field)
      would_delete_count : stems a dry run WOULD have deleted
      paused             : True = recording must not start (real state
                           still over a limit; dry_run does not fake this)
    """
    max_used_pct = float(storage_cfg["max_used_pct"])
    min_free_gb = float(storage_cfg["min_free_gb"])
    dry_run = bool(storage_cfg["ring_dry_run"])

    used_pct0, free_gb0, usage = _usage(video_dir, disk_usage_fn)
    result = {
        "used_pct": round(used_pct0, 2),
        "free_gb": round(free_gb0, 2),
        "deleted": [],
        "deleted_count": 0,
        "would_delete_count": 0,
        "paused": False,
        "dry_run": dry_run,
    }

    def over(freed_bytes):
        used_pct = (100.0 * (usage.used - freed_bytes) / usage.total
                    if usage.total else 0.0)
        free_gb = (usage.free + freed_bytes) / GIB
        return used_pct > max_used_pct or free_gb < min_free_gb

    if not over(0):
        return result

    log_fn(f"[RING] over limit: used={used_pct0:.1f}% "
           f"(cap {max_used_pct:.0f}%) free={free_gb0:.1f}GiB "
           f"(floor {min_free_gb:.0f}GiB) — pruning oldest clips"
           f"{' [DRY RUN]' if dry_run else ''}")

    freed = 0
    for triple in completed_clip_triples(video_dir):
        if not over(freed):
            break
        freed += triple["bytes"]
        if dry_run:
            result["would_delete_count"] += 1
            log_fn(f"[RING][DRY] would delete {triple['stem']} "
                   f"({triple['bytes']} B, {len(triple['files'])} files)")
            continue
        for path in triple["files"]:
            try:
                remove_fn(path)
            except OSError as exc:
                log_fn(f"[RING][WARN] failed to delete {path}: {exc}")
        result["deleted"].append(triple["stem"])
        log_fn(f"[RING] deleted {triple['stem']} "
               f"({triple['bytes']} B, {len(triple['files'])} files)")
    result["deleted_count"] = len(result["deleted"])

    # The pause decision uses the REAL post-prune state: a dry run freed
    # nothing, so a dry-run unit at the floor pauses (loudly) instead of
    # recording the disk toward 0 — unbrickable beats convenient.
    real_freed = 0 if dry_run else freed
    if over(real_freed):
        result["paused"] = True
        log_fn(f"[RING][PAUSE] limits still unmet after "
               f"{'dry-run report' if dry_run else 'emptying the ring'} "
               f"(freed {real_freed} B): recording PAUSED, not written to 0")
    return result
