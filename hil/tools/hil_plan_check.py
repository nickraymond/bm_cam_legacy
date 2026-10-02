#!/usr/bin/env python3
"""hil_plan_check.py — score the backend's /remote-config/plan against the G3 case table.

Purpose
  G3 criterion "every bad value is refused BEFORE send": run each case from
  hil_hardmode_cases.py through POST /devices/{id}/remote-config/plan (which writes nothing,
  SPEC r2 §2.3) and compare refused / accepted with the expectation. This proves the deployed
  staging backend + its vendored catalog refuse what the catalog says is bad, end to end,
  with zero cellular and zero unit risk.

Safety
  This script only ever calls GET /remote-config/catalog, GET /devices/{id}/remote-config and
  POST .../remote-config/plan. It has no code path to /changes or /send (grep it).

Runs ON the monitor host (nereus000): the admin token never leaves it (read from --env-file,
ADMIN_TOKEN=..., never printed or written).

Inputs
  --cases cases.json   from hil_hardmode_cases.py
  --device BMCAM_003   backend device id
  --api URL            staging base (default env HIL_API)
  --env-file PATH      default /home/pi/.config/nereus/heal_driver.env
  --out DIR            results dir (default .)
  --sleep S            pause between calls (default 0.25 s; staging is shared)
  --limit N            first N cases only (smoke)
Outputs (in --out)
  plan_check.csv       id, key, body, expect, expect_reason, http, got (accept|refuse|error),
                       reasons, warnings, json_bytes, verdict (PASS|FAIL|INFO), reason_match
  plan_check.json      summary: counts per verdict, catalog sha match, sanity case, FAIL list
  api/                 the device view + catalog head as fetched at the start
Exit
  0 = every scored case PASS; 1 = any FAIL; 2 = precondition failed (catalog sha mismatch,
  sanity accept case refused, device not writable) — nothing was scored.

Example (on nereus000)
  python3 hil_plan_check.py --cases cases.json --device BMCAM_003 \
      --api https://nereus-vision-staging.onrender.com --out g3_plan_bmcam003

Limitations
  - `depends` cases are recorded as INFO, never PASS/FAIL (they need reported state).
  - A FAIL is a disagreement between the backend and the case model; review it (spec,
    backend, or the model in hil_hardmode_cases.py) before calling it a backend bug.
  - Uses "supersede": true so a queued admin row on the device does not mask every case
    (plan writes nothing, so this has no side effect).
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

SANITY = {"set": {"camera.exposure.ev": 0.0}}   # a control key, in range, no cross-key rule


def read_token(env_file):
    with open(env_file, "r", encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.strip().partition("=")
            if key == "ADMIN_TOKEN" and value:
                return value.strip().strip("'\"")
    raise SystemExit(f"ADMIN_TOKEN not found in {env_file}")


class Api:
    ALLOWED = ("/remote-config/catalog", "/remote-config", "/remote-config/plan")

    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method, path, body=None):
        if not path.endswith(self.ALLOWED):
            raise SystemExit(f"[plan_check] refusing path {path}: plan/read only")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            t = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(t)
            except ValueError:
                return e.code, t
        except Exception as e:  # network
            return 0, f"{type(e).__name__}: {e}"


def classify(status, resp):
    """(got, reasons[], warnings[], json_bytes)."""
    if status == 200 and isinstance(resp, dict):
        warns = [w.get("key", "") + ":" + str(w.get("why", ""))[:60] for w in resp.get("warnings") or []]
        jb = resp.get("json_bytes", (resp.get("command") or {}).get("json_bytes"))
        if resp.get("refusals") or resp.get("ok") is False:
            return "refuse", [r.get("reason", "") for r in resp.get("refusals") or []] or ["ok=false"], warns, jb
        return "accept", [], warns, jb
    if status == 422 and isinstance(resp, dict):
        d = resp.get("detail", resp)
        if isinstance(d, dict):
            rs = [r.get("reason", "") for r in d.get("refusals") or []] or [d.get("reason", "")]
        else:
            rs = [str(d)[:80]]
        return "refuse", rs, [], None
    return "error", [f"http {status}: {str(resp)[:120]}"], [], None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--api", default=os.environ.get("HIL_API", ""))
    ap.add_argument("--env-file", default="/home/pi/.config/nereus/heal_driver.env")
    ap.add_argument("--out", default=".")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if not a.api:
        raise SystemExit("--api or HIL_API required")
    os.makedirs(os.path.join(a.out, "api"), exist_ok=True)
    doc = json.load(open(a.cases))
    cases = doc["cases"][: a.limit or None]
    api = Api(a.api, read_token(a.env_file))
    dev = f"/devices/{a.device}/remote-config"
    summary = {"device": a.device, "api": a.api, "cases_file": a.cases, "n_cases": len(cases),
               "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # preconditions: same catalog, device writable, a known-good case accepted
    st, cat = api.call("GET", "/remote-config/catalog")
    json.dump({"status": st, "sha256": (cat or {}).get("sha256") if isinstance(cat, dict) else None,
               "registry_version": (cat or {}).get("registry_version") if isinstance(cat, dict) else None},
              open(os.path.join(a.out, "api", "catalog_head.json"), "w"), indent=1)
    served = cat.get("sha256") if isinstance(cat, dict) else None
    summary["catalog_sha256_cases"] = doc.get("catalog_sha256")
    summary["catalog_sha256_served"] = served
    st_v, view = api.call("GET", dev)
    json.dump({"status": st_v, "body": view}, open(os.path.join(a.out, "api", "device_view_start.json"), "w"), indent=1)
    st_s, san = api.call("POST", dev + "/plan", dict(SANITY, supersede=True))
    summary["sanity"] = {"status": st_s, "body": san}
    pre = []
    if served != doc.get("catalog_sha256"):
        pre.append(f"catalog sha mismatch: cases {str(doc.get('catalog_sha256'))[:12]} vs served {str(served)[:12]}")
    if classify(st_s, san)[0] != "accept":
        pre.append(f"sanity case {SANITY} not accepted (http {st_s}): device not writable / flag off?")
    if pre:
        summary["precondition_failures"] = pre
        json.dump(summary, open(os.path.join(a.out, "plan_check.json"), "w"), indent=1)
        for p in pre:
            print("[plan_check] PRECONDITION FAIL:", p)
        sys.exit(2)

    counts = {"PASS": 0, "FAIL": 0, "INFO": 0}
    fails = []
    with open(os.path.join(a.out, "plan_check.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "key", "body", "expect", "expect_reason", "http", "got", "reasons", "warnings",
                    "json_bytes", "verdict", "reason_match"])
        for i, c in enumerate(cases, 1):
            body = dict(c["body"], supersede=True)
            st, resp = api.call("POST", dev + "/plan", body)
            got, reasons, warns, jb = classify(st, resp)
            if c["expect"] == "depends" or got == "error":
                v = "INFO" if got != "error" else "FAIL"
            else:
                v = "PASS" if got == c["expect"] else "FAIL"
            rm = "" if c["expect"] != "refuse" or got != "refuse" else ("yes" if c["expect_reason"] in reasons else "no")
            counts[v] += 1
            if v == "FAIL":
                fails.append({"id": c["id"], "key": c["key"], "body": c["body"], "expect": c["expect"],
                              "got": got, "reasons": reasons, "http": st})
            w.writerow([c["id"], c["key"], json.dumps(c["body"]), c["expect"], c["expect_reason"], st, got,
                        "|".join(reasons), "|".join(warns), jb, v, rm])
            if i % 50 == 0:
                print(f"[plan_check] {i}/{len(cases)} {counts}", flush=True)
            time.sleep(a.sleep)
    summary.update({"finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "counts": counts,
                    "fails": fails})
    json.dump(summary, open(os.path.join(a.out, "plan_check.json"), "w"), indent=1)
    print(f"[plan_check] done {counts}; {len(fails)} FAIL -> {a.out}/plan_check.csv")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
