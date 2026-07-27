#!/usr/bin/env python3
# filename: rc_media_id.py
# description: Sprint10 soak — per-image media group id (gid) for chunk attribution.
"""
Sprint10 — media group id for image-chunk attribution (Nick, 2026-07-27).

Problem: image chunks (`<I{i}>...`) carry no image identity. The Sofar
cellular path is not FIFO and drops silently, so backend parsers must
attribute chunks to images by arrival order between START markers —
ambiguous under back-to-back cycles and unfixable for stragglers.

Fix: each transmitted image gets a 3-char base36 rolling gid. Chunks
become `<I{gid}.{i}>...`; the START message gains a `gid` field binding
gid -> filename (END needs nothing — it correlates by filename, and the
P4 "END byte-identical" decision stands). Legacy format remains the
default: the whole feature is OFF unless the YAML opts in:

    media_gid:
      enabled: true

Counter state: one small text file (default alongside bm_command_state),
atomic tmp+replace write, wraps at 36^3 = 46656 (~5 years of hourly
images; wrap-collision horizon is far beyond any recompile window).

Overhead: ~4 B/chunk + ~10 B in START ≈ 1 % of a 384-char chunk message.
"""

import os

GID_WIDTH = 3
GID_MOD = 36 ** GID_WIDTH  # 46656
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

DEFAULT_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bm_media_gid.txt")


def encode_gid(n):
    """int -> zero-padded 3-char base36 (lowercase)."""
    n = int(n) % GID_MOD
    out = []
    for _ in range(GID_WIDTH):
        n, r = divmod(n, 36)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def next_gid(state_path=DEFAULT_STATE_PATH):
    """Return the next gid and persist the counter (atomic replace).

    Corrupt/missing state restarts at 0 — a gid is an attribution tag,
    not a security token; losing the counter costs nothing but a jump.
    """
    try:
        with open(state_path, "r", encoding="ascii") as f:
            n = (int(f.read().strip()) + 1) % GID_MOD
    except (OSError, ValueError):
        n = 0
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(str(n))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, state_path)
    return encode_gid(n)


def chunk_prefix(i, gid=None):
    """Wire prefix for chunk i: legacy `<I7>` or gid form `<Iab2.7>`."""
    if gid is None:
        return f"<I{i}>"
    return f"<I{gid}.{i}>"


def load_media_gid_config(config_path):
    """Read the `media_gid:` island from camera_schedule.yaml.

    Same tolerant line-based convention as the bm_commands island loader
    (dev Macs may lack PyYAML; the Pi has it). Absent island == disabled
    == wire byte-identical to pre-Sprint10.
    """
    enabled = False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith(" "):
                    in_island = line.strip() == "media_gid:"
                    continue
                if in_island and line.strip().startswith("enabled:"):
                    val = line.split(":", 1)[1].strip().lower()
                    enabled = val in ("true", "1", "yes", "on")
    except OSError:
        pass
    return {"enabled": enabled}
