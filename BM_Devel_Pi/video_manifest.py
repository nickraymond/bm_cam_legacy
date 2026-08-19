#!/usr/bin/env python3
# filename: video_manifest.py
# description: Sprint15 sidecars + manifest.json + per-clip status messages.
"""
Sprint15 clip metadata (D-S15-4/6/9).

Per completed clip:
  - a `<base>.json` SIDECAR: the status-message fields plus duration,
    sha256 prefix, encode timings, and requested camera controls — the
    self-contained local record (manifesto rule 10).
  - one compact STATUS JSON line for the existing cellular tx path
    (constraint 9), parity with the still-image telemetry vocabulary:
      {"t":"vid","fn":...,"sz":...,"res":"1000x562","fps":15,"br":2.0,
       "dur":300,"tmp":52.1,"du":21.4,"dt":104.0,"rd":0}
    tmp = CPU temp C; du/dt = disk used/total GiB; rd = ring deletions.
  - `manifest.json` REGENERATED from the directory (the manifest IS the
    UI state, D-S15-9): newest-first clip list for the gallery.

StatusQueue: send failure never blocks recording — lines queue and retry
at the next clip boundary, drop-oldest beyond a small cap (D-S15-6).
"""

import hashlib
import json
import os
import shutil

GIB = 1024 ** 3

# Compact-JSON byte budget for one status line (same practical single-
# message bound the stills WS telemetry respects).
STATUS_MAX_BYTES = 280

# Bounded retry queue: at one line per clip a cap of 12 covers an hour of
# stalled sends before drop-oldest starts (constraint: never block, never
# grow without bound).
STATUS_QUEUE_CAP = 12


def sha256_prefix(path, chars=16):
    """First `chars` hex chars of the file's sha256 (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:chars]


def utc_from_basename(base):
    """'2026-08-17T23-40-00Z_video_...' -> '2026-08-17T23:40:00Z'."""
    ts = base.split("_", 1)[0]
    if len(ts) >= 20 and ts.endswith("Z"):
        return ts[:10] + "T" + ts[11:19].replace("-", ":") + "Z"
    return None


def build_clip_record(clip_result, settings, vcfg, ring_result, *,
                      cpu_temp_c=None, disk_usage=None):
    """The sidecar dict for one completed clip (superset of the status
    fields). cpu_temp_c/disk_usage are read by the caller so tests and
    the Mac never touch Pi sysfs."""
    base = clip_result["basename"]
    # Sprint17: geometry is the VIDEO island's, not the stills keys'. The
    # sidecar now carries the sensor mode and the available-detail figures,
    # because "res" alone cannot tell an honest 1080p from an upscaled one —
    # which is precisely how the Sprint15 defect stayed invisible.
    geo = vcfg["geometry"]
    w, h = geo["output_wh"]
    record = {
        "metadata_schema": "bmcam_video_sidecar_v2",
        "fn": base + ".mp4",
        "sz": int(clip_result["bytes"]),
        "res": f"{int(w)}x{int(h)}",
        "fps": int(geo["fps"]),
        "br": float(vcfg["bitrate_mbps"]),
        "dur": int(round(float(vcfg["clip_minutes"]) * 60)),
        "utc": utc_from_basename(base),
        "tmp": round(float(cpu_temp_c), 1) if cpu_temp_c is not None else None,
        "du": None,
        "dt": None,
        "rd": int(ring_result.get("deleted_count", 0)) if ring_result else 0,
        "thumb": os.path.basename(clip_result["thumb"])
        if clip_result.get("thumb") else None,
        "encode_s": round(float(clip_result.get("encode_s", 0.0)), 1),
        "boundary_s": round(float(clip_result.get("boundary_s", 0.0)), 1),
        "crop_native_xywh": list(geo["crop_native_xywh"]),
        "preset": geo["preset"],
        "sensor_mode": geo["sensor_mode"],
        "avail_px": f"{geo['available_px'][0]}x{geo['available_px'][1]}",
        "scale": geo["scale"],
        "encoder": dict(vcfg.get("encoder") or {}),
        "requested_controls": clip_result.get("requested_controls"),
    }
    if disk_usage is not None:
        record["du"] = round(disk_usage.used / GIB, 1)
        record["dt"] = round(disk_usage.total / GIB, 1)
    if clip_result.get("mp4"):
        try:
            record["sha256_16"] = sha256_prefix(clip_result["mp4"])
        except OSError:
            record["sha256_16"] = None
    return record


STATUS_FIELDS = ("fn", "sz", "res", "fps", "br", "dur", "tmp", "du", "dt", "rd")


def status_line_from_record(record):
    """One compact status JSON line (D-S15-6). Field order fixed for
    grep-ability; None values dropped to save bytes."""
    obj = {"t": "vid"}
    for key in STATUS_FIELDS:
        if record.get(key) is not None:
            obj[key] = record[key]
    line = json.dumps(obj, separators=(",", ":"))
    if len(line.encode("ascii", errors="ignore")) > STATUS_MAX_BYTES:
        # Should be unreachable with sane values; drop optional telemetry
        # before ever exceeding one message.
        for drop in ("tmp", "du", "dt", "rd"):
            obj.pop(drop, None)
        line = json.dumps(obj, separators=(",", ":"))
        print(f"[VID][WARN] status line trimmed to {len(line)} B")
    return line


def pause_status_line(ring_result):
    """Edge-triggered storage-pause telemetry (D-S15-5 loud pause)."""
    return json.dumps({
        "t": "vid",
        "a": "pause",
        "du": ring_result.get("used_pct"),
        "fg": ring_result.get("free_gb"),
        "rd": ring_result.get("deleted_count", 0),
    }, separators=(",", ":"))


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)
    return path


def write_sidecar(video_dir, base, record):
    """Atomic `<base>.json` next to the clip."""
    return _atomic_write_json(os.path.join(video_dir, base + ".json"), record)


def write_manifest(video_dir, generated_utc=None):
    """Regenerate manifest.json from the directory contents (newest
    first). The manifest is DERIVED state — a missing or stale sidecar
    degrades that entry, never the manifest."""
    import video_ring

    clips = []
    for triple in reversed(video_ring.completed_clip_triples(video_dir)):
        stem = triple["stem"]
        mp4_path = os.path.join(video_dir, stem + ".mp4")
        entry = {
            "name": stem + ".mp4",
            "bytes": os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0,
            "utc": utc_from_basename(stem),
            "thumb": None,
            "dur": None,
            "res": None,
            "fps": None,
            # Sprint18: the gallery card shows achieved-vs-set bitrate and
            # whether the recorded size is real detail. bytes/dur gives
            # achieved; br/preset/scale must come from the sidecar. Three
            # small fields here keep the LIST cheap — everything else stays
            # in the per-clip detail route.
            "br": None,
            "preset": None,
            "scale": None,
        }
        thumb = os.path.join(video_dir, stem + "_thumb.jpg")
        if os.path.exists(thumb):
            entry["thumb"] = stem + "_thumb.jpg"
        sidecar = os.path.join(video_dir, stem + ".json")
        if os.path.exists(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    rec = json.load(f)
                for key in ("dur", "res", "fps", "br", "preset", "scale"):
                    entry[key] = rec.get(key)
            except Exception:
                pass
        clips.append(entry)
    manifest = {"schema": "bmcam_video_manifest_v1",
                "generated_utc": generated_utc,
                "count": len(clips),
                "clips": clips}
    return _atomic_write_json(os.path.join(video_dir, "manifest.json"), manifest)


class StatusQueue:
    """Bounded drop-oldest retry queue for status lines (D-S15-6).

    flush(send_fn) sends FIFO until empty or the first failure; a failed
    line stays queued for the next boundary. Nothing here ever raises.
    """

    def __init__(self, cap=STATUS_QUEUE_CAP):
        self.cap = int(cap)
        self.lines = []
        self.dropped = 0

    def append(self, line):
        self.lines.append(line)
        while len(self.lines) > self.cap:
            self.lines.pop(0)
            self.dropped += 1
            print(f"[VID][WARN] status queue over cap "
                  f"({self.cap}); dropped oldest "
                  f"({self.dropped} dropped total)")

    def flush(self, send_fn):
        """Returns the number of lines sent."""
        sent = 0
        while self.lines:
            line = self.lines[0]
            try:
                send_fn(line + "\n")
            except Exception as exc:
                print(f"[VID][WARN] status send failed "
                      f"({len(self.lines)} queued, retry next boundary): {exc}")
                break
            self.lines.pop(0)
            sent += 1
            print(f"[VID] status sent: {line}")
        return sent

    def drain_print(self, label="NO transmit"):
        """No-bus mode: print-and-clear instead of sending."""
        for line in self.lines:
            print(f"[VID] status ({label}): {line}")
        drained = len(self.lines)
        self.lines = []
        return drained
