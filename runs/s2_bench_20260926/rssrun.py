#!/usr/bin/env python3
"""Run a runtime script exactly as `python3 <script> args...` would, then print one
[BENCH] line: exit code, wall seconds, peak RSS (self + children), CPU time."""
import os, resource, runpy, sys, time
t0 = time.time()
script = os.path.abspath(sys.argv[1])
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(script))
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code
finally:
    me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    print(f"[BENCH] exit={code} wall_s={time.time() - t0:.1f} maxrss_self_kb={me.ru_maxrss} "
          f"maxrss_children_kb={kids.ru_maxrss} cpu_self_s={me.ru_utime + me.ru_stime:.1f} "
          f"cpu_children_s={kids.ru_utime + kids.ru_stime:.1f}", flush=True)
sys.exit(code)
