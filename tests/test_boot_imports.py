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


def import_closure(entry="rc_progressive_jpeg"):
    """Every BM_Devel_Pi module reachable from `entry` through import statements,
    including lazy imports inside functions (static AST walk)."""
    import ast
    local = {n[:-3] for n in os.listdir(APP) if n.endswith(".py")}
    seen, stack = set(), [entry]
    while stack:
        mod = stack.pop()
        if mod in seen or mod not in local:
            continue
        seen.add(mod)
        with open(os.path.join(APP, mod + ".py"), "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stack += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                stack.append(node.module.split(".")[0])
    return seen


def manifest_paths():
    """Destination-side repo paths in tools/rc_runtime_manifest.txt (first token
    of each non-comment line; inline comments and `-> dest` renames ignored)."""
    with open(os.path.join(REPO_ROOT, "tools", "rc_runtime_manifest.txt")) as fh:
        return {line.split()[0] for line in fh
                if line.strip() and not line.lstrip().startswith("#")}


class TestManifestShipsTheImportClosure(unittest.TestCase):
    """A field update copies ONLY the manifest. A runtime module missing from it
    installs a unit that cannot import (rc_media_key after Sprint25 S4). This
    checks the whole static import closure of the entry script, lazy imports
    included, so a module added in any stage is caught before a deploy."""

    def test_every_reachable_module_is_shipped(self):
        closure = import_closure()
        self.assertIn("rc_video_tx", closure)          # lazy import is followed
        missing = sorted(f"BM_Devel_Pi/{m}.py" for m in closure
                         if f"BM_Devel_Pi/{m}.py" not in manifest_paths())
        self.assertEqual(missing, [], f"reachable but not in tools/rc_runtime_manifest.txt: {missing}")


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
