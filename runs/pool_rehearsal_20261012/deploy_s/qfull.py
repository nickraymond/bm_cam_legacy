import re, glob, datetime as d, collections, sys
days = sys.argv[1] if len(sys.argv) > 1 else "2026101[0-4]"
for spot in ("SPOT-31593C", "SPOT-33507C"):
    print("=====", spot)
    per = collections.OrderedDict()
    for f in sorted(glob.glob(f"/home/pi/spotter_logs/{spot}/console_{days}.log")):
        for line in open(f, errors="replace"):
            if "is full" not in line and "Submitted spotter/transmit-data" not in line:
                continue
            m = re.search(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z", line)
            if not m:
                continue
            t = d.datetime.fromisoformat(m.group(1)); hour = t.strftime("%m-%d %H")
            w = per.setdefault(hour, {"sub": 0, "full": 0, "runs": [], "cur": 0, "last": None})
            if "is full" in line:
                w["full"] += 1
                if w["last"] and (t - w["last"]).total_seconds() < 1.6:
                    w["cur"] += 1
                else:
                    if w["cur"]:
                        w["runs"].append(w["cur"])
                    w["cur"] = 1
                w["last"] = t
            else:
                w["sub"] += 1
    for h, w in per.items():
        if w["cur"]:
            w["runs"].append(w["cur"])
        if w["sub"] < 20:
            continue
        print(f"{h}Z submits={w['sub']:3d} full={w['full']:3d} runs={w['runs']}")
