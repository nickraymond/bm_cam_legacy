"""Scratch: dump Sofar sensor-data for one Spotter in 4 h slices to JSON (scratch only: hex = bench footage)."""
import json, os, sys
sys.path.insert(0, "tools")
import bm_video_soak_report as r
spot, start, end, out = sys.argv[1:5]
rows = r.fetch_sofar_sliced(spot, start, end, os.environ["SOFAR_API_TOKEN_BM_REEF"], 4)
json.dump(rows, open(out, "w"))
print(spot, len(rows), "rows ->", out)
