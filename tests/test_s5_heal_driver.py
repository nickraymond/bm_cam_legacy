"""bm_heal_driver: a `still_arriving` answer must not burn a heal wake (nvd BUGS.md B19).

Drives Driver.on_wake with a fake backend (no network, no console); publish offsets are
zeroed so nothing sleeps. Run (repo root):  python3 -m pytest tests/test_s5_heal_driver.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import bm_heal_driver as D  # noqa: E402


class FakeBackend:
    """status per media id; `missing` -> 200 with ranges, otherwise 409 with that reason."""

    def __init__(self, status):
        self.status = status
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/admin/ingest/media/"):
            mid = int(path.split("/")[4])
            reason = self.status[mid]
            if reason.startswith("missing"):
                return 200, {"ranges": reason.split(" ", 1)[1]}
            return 409, {"detail": {"media_id": mid, "reason": reason}}
        if path.endswith("heal-candidates?hours=24"):
            return 200, {"candidates": [], "skipped": []}
        raise AssertionError(path)


@pytest.fixture
def driver(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "PUBLISH_OFFSETS_S", (0,))
    d = D.Driver(str(tmp_path), {"SPOT-33507C": "BMCAM_003"}, FakeBackend({}))
    os.makedirs(tmp_path / "SPOT-33507C")
    return d


def events(d):
    with open(os.path.join(d.dir, "events.jsonl")) as fh:
        return [json.loads(line)["event"] for line in fh]


def outstanding(d, wakes=1, waits=0):
    cmd = {"command_id": 100001, "console_line": "bm pub bmcam/cmd {} 1 1", "media_ids": [53553],
           "wakes": wakes, "created_utc": "x"}
    if waits:
        cmd["waits"] = waits
    d.state["BMCAM_003"] = cmd
    return cmd


def test_still_arriving_waits_without_publishing_or_counting(driver):
    driver.backend = FakeBackend({53553: "still_arriving"})
    outstanding(driver, wakes=1)
    driver.on_wake("SPOT-33507C", 0.0)
    cmd = driver.state["BMCAM_003"]
    assert events(driver) == ["wake", "waiting"]
    assert cmd["wakes"] == 1 and cmd["waits"] == 1          # wake not counted, command kept
    assert not os.path.exists(os.path.join(driver.log_root, "SPOT-33507C", "cmd.txt"))


def test_wait_is_bounded_then_publishes(driver):
    driver.backend = FakeBackend({53553: "still_arriving"})
    outstanding(driver, wakes=1, waits=D.MAX_WAIT_WAKES)
    driver.on_wake("SPOT-33507C", 0.0)
    assert events(driver) == ["wake", "publish"]
    assert driver.state["BMCAM_003"]["wakes"] == 2


def test_missing_still_publishes(driver):
    driver.backend = FakeBackend({53553: "missing 89-123"})
    outstanding(driver, wakes=1)
    driver.on_wake("SPOT-33507C", 0.0)
    assert events(driver) == ["wake", "publish"]
    assert driver.state["BMCAM_003"]["wakes"] == 2


def test_mixed_still_arriving_and_missing_publishes(driver):
    driver.backend = FakeBackend({53553: "still_arriving", 53619: "missing 110-123"})
    cmd = outstanding(driver, wakes=1)
    cmd["media_ids"] = [53553, 53619]
    driver.on_wake("SPOT-33507C", 0.0)
    assert events(driver) == ["wake", "publish"]


def test_healed_and_expired_unchanged(driver):
    driver.backend = FakeBackend({53553: "complete"})
    outstanding(driver, wakes=2)
    driver.on_wake("SPOT-33507C", 0.0)
    assert events(driver) == ["wake", "healed", "candidates"]
    assert driver.state["BMCAM_003"] is None

    driver.backend = FakeBackend({53553: "still_arriving"})
    outstanding(driver, wakes=D.HEAL_WAKES)                 # already published 3 wakes
    driver.on_wake("SPOT-33507C", 0.0)
    assert events(driver)[-3:] == ["wake", "expired", "candidates"]
