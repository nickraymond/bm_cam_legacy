#!/usr/bin/env python3
# filename: test_boot_imports.py
# description: Sprint26 S1 — boot-time imports stay lean: a video boot never loads PIL.
"""
Pins the Sprint26 S1 memory/boot-time cleanup (DESIGN_supervisor.md §7 C2).

The runtime is imported on every boot, stills or video. PIL is heavy on a Pi
Zero 2W and only the stills encode needs it, so:
  - importing the runtime modules loads no PIL
  - `--print-config` (stills or video) loads no PIL
  - rc_jpeg_encoder.prepare_source DOES load it (the stills path still works)

Each check runs in a fresh interpreter (sys.modules must start clean).

Run (repo root):
  python3 -m unittest tests.test_boot_imports -v
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO_ROOT, "BM_Devel_Pi")
NATIVE = os.path.join(REPO_ROOT, "reference_images", "prepared", "P7071008",
                      "synthetic_native_4608x2592.jpg")

VIDEO_YAML = textwrap.dedent("""\
    capture_mode: "video"
    enforce_time_window: false
    video_tx:
      enabled: true
    """)
STILLS_YAML = 'capture_mode: "progressive_jpeg"\nenforce_time_window: false\n'


def run_py(code):
    """Run `code` in a fresh interpreter with the app on sys.path; return stdout."""
    prelude = f"import sys\nsys.path.insert(0, {APP!r})\n"
    proc = subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"child failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc.stdout


class TestBootImports(unittest.TestCase):
    def yaml(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_importing_the_runtime_loads_no_pil(self):
        out = run_py("""
            import rc_progressive_jpeg, rc_video_tx, video_recorder, rc_command_hooks, rc_capture
            print("PIL" in sys.modules)
        """)
        self.assertEqual(out.strip().splitlines()[-1], "False")

    def test_print_config_loads_no_pil(self):
        for label, text in (("video", VIDEO_YAML), ("stills", STILLS_YAML)):
            path = self.yaml(text)
            out = run_py(f"""
                import io, contextlib
                import rc_progressive_jpeg as rc
                with contextlib.redirect_stdout(io.StringIO()):
                    code = rc.main(["--config-path", {path!r}, "--print-config"])
                print(code, "PIL" in sys.modules)
            """)
            self.assertEqual(out.strip().splitlines()[-1], "0 False", label)

    def test_stills_encode_still_loads_pil(self):
        out = run_py(f"""
            import rc_jpeg_encoder as enc
            src = enc.prepare_source({NATIVE!r}, (1504, 846, 1600, 900), 1000)
            print(src.size, "PIL" in sys.modules)
        """)
        self.assertEqual(out.strip().splitlines()[-1], "(1000, 562) True")


if __name__ == "__main__":
    unittest.main()
