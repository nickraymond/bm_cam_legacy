#!/usr/bin/env python3
# filename: test_s27_video_rules.py
# description: Sprint27 — the video config rules refuse exactly what the real v1 video loaders refuse, so a remote set can never leave a video unit unable to start.
"""
Sprint27 SPEC §3.4 / REVIEW_r1 row 1 (+ round 2 amendment A).

Before Sprint27 a `set` of a bad video geometry or clip size was acked ok and
the next start exited 2 before the command daemon ran (rc_progressive_jpeg's
video config load): the unit was unreachable until SSH. The rules
config_validate._rule_video_geometry / _rule_video_send_size close that.

Pins:
  - PARITY: for a sweep of effective configs (two bench configs and registry
    defaults as bases; every video.record.* / video.send.* key at its range
    edges and beyond the loaders' extra limits; odd, tiny and uppercase WxH;
    every framing and sensor mode; crop xor output), the rules report a
    violation IFF the real v1 loaders reject the rendered config
    (config_v2.render_v1_text + config_v2._render_resolves), plus the clip
    fit's upscale refusal computed from the loaders' own resolved sizes.
  - every violation names mode.media (so switching INTO video is refused too);
  - the rules are silent for a still unit;
  - command_v9 answers e:xk to such a set, and supervisor_config.resolve drops a
    stored bad overlay at boot (recovery for a unit holding one);
  - the bench configs of bmcam003/004 (runs/s4a_soak_20260928) pass unchanged.

Run (repo root):
  python3 -m unittest tests.test_s27_video_rules -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import config_validate as V  # noqa: E402
import supervisor_config  # noqa: E402
import video_geometry as VG  # noqa: E402

BENCH = [os.path.join(REPO_ROOT, "runs", "s4a_soak_20260928", "pulled", f"bmcam00{n}_camera_config.yaml")
         for n in (3, 4)]
RULE_NAMES = ("video recording geometry", "video.send.size")


def _bases():
    out = []
    for path in BENCH:
        if os.path.exists(path):
            out.append(("bench:" + os.path.basename(path), dict(config_v2.load_config(path).effective)))
    d = R.defaults()
    d["mode.media"] = "video"
    out.append(("defaults", d))
    return out


def _variants():
    """(label, {path: value}) overrides; each value passes the registry's own
    per-key check, so only the loaders' EXTRA limits are being probed."""
    v = []
    for name in sorted(VG.PRESETS):
        v.append((f"framing={name}", {"video.record.framing": name}))
    for mode in sorted(VG.SENSOR_MODES):
        v.append((f"sensor_mode={mode}", {"video.record.sensor_mode": mode}))
        v.append((f"stills_roi+mode={mode}", {"video.record.framing": "stills_roi_1000p",
                                              "video.record.sensor_mode": mode}))
    for fps in (1, 14, 15, 16, 29, 30):
        v.append((f"record.fps={fps}", {"video.record.fps": fps}))
        v.append((f"720p fps={fps}", {"video.record.framing": "wide_720p", "video.record.fps": fps}))
    crops = ([0, 0, 4608, 2592], [1504, 846, 1600, 900], [1804, 1015, 1000, 562], [0, 0, 16, 16],
             [768, 432, 3072, 1728], [4000, 2000, 608, 592])
    outputs = ("1920x1080", "1280x720", "1000x562", "1001x563", "480x270", "16x16", "1922x1080",
               "1920X1080")
    for crop in crops:
        v.append((f"crop={crop}", {"video.record.crop": crop}))
        for out in outputs:
            v.append((f"crop={crop} out={out}", {"video.record.crop": crop, "video.record.output": out}))
            v.append((f"crop={crop} out={out} framing=null",
                      {"video.record.framing": None, "video.record.crop": crop,
                       "video.record.output": out}))
    for out in outputs:
        v.append((f"output={out}", {"video.record.output": out}))
        v.append((f"output={out} framing=null", {"video.record.framing": None, "video.record.output": out}))
    for size in ("480x270", "481x271", "8x8", "16x16", "14x16", "16X16", "1920x1080", "1922x1080",
                 "1000x562", "1002x562", "1280x720", "2x2", "100000x2"):
        v.append((f"send.size={size}", {"video.send.size": size}))
        v.append((f"stills_roi send.size={size}", {"video.record.framing": "stills_roi_1000p",
                                                    "video.send.size": size}))
    # every other video.* key at its range edges (the loaders re-check ranges)
    for key in R.KEYS:
        if not key.path.startswith(("video.record.", "video.send.")) or not key.range:
            continue
        if key.path in ("video.record.fps",):
            continue
        for edge in key.range:
            v.append((f"{key.path}={edge}", {key.path: edge}))
    for key in R.KEYS:
        if key.path.startswith("video.record.encoder.") and key.enum:
            for e in key.enum:
                v.append((f"{key.path}={e!r}", {key.path: e}))
    return v


def _loaders_accept(values):
    """-> (ok, why): the real v1 loaders on the rendered config, then the clip
    fit's own upscale refusal (rc_video_clip.fit_clip_to_budget: send size >
    recording output) computed from the loaders' resolved sizes."""
    import contextlib
    import io

    import rc_video_tx
    import video_recorder
    with tempfile.TemporaryDirectory() as tmp:
        render = os.path.join(tmp, "render.yaml")
        with open(render, "w", encoding="utf-8") as fh:
            fh.write(config_v2.render_v1_text(values))
        try:
            config_v2._render_resolves(render, values["mode.media"])
            with contextlib.redirect_stdout(io.StringIO()):
                rec = video_recorder.load_video_config(render)
                vtx = rc_video_tx.load_video_tx_config(render)
        except Exception as exc:          # noqa: BLE001 - any refusal counts
            return False, f"{type(exc).__name__}: {exc}"
    (sw, sh), (rw, rh) = vtx["output_wh"], rec["geometry"]["output_wh"]
    if values["mode.media"] == "video" and (sw > rw or sh > rh):
        return False, f"clip fit refuses to upscale {rw}x{rh} -> {sw}x{sh}"
    return True, None


def _rule_hits(values):
    return [x for x in V.validate(values, "effective") if x.message.startswith(RULE_NAMES)
            or "video.send.size" in x.message]


class Parity(unittest.TestCase):
    def test_rules_refuse_exactly_what_the_loaders_refuse(self):
        checked = refused = 0
        for base_label, base in _bases():
            for label, override in _variants():
                values = dict(base)
                values.update(override)
                if any(R.check_value(R.BY_PATH[p], x) for p, x in override.items()):
                    continue            # the per-key check already refuses it
                other = [x for x in V.validate(values, "effective") if x not in _rule_hits(values)]
                if other:
                    continue            # an older rule refuses it first: not this rule's job
                ok, why = _loaders_accept(values)
                hits = _rule_hits(values)
                with self.subTest(base=base_label, case=label):
                    self.assertEqual(not hits, ok,
                                     f"loaders {'accept' if ok else 'refuse: ' + str(why)}; "
                                     f"rules {[h.message for h in hits]}")
                    for h in hits:
                        self.assertIn("mode.media", h.paths)
                checked += 1
                refused += 0 if ok else 1
        self.assertGreater(checked, 200)
        self.assertGreater(refused, 20)          # the sweep really exercises refusals


class Scope(unittest.TestCase):
    def test_still_unit_is_not_judged(self):
        d = R.defaults()
        d.update({"mode.media": "still", "video.send.size": "481x271", "video.record.fps": 30,
                  "video.record.framing": "wide_1080p"})
        self.assertEqual(_rule_hits(d), [])

    def test_bench_configs_pass(self):
        for path in BENCH:
            if not os.path.exists(path):
                self.skipTest(f"{path} not in this checkout")
            values = config_v2.load_config(path).effective
            self.assertEqual(V.validate(values, "strict"), [], path)

    def test_boot_drops_a_stored_bad_overlay(self):
        """A unit that already holds a bad overlay recovers at the next start:
        supervisor_config.resolve drops the named keys (G3) instead of
        rendering a config the loaders would refuse."""
        base = R.defaults()
        base["mode.media"] = "video"
        state = {"schema": "bm_command_state_v2",
                 "overlay": {"video.record.fps": 30, "video.record.framing": "wide_1080p"}}
        try:
            eff = supervisor_config.resolve(base, state)
        except Exception as exc:          # noqa: BLE001
            self.fail(f"resolve raised {exc!r}")
        self.assertTrue(_loaders_accept(eff.values)[0], eff.lines)
        self.assertTrue(eff.dropped, "the bad overlay must be dropped")



class RemoteSet(unittest.TestCase):
    """The unit's own answer: a bad video set is refused e:xk and nothing is
    stored; a good one still goes through (the test_s4_dispatch rig)."""

    def _rig(self):
        from tests.test_s4_dispatch import Rig
        return Rig(self, base_over={"mode.media": "video"})

    def test_bad_video_sets_are_refused_xk(self):
        r = self._rig()
        cases = [({"video.record.fps": 30, "video.record.framing": "wide_1080p"}, "video.record.fps"),
                 ({"video.record.sensor_mode": "1536x864", "video.record.framing": "wide_1080p"},
                  "video.record.sensor_mode"),
                 ({"video.record.output": "1280x720"}, "video.record.output"),
                 ({"video.send.size": "481x271"}, "video.send.size"),
                 ({"video.send.size": "1920x1080"}, "video.send.size")]
        for i, (kv, key) in enumerate(cases):
            r.send({"id": 1_000_100 + i, "c": "set", "kv": kv})
            ack = r.acks()[0]
            with self.subTest(kv=kv):
                self.assertEqual((ack["ok"], ack.get("e")), (0, "xk"), ack)
                self.assertIn(ack.get("k"), list(kv))
        self.assertEqual(r.state.overlay, {})

    def test_good_video_set_is_saved(self):
        r = self._rig()
        r.send({"id": 1_000_200, "c": "set",
                "kv": {"video.record.framing": "wide_720p", "video.send.size": "480x270"}})
        self.assertEqual(r.acks()[0]["ok"], 1)

    def test_switch_into_video_with_bad_values_is_refused(self):
        from tests.test_s4_dispatch import Rig
        r = Rig(self, base_over={"mode.media": "still", "video.send.size": "481x271"})
        r.send({"id": 1_000_300, "c": "set", "kv": {"mode.media": "video"}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("e"), ack.get("k")), (0, "xk", "mode.media"))


if __name__ == "__main__":
    unittest.main()
