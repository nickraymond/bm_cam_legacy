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
