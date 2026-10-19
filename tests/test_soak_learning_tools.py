"""Offline tests for the soak-learning tools (c_soak_learning): ledger helpers, cause rules and Q5 statistics.

No network, no hardware, stdlib only. Run: python3 -m pytest tests/test_soak_learning_tools.py -q
"""
import math
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import soak_learning_ledger as L  # noqa: E402
import soak_learning_q5 as Q  # noqa: E402


def test_count_ranges_and_runs():
    assert L.count_ranges("7-16,34,69-78,90,133") == 23
    assert L.count_ranges("85") == 1
    assert L.runs_of_missing([3, 4, 5, 9, 11, 12]) == [[3, 4, 5], [9], [11, 12]]
    assert L._ranges([3, 4, 5, 9]) == "3-5,9"


def test_key_time_matches_start_filename():
    # 0ersh1 = START filename 2026-10-15T04:00:37Z (SPOT-33507C sample, raw/sofar)
    assert L.iso(L.key_time("0ersh1")) == "2026-10-15T04:00:37Z"


def test_parse_sofar_groups_chunks_and_M():
    msgs = [
        {"ts": L.T("2026-10-15T04:00:37Z"), "node": "n1", "file": "f", "text": "<WS v=1 a=cap hn=bmcam004>"},
        {"ts": L.T("2026-10-15T04:01:17Z"), "node": "n1", "file": "f",
         "text": "<START IMG> filename: 2026-10-15T04:00:37Z_image_compressed.nrjxl, length: 3, key=0ersh1, fmt=nrjxl, hn=bmcam004"},
        {"ts": L.T("2026-10-15T04:01:18Z"), "node": "n1", "file": "f", "text": "<I0ersh1.0/3>AAAA"},
        {"ts": L.T("2026-10-15T04:01:19Z"), "node": "n1", "file": "f", "text": "<I0ersh1.2/3>AAAA"},
        {"ts": L.T("2026-10-15T04:01:20Z"), "node": "n2", "file": "f", "text": "<I0zzzzz.0/9>AAAA"},  # other node
    ]
    node, keys, ws, hl, other = L.parse_sofar(msgs, "bmcam004")
    assert node == "n1" and set(keys) == {"0ersh1"} and len(ws) == 1
    k = keys["0ersh1"]
    assert k["M"].most_common(1)[0][0] == 3 and sorted(k["chunks"]) == [0, 2]
    assert L.iso(k["fn_time"]) == "2026-10-15T04:00:37Z" and k["start_len"] == 3


def _row(**kw):
    base = {"in_bar": True, "complete_le_24h": False, "kind": "production", "capture_utc": "2026-10-16T08:00:30Z",
            "first_send_lost": 33, "media_id": 58625, "first_send_stall_runs": 0, "accepted_not_at_sofar_first": 0,
            "_asks": [], "_hls": [], "_heal": []}
    base.update(kw)
    return base


CFG = {"spot": "SPOT-31593C", "run_end": L.T("2026-10-16T19:15:43Z")}


def test_classify_never_asked_is_c2():
    cap = L.T("2026-10-16T08:00:30Z")
    wakes = [cap.replace(minute=0, second=30) + timedelta(hours=h) for h in range(1, 12)]
    out = L.classify(_row(), CFG, wakes, set(), lambda t: 23)
    assert out["cause_primary"] == "C2" and "no ask" in out["cause_note"]


def test_classify_pull_truncated_is_c7():
    r = _row(capture_utc="2026-10-16T19:00:29Z", first_send_lost=12)
    out = L.classify(r, CFG, [], set(), lambda t: 23)
    assert out["cause_primary"] == "C7" and "deadline after the pull" in out["cause_note"]


def test_classify_unanswered_ask_followed_by_wake_is_c3():
    cap = L.T("2026-10-15T01:00:30Z")
    wakes = [cap.replace(minute=0, second=30) + timedelta(hours=h) for h in range(1, 25)]   # no missed slots
    ask = {"id": 100365, "sent": cap + timedelta(hours=2), "n": 33,
           "row": {"dead_zone": "yes", "dead_zone_why": "rx after last submit", "spotter_rx": "x"}}
    r = _row(capture_utc="2026-10-15T01:00:30Z", _asks=[ask])
    out = L.classify(r, CFG, wakes, set(), lambda t: 20)
    assert out["cause_primary"] == "C3"


def test_classify_not_in_bar_and_complete():
    assert L.classify(_row(in_bar=False), CFG, [], set(), lambda t: 0)["cause_primary"] == ""
    assert L.classify(_row(complete_le_24h=True), CFG, [], set(), lambda t: 0)["cause_primary"] == ""


def test_bootstrap_is_deterministic_and_handles_zero_before():
    b = [(10, 1000, 6), (20, 1000, 6), (30, 1000, 7)]
    a = [(5, 1000, 6), (10, 1000, 6), (15, 1000, 7)]
    lo1, _ = Q.bootstrap(b, a)
    lo2, _ = Q.bootstrap(b, a)
    assert lo1 == lo2 and -1 < lo1 < 0.5
    lo0, R = Q.bootstrap([(0, 1000, 6)] * 3, a)
    assert lo0 == -math.inf and R[0] == -math.inf


def test_poisson_rr_lower_brackets_observed():
    lo = Q.poisson_rr_lower(100, 10000, 50, 10000)   # observed R = 0.5
    assert 0.30 < lo < 0.5


def test_nb_fit_poisson_limit_recovers_ratio():
    ws = ([{"side": "before", "lost": 10, "M": 100}] * 12) + ([{"side": "after", "lost": 5, "M": 100}] * 12)
    nb = Q.nb_fit(ws)
    assert abs(nb["R_hat"] - 0.5) < 0.01
    assert nb["R_lower"] < nb["R_hat"]
    assert nb["alpha"] < 1e-3          # no extra-Poisson spread in this data


def test_load_cycle_logs_uses_key_line_not_file_name(tmp_path):
    d = tmp_path / "bmcam004"
    d.mkdir()
    (d / "rc_cycle_20261015T160835Z.log").write_text(          # stale Pi clock in the file name
        "[KEY] media key 0esrj9 from Spotter UTC 2026-10-15T16:37:57.585000+00:00 (spotter)\n"
        "[KEY] sent record: /home/pi/BM_Devel_Pi/sent/x.sent.json (190 msgs, chunks <I0esrj9.n/190>)\n")
    (d / "rc_cycle_20261015T160840Z.log").write_text(
        "[KEY] media key 0essl1 from Spotter UTC 2026-10-15T17:00:37.347000+00:00 (spotter)\n"
        "[RC] cycle end: elapsed=470.4s of 480s; halt=halt_initiated\n")
    (d / "rc_cycle_20261016T195906Z.log").write_text("[RC-CRON] running RC capture/transmit cycle\n")  # held, no capture
    recs = L.load_cycle_logs(tmp_path, "bmcam004")
    by = {r["key"]: r for r in recs}
    assert L.iso(by["0esrj9"]["spotter_utc"]) == "2026-10-15T16:37:57Z" and by["0esrj9"]["msgs"] == 190
    assert by["0esrj9"]["cycle_end"] is False and by["0essl1"]["cycle_end"] is True
    assert by[None]["file"].endswith("rc_cycle_20261016T195906Z.log")


def test_classify_without_evidence_is_insufficient_not_c8():
    r = _row(kind="production", first_send_lost=0, media_id="", evidence="sofar:none",
             spotter_not_accepted_first="", capture_utc="2026-10-15T01:00:30Z")
    cap = L.T("2026-10-15T01:00:30Z")
    wakes = [cap.replace(minute=0, second=30) + timedelta(hours=h) for h in range(1, 25)]
    out = L.classify(r, CFG, wakes, set(), lambda t: 0)
    assert out["cause_primary"] == "insufficient evidence"


def _q5_run_dir(tmp_path, before_lost, after_lost):
    import csv
    t_off = L.T("2026-10-15T16:47:38Z")
    rows = []
    for h in range(-18, 19):
        if h == 0:
            continue
        cap = t_off.replace(minute=0, second=30) + timedelta(hours=h if h < 0 else h)
        rows.append({"unit": "BMCAM_003", "media_key": f"k{h + 50:03d}", "capture_utc": L.iso(cap), "kind": "production",
                     "chunks_expected": 100, "first_send_lost": before_lost if cap < t_off else after_lost,
                     "wake_qfull": "", "wake_hdr_adds": "", "first_send_stall_runs": 0})
    with (tmp_path / "ledger.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (tmp_path / "bus_windows.csv").write_text("unit,bus_on\n")
    (tmp_path / "wakes.csv").write_text("unit,slot_utc,wake_seen\n")
    return {"spot": "SPOT-31593C", "t_off": t_off, "before_start": t_off - timedelta(hours=18, minutes=30), "exclusions": []}


def test_q5_not_testable_when_exclusions_cover_window(tmp_path):
    cfg = _q5_run_dir(tmp_path, 10, 5)
    cfg["exclusions"] = [("THERMAL_FAULT test", cfg["before_start"] - timedelta(hours=1), cfg["t_off"] + timedelta(days=2))]
    res, _ = Q.analyse("BMCAM_003", cfg, tmp_path)
    assert res["verdict"].startswith("not testable") and "100%" in res["verdict"]
    assert res["stats"]["basis"].startswith("SENSITIVITY")


def test_q5_not_testable_when_before_rate_zero(tmp_path):
    cfg = _q5_run_dir(tmp_path, 0, 5)
    res, _ = Q.analyse("BMCAM_003", cfg, tmp_path)
    assert "rate_before = 0" in res["verdict"]


def test_q5_nonscheduled_wake_excludes_next_20_min(tmp_path):
    import csv
    cfg = _q5_run_dir(tmp_path, 10, 5)
    rows = list(csv.DictReader(open(tmp_path / "ledger.csv")))
    rows.append({**rows[0], "media_key": "stub01", "capture_utc": "2026-10-15T16:48:23Z", "kind": "stub"})
    with (tmp_path / "ledger.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    ex = Q.rule_exclusions(tmp_path, "BMCAM_003", cfg)
    assert any(a == L.T("2026-10-15T16:48:23Z") and b == L.T("2026-10-15T17:08:23Z") for _, a, b in ex)
    wakes, _ = Q.load_wakes(tmp_path, "BMCAM_003", dict(cfg, exclusions=ex))
    w17 = [w for w in wakes if w["capture_utc"] == "2026-10-15T17:00:30Z"][0]
    assert not w17["used"] and "non-scheduled" in w17["reason"]
