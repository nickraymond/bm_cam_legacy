#!/usr/bin/env python3
# filename: rc_video_clip.py
# description: Sprint22 — fit a recorded clip to a MESSAGE budget (2-pass x264 -> raw Annex-B payload).
"""
Sprint22 Phase 2 — turn a finished recording into the payload the BM uplink
sends (docs/bm_media_wire_contract.md section 6): raw Annex-B H.264, one
keyframe, x264's version SEI stripped, SPS/PPS inside chunk 0.

The link gives a MESSAGE budget; bytes are messages (288 raw bytes each). So
the job is "spend this many bytes as well as possible", not "pick a quality":

  1. decode the source ONCE into a small raw intermediate (the LAST `duration_s`
     of the recording, scaled to the send geometry) — both passes read it, so
     the 1080p decode is paid once (8 s on a Pi Zero 2 W; the encodes are ~1 s)
  2. x264 pass 1: a dry run that only writes per-frame complexity notes
  3. x264 pass 2 at 96 % of the budget, SEI stripped in the same command
  4. a 5 s clip is too short for rate control to land reliably (measured on
     bmcam004 2026-09-21: 67-83 % of target on a calm scene, 99-104 % on a busy
     one) — so pass 2 ALONE is re-run with a proportional correction, reusing
     the pass-1 notes, until the payload uses 93-100 % of the budget
  5. still over after the last try -> trim whole frames off the tail. Raw
     Annex-B plays up to a cut, so the clip stays valid and "complete" as sent.

Nothing here touches the UART, the camera, or the clock. Subprocess + file
access are injectable for zero-ffmpeg unit tests.

Known limitations:
  - the tail trim cuts in DECODE order; with B-frames a trimmed clip may lose
    one or two frames that sit before the cut in display order. It is the last
    resort after three corrected encodes and was never needed in the ladder.
  - one keyframe per clip: chunk 0 (SPS/PPS + the start of the IDR) is a single
    point of failure on the wire — covered by the chunk-0 repeat, not here.
"""

import os
import re
import subprocess
import time

RAW_BYTES_PER_MSG_DEFAULT = 288        # 384 base64 chars (bm_serial.image_buffer_size)
TARGET_FRACTION = 0.96                 # aim low: a 2-pass miss can land either side
ACCEPT_LOW, ACCEPT_HIGH = 0.93, 1.0    # fraction of the budget a payload may use
CORRECTION_AIM = 0.965                 # what a corrected retry steers toward
MAX_PASS2_TRIES = 3
ENCODE_TIMEOUT_S = 180
_START_CODE = re.compile(b"\x00\x00\x01")


class ClipError(Exception):
    """A clip could not be produced. The message names the stage and the fix."""


def _default_run(argv, timeout_s):
    """Run one ffmpeg command. Returns (returncode, tail_of_output)."""
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout_s}s"
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")[-400:]


def nal_units(payload):
    """[(start_offset, nal_type)] for every NAL unit; start includes its start code."""
    out = []
    for hit in _START_CODE.finditer(payload):
        begin = hit.start()
        if begin > 0 and payload[begin - 1] == 0:
            begin -= 1                                  # 4-byte start code
        if hit.end() < len(payload):
            out.append((begin, payload[hit.end()] & 0x1F))
    return out


def check_payload(payload, raw_bytes_per_msg=RAW_BYTES_PER_MSG_DEFAULT):
    """Contract section 6 + 8: starts with SPS, SPS+PPS end inside chunk 0, no SEI.
    Raises ClipError naming what is wrong."""
    units = nal_units(payload)
    if not units or units[0][0] != 0 or units[0][1] != 7:
        raise ClipError("payload does not start with an SPS NAL (type 7) — the SEI "
                        "strip failed or the encoder emitted an AUD first")
    types = [t for _, t in units]
    if 6 in types:
        raise ClipError("payload still contains an SEI NAL (type 6); check the "
                        "filter_units=remove_types=6 bitstream filter")
    if 8 not in types:
        raise ClipError("payload has no PPS NAL (type 8)")
    after_pps = units[types.index(8) + 1][0] if types.index(8) + 1 < len(units) else len(payload)
    if after_pps > raw_bytes_per_msg:
        raise ClipError(f"SPS+PPS end at byte {after_pps}, outside chunk 0 "
                        f"({raw_bytes_per_msg} B): the chunk-0 repeat would not protect them")


def trim_to_budget(payload, budget_bytes):
    """Drop whole frames off the tail until the payload fits. Returns
    (payload, frames_dropped). Cuts only at a slice NAL boundary (type 1/5) and
    never removes the first slice. Raises ClipError if even one frame does not fit."""
    if len(payload) <= budget_bytes:
        return payload, 0
    slices = [off for off, t in nal_units(payload) if t in (1, 5)]
    keep = [off for off in slices[1:] if off <= budget_bytes]
    if not keep:
        raise ClipError(f"even the keyframe alone ({slices[1] if len(slices) > 1 else len(payload)} B) "
                        f"exceeds the {budget_bytes} B budget — lower the resolution or raise the cap")
    cut = keep[-1]
    return payload[:cut], sum(1 for off in slices if off >= cut)


def fit_clip_to_budget(source_path, work_dir, *, width, height, fps, duration_s,
                       budget_msgs, raw_bytes_per_msg=RAW_BYTES_PER_MSG_DEFAULT,
                       source_wh=None, preset="medium", ffmpeg_binary="ffmpeg",
                       run_fn=_default_run, clock=time.monotonic, log_fn=print):
    """Encode the LAST `duration_s` seconds of `source_path` to fit `budget_msgs`.

    Returns {payload, bytes, msgs, budget_msgs, used_pct, target_kbps, pass2_tries,
             frames_trimmed, prescale_s, encode_s}. Raises ClipError on failure;
    every temp file is removed either way.
    """
    width, height, fps = int(width), int(height), int(fps)
    budget_msgs = int(budget_msgs)
    if budget_msgs < 2:
        raise ClipError(f"budget of {budget_msgs} messages cannot hold a clip")
    if source_wh and (width > source_wh[0] or height > source_wh[1]):
        raise ClipError(f"refusing to UPSCALE {source_wh[0]}x{source_wh[1]} -> {width}x{height} "
                        f"(video_tx.output must not exceed the recording)")
    if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
        raise ClipError(f"source clip missing or empty: {source_path}")

    budget_bytes = budget_msgs * int(raw_bytes_per_msg)
    n_frames = int(round(float(duration_s) * fps))
    os.makedirs(work_dir, exist_ok=True)
    ref = os.path.join(work_dir, "vtx_ref.y4m")
    out = os.path.join(work_dir, "vtx_payload.h264")
    passlog = os.path.join(work_dir, "vtx_pass")
    quiet = [ffmpeg_binary, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]

    def x264(kbps):
        return ["-c:v", "libx264", "-preset", preset, "-b:v", f"{kbps:.1f}k",
                "-pix_fmt", "yuv420p", "-g", str(n_frames + 1),
                "-keyint_min", str(n_frames + 1), "-sc_threshold", "0", "-an"]

    try:
        t0 = clock()
        rc, tail = run_fn(quiet + ["-sseof", f"-{float(duration_s):g}", "-i", source_path,
                                   "-vf", f"scale={width}:{height},fps={fps}",
                                   "-frames:v", str(n_frames), "-pix_fmt", "yuv420p",
                                   "-f", "yuv4mpegpipe", ref], ENCODE_TIMEOUT_S)
        if rc != 0 or not os.path.isfile(ref) or os.path.getsize(ref) <= 0:
            raise ClipError(f"prescale failed (rc={rc}): {tail.strip() or 'no output'}")
        prescale_s = clock() - t0

        kbps = budget_bytes * 8 / float(duration_s) / 1000 * TARGET_FRACTION
        t1 = clock()
        rc, tail = run_fn(quiet + ["-i", ref] + x264(kbps) +
                          ["-pass", "1", "-passlogfile", passlog, "-f", "null", os.devnull],
                          ENCODE_TIMEOUT_S)
        if rc != 0:
            raise ClipError(f"x264 pass 1 failed (rc={rc}): {tail.strip()}")

        # Rate response is steeply NON-linear on some scenes (bmcam004 2026-09-21:
        # 55.7k -> 77 % of budget, 69.4k -> 131 %), so a purely proportional
        # retry can bounce across the budget. Keep the BEST payload that fits,
        # and once one try is under and one is over, interpolate between them
        # (log-log) instead of extrapolating from the last miss.
        import math
        best = None                                  # (bytes, payload, kbps)
        under = over = None                          # (kbps, bytes) brackets
        target_bytes = CORRECTION_AIM * budget_bytes
        last_payload, tries = b"", 0
        for tries in range(1, MAX_PASS2_TRIES + 1):
            rc, tail = run_fn(quiet + ["-i", ref] + x264(kbps) +
                              ["-pass", "2", "-passlogfile", passlog,
                               "-bsf:v", "filter_units=remove_types=6", "-f", "h264", out],
                              ENCODE_TIMEOUT_S)
            if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) <= 0:
                raise ClipError(f"x264 pass 2 failed (rc={rc}): {tail.strip() or 'no output'}")
            with open(out, "rb") as fh:
                last_payload = fh.read()
            size = len(last_payload)
            used = size / budget_bytes
            log_fn(f"[VTX] encode try {tries}: {kbps:.1f} kbps -> {size} B "
                   f"= {used * 100:.0f}% of {budget_msgs} msgs")
            if size <= budget_bytes and (best is None or size > best[0]):
                best = (size, last_payload, kbps)
            if ACCEPT_LOW <= used <= ACCEPT_HIGH:
                break
            if size <= budget_bytes:
                under = (kbps, size) if under is None or size > under[1] else under
            else:
                over = (kbps, size) if over is None or size < over[1] else over
            if under and over:
                slope = math.log(over[1] / under[1]) / math.log(over[0] / under[0])
                kbps = under[0] * (target_bytes / under[1]) ** (1.0 / slope)
            else:
                kbps *= CORRECTION_AIM / used          # same pass-1 notes, new target
        if best is not None:
            payload, kbps = best[1], best[2]
        else:
            payload = last_payload                     # every try overshot: trim below
        encode_s = clock() - t1

        payload, trimmed = trim_to_budget(payload, budget_bytes)
        if trimmed:
            log_fn(f"[VTX][WARN] still over budget after {tries} tries; trimmed "
                   f"{trimmed} frame(s) off the tail to fit")
        check_payload(payload, raw_bytes_per_msg)
        return {
            "payload": payload, "bytes": len(payload),
            "msgs": -(-len(payload) // int(raw_bytes_per_msg)), "budget_msgs": budget_msgs,
            "used_pct": round(100.0 * len(payload) / budget_bytes, 1),
            "target_kbps": round(kbps, 1), "pass2_tries": tries,
            "frames_trimmed": trimmed, "prescale_s": round(prescale_s, 1),
            "encode_s": round(encode_s, 1),
        }
    finally:
        for path in (ref, out, passlog + "-0.log", passlog + "-0.log.mbtree",
                     passlog + "-0.log.temp", passlog + "-0.log.mbtree.temp"):
            try:
                os.remove(path)
            except OSError:
                pass
