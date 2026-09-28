"""S0 gate option B: replay real Sofar traffic through the old and new parser.

Purpose: prove M0 (nereus-vision-dev PR #49) changes nothing for rev 3 traffic.
Inputs:  Sofar sensor-data (read-only GET) for every gateway in external_gateways,
         last DAYS days, fetched in 6 h windows (the production poll window);
         parser_old.py = staging @ dbcb812 (before #49), parser_new.py = @ 563022f.
Outputs: raw/<spotter>_<start>.json, replay_results.json, printed table.
Compare: per window AND whole-span-per-spotter (groups crossing a window edge),
         summary dict + every parsed media (all fields; image bytes compared by sha256).
Also counts chunk-marker forms in the real traffic: legacy <I{n}>, keyed <I{6}.{n}>,
other <I{x}.{n}>.
Run: backend python from the nereus-vision-dev worktree root (needs backend.app on sys.path).
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.util, json, os, re, sys, time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.getcwd())
from backend.app.services.sofar_client import fetch_sofar_api, iso_z  # noqa: E402

DAYS = 7
GATEWAYS = [("SPOT-31593C", "SOFAR_API_TOKEN_BM_REEF"),
            ("SPOT-33361C", "SOFAR_API_TOKEN_AOML"),
            ("SPOT-33507C", "SOFAR_API_TOKEN_BM_REEF")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod); return mod


OLD, NEW = load("parser_old", HERE / "parser_old.py"), load("parser_new", HERE / "parser_new.py")
LEGACY = re.compile(r"<I\d+>"); KEYED = re.compile(r"<I[0-9a-z]{6}\.\d+>"); OTHER = re.compile(r"<I[^>\d][^>]*\.\d+>")


def norm(summary, by_node):
    media = []
    for node, imgs in sorted(by_node.items()):
        for im in imgs:
            d = asdict(im); d.pop("image_bytes", None); media.append(d)
    return json.loads(json.dumps({"summary": summary, "media": media}, default=str, sort_keys=True))


def run(mod, payload):
    s, b = mod.parse_sensor_data_images(payload, include_image_bytes=True)
    return norm(s, b)


def fetch(spotter, env, start, end):
    f = HERE / "raw" / f"{spotter}_{start:%Y%m%dT%H%MZ}.json"
    if f.exists():
        return json.loads(f.read_text())
    r = fetch_sofar_api(api_class="sensor-data", spotter_id=spotter, token=os.environ[env],
                        start_date=iso_z(start), end_date=iso_z(end))
    f.write_text(json.dumps(r.payload)); time.sleep(0.5)
    return r.payload


end_all = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start_all = end_all - timedelta(days=DAYS)
print(f"replay window {iso_z(start_all)} .. {iso_z(end_all)}, 6 h windows, {len(GATEWAYS)} spotters", flush=True)
results, fails = [], 0
for spotter, env in GATEWAYS:
    rows_all, t, win = [], start_all, 0
    markers = {"legacy": 0, "keyed": 0, "other_dotted": 0}
    while t < end_all:
        payload = fetch(spotter, env, t, t + timedelta(hours=6))
        data = payload.get("data") or []
        rows_all.extend(data)
        for row in data:
            try:
                txt = bytes.fromhex(str(row.get("value", ""))).decode("utf-8", "replace")
            except ValueError:
                continue
            markers["legacy"] += bool(LEGACY.search(txt)); markers["keyed"] += bool(KEYED.search(txt))
            markers["other_dotted"] += bool(OTHER.search(txt))
        o, n = run(OLD, payload), run(NEW, payload)
        if o != n:
            fails += 1; print(f"  DIFF {spotter} window {iso_z(t)}", flush=True)
        win += 1; t += timedelta(hours=6)
    whole = {"status": "success", "spotterId": spotter, "data": rows_all}
    o, n = run(OLD, whole), run(NEW, whole)
    same = o == n; fails += (not same)
    rec = {"spotter": spotter, "windows": win, "rows": len(rows_all), "markers": markers,
           "media": len(n["media"]), "complete": sum(m["is_complete"] for m in n["media"]),
           "whole_span_identical": same}
    results.append(rec)
    print(f"  {spotter}: {win} windows, {len(rows_all)} rows, media {rec['media']} "
          f"(complete {rec['complete']}), markers {markers}, whole-span identical={same}", flush=True)
(HERE / "replay_results.json").write_text(json.dumps(
    {"window": [iso_z(start_all), iso_z(end_all)], "old": "dbcb812", "new": "563022f",
     "diff_count": fails, "spotters": results}, indent=1))
print("REPLAY:", "PASS (old == new everywhere)" if fails == 0 else f"FAIL ({fails} diffs)")
sys.exit(1 if fails else 0)
