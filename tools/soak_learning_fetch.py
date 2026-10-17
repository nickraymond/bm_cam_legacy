#!/usr/bin/env python3
# filename: soak_learning_fetch.py
# description: c_soak_learning (E4) — READ-ONLY fetch of backend + Sofar rows for the per-image soak ledger.
"""
Read-only fetcher for the soak-learning ledger (spec SPEC_c_soak_learning.md rev 2).

Purpose
  Pull, and save verbatim, every row the ledger needs for bench units bmcam003/004:
  backend (staging = BM production) admin GETs and Sofar `api/sensor-data` reads.
  Nothing here writes anywhere except the local run folder.

Read-only guarantees (spec "Read-only boundaries")
  - Backend: HTTP GET only. The client class has no other verb. Admin token is read
    from ~/.config/nereus/staging.env (ADMIN_TOKEN line only, the same access the E5
    colour session uses); it is never printed, logged or written to disk.
  - Sofar: GET api/sensor-data only, token from env SOFAR_API_TOKEN_BM_REEF (bench
    Spotters). The token never appears in saved files or logs (URLs are logged
    with the token redacted).
  - Hard refusal for SPOT-33361C / BMCAM_002 (NOAA field unit): never read.

Inputs
  --run-dir   run folder (default ~/NereusData/bm_cam_legacy/2026-10-16_soak_learning)
  --start / --end   UTC ISO window (default 2026-10-14T04:00Z .. now)
  subcommand: meta | backend | sofar

Outputs (under <run-dir>/raw/)
  backend/<name>.json   one file per GET (body as returned, plus a `_fetch` header)
  sofar/<SPOT>_<slice-start>.json  one file per time slice (rows as returned)
  ../logs/fetch.log     one line per request: time, url (token redacted), status, rows, bytes

Example
  python3 tools/soak_learning_fetch.py meta
  python3 tools/soak_learning_fetch.py backend --start 2026-10-14T04:00:00Z
  python3 tools/soak_learning_fetch.py sofar --start 2026-10-14T04:00:00Z --slice-h 3

Known limitations
  - Sofar may cap rows per request; a slice whose row count is a round cap
    (1000/2000/5000/10000) is re-fetched as two halves (fails loudly if depth > 6).
  - /devices/{id}/media pages newest-first by capture time; paging stops at the
    first page entirely older than --start.
"""

import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BACKEND = "https://nereus-vision-staging.onrender.com"
SOFAR = "https://api.sofarocean.com/api/sensor-data"
DEFAULT_RUN = Path.home() / "NereusData/bm_cam_legacy/2026-10-16_soak_learning"

# Bench rigs (hil.env, swapped 2026-10-13 ~1:50 PM PT; rig_map.json).
RIGS = {"BMCAM_003": "SPOT-31593C", "BMCAM_004": "SPOT-33507C"}
FORBIDDEN = ("SPOT-33361C", "BMCAM_002", "BMCAM_001")   # field units: never read
ROUND_CAPS = {500, 1000, 2000, 5000, 10000, 20000}


def _ssl():
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    return ctx


def _admin_token():
    p = Path.home() / ".config/nereus/staging.env"
    for line in p.read_text().splitlines():
        if line.startswith("ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("FAIL: no ADMIN_TOKEN line in ~/.config/nereus/staging.env (ask the PjM; never copy a token)")


def _guard(*parts):
    for p in parts:
        for f in FORBIDDEN:
            if f.lower() in str(p).lower():
                raise SystemExit(f"REFUSED: {f} is a field unit; this tool never reads it")


class Log:
    def __init__(self, run_dir):
        self.path = run_dir / "logs/fetch.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg):
        line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
        print(line, flush=True)
        with self.path.open("a") as fh:
            fh.write(line + "\n")


class BackendRO:
    """GET-only backend client. There is deliberately no post/put/patch/delete."""

    def __init__(self, log):
        self._tok = _admin_token()
        self._ctx = _ssl()
        self.log = log

    def get(self, path, params=None):
        _guard(path, json.dumps(params or {}))
        url = BACKEND + path + ("?" + urlencode(params) if params else "")
        req = Request(url, method="GET", headers={"Authorization": f"Bearer {self._tok}",
                                                  "Accept": "application/json"})
        for attempt in range(4):
            try:
                with urlopen(req, timeout=120, context=self._ctx) as r:
                    body = r.read()
                    self.log(f"GET {url} -> {r.status} {len(body)} B")
                    return json.loads(body)
            except HTTPError as e:
                self.log(f"GET {url} -> HTTP {e.code} {e.read()[:300]!r}")
                if e.code in (502, 503, 504) and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise SystemExit(f"FAIL: GET {url} kept failing")


def _save(run_dir, sub, name, obj, meta):
    p = run_dir / "raw" / sub / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"_fetch": meta, "body": obj}, indent=1))
    return p


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def cmd_meta(a, log):
    """Settings + config periods only (no media, no chunk counts): safe before EXPECTATIONS is hashed."""
    be = BackendRO(log)
    now = _iso(datetime.now(timezone.utc))
    for dev, spot in RIGS.items():
        meta = {"at": now, "dev": dev, "spot": spot}
        _save(a.run_dir, "backend", f"gateway_{spot}", be.get(f"/admin/ingest/gateways/{spot}"), meta)
        _save(a.run_dir, "backend", f"rollout_{spot}", be.get(f"/admin/gateways/{spot}/rollout"), meta)
        _save(a.run_dir, "backend", f"config_history_{dev}",
              be.get(f"/devices/{dev}/config-history", {"start": a.start, "end": a.end}), meta)
        _save(a.run_dir, "backend", f"commands_{dev}", be.get(f"/admin/devices/{dev}/commands"), meta)
        _save(a.run_dir, "backend", f"device_{dev}", be.get(f"/devices/{dev}"), meta)
    log("[meta] done")


def _windows(start, end, hours):
    t = _parse(start)
    e = _parse(end)
    while t < e:
        u = min(t + timedelta(hours=hours), e)
        yield t, u
        t = u


def cmd_backend(a, log):
    be = BackendRO(log)
    now = _iso(datetime.now(timezone.utc))
    for dev, spot in RIGS.items():
        meta = {"at": now, "dev": dev, "spot": spot, "start": a.start, "end": a.end}
        # Media list (capture order, newest first), all pages back to --start.
        rows, page = [], 1
        while True:
            batch = be.get(f"/devices/{dev}/media", {"page": page, "page_size": 72, "include_hidden": "true"})
            items = batch if isinstance(batch, list) else batch.get("items", batch.get("media", []))
            if not items:
                break
            rows += items
            oldest = min((m.get("captured_at_utc") or m.get("timestamp_utc") or "9") for m in items)
            if oldest < a.start:
                break
            page += 1
        _save(a.run_dir, "backend", f"media_{dev}", rows, {**meta, "pages": page})
        # Per-media detail (cycle log + telemetry) for rows in the window.
        detail = {}
        for m in rows:
            cap = m.get("captured_at_utc") or m.get("timestamp_utc") or ""
            if cap >= a.start:
                mid = m.get("id") or m.get("media_id")
                detail[str(mid)] = be.get(f"/media/{mid}/debug")
        _save(a.run_dir, "backend", f"media_debug_{dev}", detail, meta)
        # Heal events (rsd asks + <HL> answers), cycle logs, heartbeats: 24 h slices.
        for kind, path, extra in (
                ("heal_events", f"/systems/{spot}/heal-events", {"limit": 2000, "sort": "asc", "device_id": dev}),
                ("heartbeats", f"/systems/{spot}/heartbeats", {"device_id": dev}),
        ):
            out = []
            for s, e in _windows(a.start, a.end, 24):
                body = be.get(path, {**extra, "start": _iso(s), "end": _iso(e)})
                out.append({"start": _iso(s), "end": _iso(e), "body": body})
            _save(a.run_dir, "backend", f"{kind}_{dev}", out, meta)
        out = []
        for s, e in _windows(a.start, a.end, 24):
            cursor = None
            while True:
                params = {"device_id": dev, "start": _iso(s), "end": _iso(e), "limit": 100, "sort": "asc"}
                if cursor:
                    params["cursor"] = cursor
                body = be.get(f"/systems/{spot}/cycle-logs", params)
                out.append({"start": _iso(s), "end": _iso(e), "cursor": cursor, "body": body})
                cursor = body.get("next_cursor") if isinstance(body, dict) else None
                if not cursor:
                    break
        _save(a.run_dir, "backend", f"cycle_logs_{dev}", out, meta)
        _save(a.run_dir, "backend", f"command_events_{spot}",
              be.get(f"/systems/{spot}/command-events", {"start": a.start, "end": a.end}), meta)
    log("[backend] done")


def _sofar_slice(spot, s, e, ctx, log, depth=0):
    tok = os.environ.get("SOFAR_API_TOKEN_BM_REEF")
    if not tok:
        raise SystemExit("FAIL: SOFAR_API_TOKEN_BM_REEF not set")
    q = {"spotterId": spot, "startDate": _iso(s), "endDate": _iso(e)}
    url = SOFAR + "?" + urlencode({**q, "token": tok})
    shown = SOFAR + "?" + urlencode({**q, "token": "REDACTED"})
    for attempt in range(4):
        try:
            with urlopen(url, timeout=120, context=ctx) as r:
                rows = json.load(r).get("data", [])
            break
        except Exception as exc:  # noqa: BLE001 - retried, then fail loudly
            log(f"GET {shown} -> ERROR {exc!r} (attempt {attempt + 1})")
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    log(f"GET {shown} -> {len(rows)} rows")
    if len(rows) in ROUND_CAPS:
        if depth > 6:
            raise SystemExit(f"FAIL: {spot} {_iso(s)} still at a round cap after 6 splits")
        mid = s + (e - s) / 2
        return (_sofar_slice(spot, s, mid, ctx, log, depth + 1)
                + _sofar_slice(spot, mid, e, ctx, log, depth + 1))
    return rows


def cmd_sofar(a, log):
    ctx = _ssl()
    for dev, spot in RIGS.items():
        _guard(spot)
        total = 0
        for s, e in _windows(a.start, a.end, a.slice_h):
            rows = _sofar_slice(spot, s, e, ctx, log)
            total += len(rows)
            _save(a.run_dir, "sofar", f"{spot}_{s.strftime('%Y%m%dT%H%M%SZ')}", rows,
                  {"spot": spot, "dev": dev, "start": _iso(s), "end": _iso(e), "rows": len(rows)})
        log(f"[sofar] {spot}: {total} rows {a.start}..{a.end}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY fetch for the soak-learning ledger")
    ap.add_argument("cmd", choices=("meta", "backend", "sofar"))
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    ap.add_argument("--start", default="2026-10-14T04:00:00Z")
    ap.add_argument("--end", default=None)
    ap.add_argument("--slice-h", type=float, default=3.0)
    a = ap.parse_args(argv)
    a.end = a.end or _iso(datetime.now(timezone.utc))
    a.run_dir = a.run_dir.expanduser()
    log = Log(a.run_dir)
    log(f"[{a.cmd}] start run_dir={a.run_dir} window={a.start}..{a.end}")
    {"meta": cmd_meta, "backend": cmd_backend, "sofar": cmd_sofar}[a.cmd](a, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
