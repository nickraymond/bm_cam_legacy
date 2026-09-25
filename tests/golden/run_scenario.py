#!/usr/bin/env python3
# filename: run_scenario.py
# description: Sprint26 S1 — run ONE golden scenario (or settings target) in a fresh interpreter.
"""
Run one golden scenario against a TEMP COPY of the runtime and write its record.

Each run is its own process (module globals such as the shared BM serial handle
or bm_serial's config path are read once per process), and the runtime is copied
into a temp dir first, so nothing a cycle writes (media-key state, sent records,
images, logs) can land in the repo.

Usage (repo root, PyYAML required — see tests/golden/README.md):
  python tests/golden/run_scenario.py wire <scenario> <outdir> [--app-src DIR]
  python tests/golden/run_scenario.py settings <target> <outdir> [--app-src DIR]

  <target> is a repo-relative profile path, or "bmcam003+<state fixture>".

Outputs in <outdir>:
  wire:      trace.txt (every port / frame / side effect, in order), summary.json
  settings:  settings.json
  always:    env.json (python, PyYAML, Pillow versions)
The process exits nonzero on any failure; stdout is the runtime's own log.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import scenarios as S  # noqa: E402
import world as W      # noqa: E402

NATIVE = os.path.join(REPO, "reference_images", "prepared", "P7071008",
                      "synthetic_native_4608x2592.jpg")
H264_VECTOR = os.path.join(REPO, "tests", "vectors", "bm_media_h264", "payload.h264")
BASE_PROFILE = os.path.join(REPO, "device_profiles", "bmcam003", "camera_schedule.yaml")

# Every runtime module the cycles can reach. A name missing from the app under
# test (e.g. a module S1 deletes) is skipped; any other import error is fatal.
APP_MODULES = [
    "bm_frame_decoder", "bm_serial", "spotter_time_sync", "process_image_v2",
    "command_tables", "command_messages", "command_state", "command_bindings",
    "command_help", "command_daemon", "rc_command_hooks", "rc_heal", "rc_media_key",
    "rc_media_id", "rc_transmit", "rc_transmit_phase", "rc_uplink_messages",
    "rc_time_budget", "rc_quality_selector", "rc_jpeg_encoder", "rc_power_halt",
    "network_config", "rc_progressive_jpeg", "rc_video_tx", "rc_video_clip",
    "video_recorder", "video_ring", "video_manifest", "video_geometry",
]
# Modules whose wall clock must stay real (read_spotter_utc loops on time.time()).
REAL_CLOCK_MODULES = {"spotter_time_sync"}


def fail(msg, code=2):
    print(f"[GOLDEN][ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def env_info():
    import PIL
    import yaml
    return {"python": sys.version.split()[0], "pyyaml": yaml.__version__,
            "pillow": PIL.__version__}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def git(*args):
    import subprocess
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True).stdout


def make_tmp(app_src, label, app_ref=None):
    """Temp run dir with the runtime under app/: the working tree's BM_Devel_Pi
    (or --app-src), or BM_Devel_Pi as committed at app_ref (git archive)."""
    tmp = tempfile.mkdtemp(prefix=f"golden_{label}_")
    if app_ref:
        import io as _io
        import tarfile
        with tarfile.open(fileobj=_io.BytesIO(git("archive", app_ref, "BM_Devel_Pi"))) as tar:
            tar.extractall(tmp, filter="data")
        os.rename(os.path.join(tmp, "BM_Devel_Pi"), os.path.join(tmp, "app"))
    else:
        shutil.copytree(app_src, os.path.join(tmp, "app"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "images",
                                                      "videos", "buffer", "cron_logs", "sent"))
    for sub in ("state", "images", "videos", "logs", "old"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    halt_stub = os.path.join(tmp, "tuned_halt.sh")      # never executed: subprocess is faked
    with open(halt_stub, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(halt_stub, 0o755)
    return tmp


def build_profile(scenario, tmp):
    """The scenario's profile: BASE_PROFILE (or `profile`, read at `profile_ref` when
    given) with its exact-line edits applied and `append` added."""
    source = scenario.get("profile", os.path.relpath(BASE_PROFILE, REPO))
    if scenario.get("profile_ref"):
        text = git("show", f"{scenario['profile_ref']}:{source}").decode("utf-8")
    else:
        with open(os.path.join(REPO, source), "r", encoding="utf-8") as fh:
            text = fh.read()
    lines = text.split("\n")
    for after, old, new in scenario.get("edits", []):
        start = 0
        if after is not None:
            try:
                start = lines.index(after)
            except ValueError:
                fail(f"profile edit anchor {after!r} not found in {source}")
        try:
            idx = lines.index(old, start)
        except ValueError:
            fail(f"profile edit {old!r} (after {after!r}) not found in {source}")
        lines[idx] = new
    text = "\n".join(lines) + scenario.get("append", "")
    return text.replace("{TMP}", tmp)


def set_env(tmp, config_path):
    os.environ.update({
        "BM_CAMERA_CONFIG_PATH": config_path,
        "BM_COMMAND_STATE_PATH": os.path.join(tmp, "state", "bm_command_state.json"),
        "BM_CAM_SOFTWARE_SHA": "golden0",
        "BMCAM_REFERENCE_ROOT": REPO,
        "TZ": "UTC",
    })
    time.tzset()


def import_app(tmp):
    sys.path.insert(0, os.path.join(tmp, "app"))
    mods = {}
    for name in APP_MODULES:
        try:
            mods[name] = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
    return mods


def patch_app(mods, tmp):
    """Fixed identity/storage, frozen wall clock, tmp paths, no retry sleeps."""
    modlist = list(mods.values())
    W.freeze_wall_clock(modlist, keep_real_time=REAL_CLOCK_MODULES)
    for m in modlist:
        for attr, value in (("IMAGE_DIRECTORY", os.path.join(tmp, "images")),
                            ("BUFFER_DIRECTORY", os.path.join(tmp, "buffer")),
                            ("LOG_FILE", os.path.join(tmp, "logs", "camera_log.csv")),
                            ("CAPTURE_HELPER_RETRY_DELAY_SECONDS", 0)):
            if hasattr(m, attr):
                setattr(m, attr, value)
    originals = {getattr(m, "collect_storage_health") for m in modlist
                 if callable(getattr(m, "collect_storage_health", None))}
    for original in originals:
        W.patch_everywhere(modlist, original, lambda: dict(W.FIXED_STORAGE))


def seed(scenario, mods, tmp):
    """Pre-existing unit state the scenario starts from. Returns the old media key."""
    wanted = scenario.get("seed", [])
    key = None
    state_path = os.path.join(tmp, "state", "bm_command_state.json")
    if "old_media" in wanted:
        mk = mods["rc_media_key"]
        old_utc = dt.datetime(2026, 9, 23, 15, 0, tzinfo=dt.timezone.utc)
        key, _why = mk.allocate_key(old_utc, "spotter", state_path=os.path.join(tmp, "old", "key.txt"))
        payload = bytes((i * 37 + 11) % 256 for i in range(3000))
        stem = "2026-09-23T15:00:00Z_image_compressed"
        path = os.path.join(tmp, "old", stem + ".jpg")
        with open(path, "wb") as fh:
            fh.write(payload)
        import base64
        msgs = -(-len(base64.b64encode(payload)) // 384)
        mk.write_sent_record(mk.DEFAULT_SENT_DIR, stem, key=key, fmt="pjpg",
                             filename=stem + ".jpg", chunk_b64_chars=384, msgs=msgs,
                             sha256=hashlib.sha256(payload).hexdigest(), payload_path=path,
                             now=old_utc + dt.timedelta(seconds=5))
    if "pending_heal" in wanted or "pending_trg" in wanted:
        state = mods["command_state"].CommandState(path=state_path)
        if "pending_heal" in wanted:
            state.record(100001, "rsd", {"h": [[key, [1, 3]]]})
        if "pending_trg" in wanted:
            state.record(600, "trg", 2)
    return key


def substitute_key(rules, key):
    for rule in rules:
        rule["payload"] = json.loads(json.dumps(rule["payload"]).replace("{KEY}", key or "nokey"))


# ---------------------------------------------------------------------------
# Video fakes (function level; the recorder argv is not traced yet)
# ---------------------------------------------------------------------------

def video_fakes(mods):
    vr, clip = mods["video_recorder"], mods["rc_video_clip"]
    with open(H264_VECTOR, "rb") as fh:
        payload = fh.read()

    def record(settings, vcfg, video_dir, *, encoder_binary, ffmpeg_binary, controls=None, **_):
        now = W.FrozenDateTime.now(dt.timezone.utc)
        geo = vcfg["geometry"]
        basename = vr.clip_basename(now, geo["output_wh"], vcfg["fps"])
        mp4 = os.path.join(video_dir, basename + ".mp4")
        with open(mp4, "wb") as fh:
            fh.write(payload)
        W.WORLD.trace.add("VREC", json.dumps({
            "record_s": round(float(vcfg["clip_minutes"]) * 60.0, 3), "fps": vcfg["fps"],
            "bitrate_mbps": vcfg["bitrate_mbps"], "geometry": geo, "controls": controls,
            "encoder": encoder_binary, "ffmpeg": ffmpeg_binary}, sort_keys=True, default=str))
        return {"ok": True, "stage": "done", "basename": basename, "mp4": mp4, "bytes": len(payload)}

    def fit(mp4, work_dir, *, width, height, fps, duration_s, budget_msgs, raw_bytes_per_msg,
            source_wh, preset, ffmpeg_binary, clock=None, **_):
        msgs = -(-len(payload) // int(raw_bytes_per_msg))
        W.WORLD.trace.add("VFIT", json.dumps({
            "width": width, "height": height, "fps": fps, "duration_s": duration_s,
            "budget_msgs": budget_msgs, "raw_bytes_per_msg": raw_bytes_per_msg,
            "source_wh": source_wh, "preset": preset}, sort_keys=True, default=str))
        budget_bytes = max(1, int(budget_msgs) * int(raw_bytes_per_msg))
        return {"payload": payload, "bytes": len(payload), "msgs": msgs, "budget_msgs": budget_msgs,
                "used_pct": round(100.0 * len(payload) / budget_bytes, 1),
                "target_kbps": round(len(payload) * 8 / 1000.0 / float(duration_s), 1),
                "pass2_tries": 1, "frames": int(fps * duration_s), "duration_s": float(duration_s),
                "keyframe_end": clip.keyframe_end_offset(payload), "frames_trimmed": 0,
                "prescale_s": 0.0, "encode_s": 0.0}

    def room(video_dir, storage):
        return {"paused": False, "used_pct": 29.0, "free_gb": 20.0, "deleted": 0}

    return record, fit, room


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def jsonable(obj):
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, bytes):
        return {"bytes": len(obj), "sha256": hashlib.sha256(obj).hexdigest()}
    return str(obj)


def dump(obj, trace):
    return trace.norm(json.dumps(obj, indent=1, sort_keys=True, default=jsonable)) + "\n"


def file_listing(tmp):
    """Every file the run left behind: size + sha256 of its content with the temp
    dir path normalised (config, sidecars and sent records embed that path)."""
    tmp_roots = sorted({tmp.encode(), os.path.realpath(tmp).encode()}, key=len, reverse=True)
    out = {}
    for root, dirs, files in os.walk(tmp):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, tmp)
            if rel.startswith("app" + os.sep) and name.endswith((".py", ".sh", ".md", ".yaml")):
                continue                      # the runtime's own files
            with open(path, "rb") as fh:
                data = fh.read()
            for tmp_root in tmp_roots:
                data = data.replace(tmp_root, b"{TMP}")
            out[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()[:16]}
    return out


def read_json_files(tmp, suffix):
    out = {}
    for root, _dirs, files in os.walk(tmp):
        for name in sorted(files):
            if name.endswith(suffix):
                with open(os.path.join(root, name), "r", encoding="utf-8") as fh:
                    out[os.path.relpath(os.path.join(root, name), tmp)] = json.load(fh)
    return out


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_wire(name, outdir, app_src):
    if name not in S.SCENARIOS:
        fail(f"unknown scenario {name!r}")
    sc = S.SCENARIOS[name]
    tmp = make_tmp(app_src, name, sc.get("app_ref"))
    config_path = os.path.join(tmp, "camera_schedule.yaml")
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(build_profile(sc, tmp))
    set_env(tmp, config_path)

    W.install_process_fakes()
    sys.path.insert(0, os.path.join(tmp, "app"))
    import bm_frame_decoder
    W.WORLD.setup(tmp, dt.datetime.fromisoformat(sc["utc"]), NATIVE, bm_frame_decoder,
                  rules=sc.get("rules", ()), cam_failures=sc.get("cam_failures", 0))
    mods = import_app(tmp)
    patch_app(mods, tmp)
    key = seed(sc, mods, tmp)
    substitute_key(W.WORLD.rules, key)

    rc, vtx = mods["rc_progressive_jpeg"], mods.get("rc_video_tx")
    captured = {}
    clock = W.WORLD.clock
    orig_cycle = rc.run_cycle

    def cycle(*a, **k):
        captured["cycle"] = orig_cycle(*a, **k)
        return captured["cycle"]

    rc.run_cycle = cycle
    if vtx is not None:                      # absent in older runtimes (e.g. main)
        orig_vtx = vtx.run_video_tx_cycle

        def video_cycle(*a, **k):
            captured["cycle"] = orig_vtx(*a, **k)
            return captured["cycle"]

        vtx.run_video_tx_cycle = video_cycle
        record, fit, room = video_fakes(mods)
        orig_vtx.__kwdefaults__.update({
            "sleep_fn": clock.sleep, "clock": clock, "record_fn": record, "fit_fn": fit,
            "ensure_room_fn": room, "encoder_binary": "/usr/bin/rpicam-vid",
            "ffmpeg_binary": "/usr/bin/ffmpeg",
            "now_fn": lambda: W.FrozenDateTime.now(dt.timezone.utc)})

    argv = ["--config-path", config_path, "--transmit",
            "--output-dir", os.path.join(tmp, "images")] + list(sc.get("argv", []))
    code = rc.main(argv, sleep_fn=clock.sleep, clock=clock)
    W.WORLD.wait_idle()

    state_path = os.path.join(tmp, "state", "bm_command_state.json")
    state = None
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    summary = {
        "scenario": name, "notes": sc.get("notes"), "exit_code": code,
        "unfired_rules": [r["payload"] for r in W.WORLD.unfired()],
        "cycle": captured.get("cycle"), "state_file": state,
        "sidecars": read_json_files(tmp, ".capture_metadata.json"),
        "sent_records": read_json_files(tmp, ".sent.json"),
        "files": file_listing(tmp),
        "fake_clock_elapsed_s": round(clock.elapsed(), 3),
    }
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "trace.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(W.WORLD.trace.lines) + "\n")
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as fh:
        fh.write(dump(summary, W.WORLD.trace))
    shutil.rmtree(tmp, ignore_errors=True)
    if summary["unfired_rules"]:
        fail(f"scripted commands never delivered: {summary['unfired_rules']}", 5)


def run_settings(target, outdir, app_src):
    """Resolved config for one profile (optionally under a v1 command-state fixture)."""
    fixture = None
    profile = target
    if "+" in target:
        unit, fixture = target.split("+", 1)
        profile = f"device_profiles/{unit}/camera_schedule.yaml"
        if fixture not in S.STATE_FIXTURES:
            fail(f"unknown state fixture {fixture!r}")
    tmp = make_tmp(app_src, "settings")
    with open(os.path.join(REPO, profile), "r", encoding="utf-8") as fh:
        text = fh.read()
    state_path = os.path.join(tmp, "state", "bm_command_state.json")
    text = text.replace('"/home/pi/BM_Devel_Pi/bm_command_state.json"', f'"{state_path}"')
    config_path = os.path.join(tmp, "camera_schedule.yaml")
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    set_env(tmp, config_path)
    W.install_process_fakes()
    sys.path.insert(0, os.path.join(tmp, "app"))
    import bm_frame_decoder
    W.WORLD.setup(tmp, dt.datetime(2026, 9, 24, 15, tzinfo=dt.timezone.utc), NATIVE, bm_frame_decoder)
    mods = import_app(tmp)
    patch_app(mods, tmp)
    rc = mods["rc_progressive_jpeg"]

    if fixture:
        state = mods["command_state"].CommandState(path=state_path)
        for cid, cmd, value in S.STATE_FIXTURES[fixture]:
            state.record(cid, cmd, value)

    def attempt(fn):
        try:
            return fn()
        except Exception as exc:        # a loader error IS a behaviour to pin
            return {"error": f"{type(exc).__name__}: {exc}"}

    out = {"target": target}
    out["resolved"] = attempt(lambda: rc.resolve_rc_settings(config_path))
    out["camera_controls_island"] = attempt(lambda: rc._load_camera_controls_island(config_path))
    out["bm_commands"] = attempt(lambda: mods["command_daemon"].load_bm_commands_config(config_path))
    if fixture:
        def overlay():
            settings = rc.resolve_rc_settings(config_path)
            state = mods["command_state"].CommandState(path=state_path)
            with contextlib.redirect_stdout(io.StringIO()):
                return rc._apply_command_overlay(settings, state)
        out["overlaid"] = attempt(overlay)
    out["video"] = attempt(lambda: mods["video_recorder"].load_video_config(config_path))
    out["video_tx"] = attempt(lambda: mods["rc_video_tx"].load_video_tx_config(config_path))
    out["media_key"] = attempt(lambda: mods["rc_media_key"].load_media_key_config(config_path))
    out["transmit_phase"] = attempt(
        lambda: mods["rc_transmit_phase"].load_transmit_phase_config(config_path))
    out["network"] = attempt(lambda: mods["network_config"].load_network_config(config_path))
    bms = mods["bm_serial"]
    out["bm_serial"] = attempt(lambda: bms.load_bm_serial_config(config_path))
    out["network_type"] = attempt(lambda: bms.load_network_type_from_config(config_path).hex())
    out["uart"] = attempt(lambda: list(bms.load_uart_config(config_path)))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out["print_config_exit"] = attempt(lambda: rc.main(["--config-path", config_path,
                                                            "--print-config"]))
    out["print_config"] = buf.getvalue().splitlines()
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "settings.json"), "w", encoding="utf-8") as fh:
        fh.write(dump(out, W.WORLD.trace))
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("mode", choices=("wire", "settings"))
    ap.add_argument("target")
    ap.add_argument("outdir")
    ap.add_argument("--app-src", default=os.path.join(REPO, "BM_Devel_Pi"))
    args = ap.parse_args()
    try:
        info = env_info()
    except ImportError as exc:
        fail(f"{exc}. Golden vectors must be recorded and checked WITH PyYAML "
             f"(see tests/golden/README.md: .venv-dev + requirements-dev.txt)", 3)
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "env.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1, sort_keys=True)
    if args.mode == "wire":
        run_wire(args.target, args.outdir, args.app_src)
    else:
        run_settings(args.target, args.outdir, args.app_src)


if __name__ == "__main__":
    main()
