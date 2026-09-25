#!/usr/bin/env python3
"""Time `import rc_progressive_jpeg` from the deployed runtime dir; report PIL load."""
import sys, time
sys.path.insert(0, "/home/pi/BM_Devel_Pi")
t = time.perf_counter()
import rc_progressive_jpeg  # noqa: E402,F401
print(f"[import] {time.perf_counter() - t:.3f}s PIL={'PIL' in sys.modules}")
